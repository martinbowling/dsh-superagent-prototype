#!/usr/bin/env python3
"""A loopback proxy to a disposable networkless app container; no model code runs on the host."""
import argparse,base64,hashlib,html,json,os,re,secrets,shlex,signal,subprocess,sys,threading,time,urllib.parse,urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from app_checks import FILES

ROUTE=re.compile(r'/(?:health|api/tasks(?:/[1-9][0-9]*)?|tasks(?:/[1-9][0-9]*/(?:toggle|delete))?|static/style\.css)?')
CSP="default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'none'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"

def verify_project(run,config,files):
    manifest=json.loads((run/'manifest.json').read_text())
    if manifest.get('fixture_suite')!='app-v1':raise ValueError('Preview requires an app-v1 run')
    grader=run/'grader'
    names=manifest.get('grader_files',[])
    if names!=['check.py','checks_v2.py','app_checks.py']:raise ValueError('Missing frozen app grader')
    digest=hashlib.sha256(b''.join((grader/name).read_bytes() for name in names)).hexdigest()
    if digest!=manifest.get('checker_sha256'):raise ValueError('Frozen grader hash changed')
    env=os.environ.copy();env.update(ARENA_DOCKER_SSH=config.get('docker_ssh',''),ARENA_PYTHON_IMAGE=config['python_image'])
    result=subprocess.run([sys.executable,str(grader/'check.py'),'--suite','app-v1','--task','taskboard'],input=json.dumps({'files':files}),text=True,capture_output=True,timeout=40,env=env)
    checked=json.loads(result.stdout)
    if result.returncode or not checked.get('passed'):raise ValueError('Preview requires an independent passing app check')
    return {'passed':True,'passed_cases':checked['passed_cases'],'total_cases':checked['total_cases'],'checker_sha256':digest,
      'project_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()}

def local_path(value):
    u=urllib.parse.urlsplit(value)
    return not u.scheme and not u.netloc and not value.startswith('//') and bool(ROUTE.fullmatch(u.path)) and len(u.query)<=1024

class SafeHTML(HTMLParser):
    tags=set('main header footer section article div span h1 h2 h3 p ul ol li form label input button a table thead tbody tr th td strong em small select option br hr nav aside'.split())
    attrs=set('class id role type name placeholder value checked selected for disabled maxlength required aria-label aria-live'.split())
    suppress={'head','script','style','iframe','object','embed','svg','math'}
    def __init__(self):super().__init__(convert_charrefs=True);self.out=[];self.skipped=[]
    def handle_starttag(self,tag,attrs):
        if self.skipped:
            if tag in self.suppress:self.skipped.append(tag)
            return
        if tag in self.suppress:self.skipped.append(tag);return
        if tag not in self.tags:return
        clean=[]
        for key,value in attrs:
            if key=='type' and tag=='input' and value not in ('text','search','hidden','checkbox','submit','button'):continue
            if key in self.attrs:clean.append((key,value))
            elif key in ('href','action') and value and (value.startswith('#') or local_path(value)):clean.append((key,value))
            elif key=='method' and value and value.lower() in ('get','post'):clean.append((key,value))
        self.out.append('<'+tag+''.join(' '+key if value is None else ' '+key+'="'+html.escape(value,quote=True)+'"' for key,value in clean)+'>')
    def handle_endtag(self,tag):
        if self.skipped:
            if tag==self.skipped[-1]:self.skipped.pop()
            return
        if tag in self.tags and tag not in ('input','br','hr'):self.out.append('</'+tag+'>')
    def handle_data(self,data):
        if not self.skipped:self.out.append(html.escape(data))

def safe_html(source):
    parser=SafeHTML();parser.feed(source)
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Taskboard preview</title><link rel="stylesheet" href="/static/style.css"></head><body>'+''.join(parser.out)+'</body></html>'

RELAY=r'''
import base64,json,urllib.request,urllib.error,sys
p=json.load(sys.stdin);headers={'Content-Type':p.get('content_type','application/json')}
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
req=urllib.request.Request('http://127.0.0.1:8765'+p['path'],data=base64.b64decode(p['body']) if p['body'] else None,headers=headers,method=p['method'])
try:r=op.open(req,timeout=3)
except urllib.error.HTTPError as e:r=e
with r:print(json.dumps({'status':r.status,'location':r.headers.get('Location'),'body':base64.b64encode(r.read(65536)).decode()}))
'''
START=r'''
import json,os,pathlib,subprocess,sys,time,urllib.request
p=json.load(sys.stdin);root=pathlib.Path('/tmp/app');root.mkdir()
for name,text in p.items():
 file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
env=os.environ.copy();env.update(APP_HOST='127.0.0.1',APP_PORT='8765',APP_DB='/tmp/preview.sqlite')
log=open('/tmp/app.log','wb');child=subprocess.Popen([sys.executable,'-S','app.py'],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+8
while time.monotonic()<end:
 if child.poll() is not None:raise RuntimeError('app exited: '+pathlib.Path('/tmp/app.log').read_text()[-1000:])
 try:
  with op.open('http://127.0.0.1:8765/health',timeout=1) as r:
   if r.status==200:print(json.dumps({'pid':child.pid}));break
 except Exception:pass
 time.sleep(.1)
else:raise RuntimeError('app did not become ready')
'''

def docker(config,args,payload=None,timeout=15):
    host=config.get('docker_ssh','')
    if host and not re.fullmatch(r'[A-Za-z0-9_.@-]+',host):raise ValueError('Invalid Docker SSH alias')
    argv=['docker',*args]
    if host:argv=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',host,shlex.join(argv)]
    result=subprocess.run(argv,input=payload,text=True,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError(result.stderr[-1600:])
    return result.stdout

def serve(run,config,state,port,replica=0):
    paths=sorted(p for p in (run/'state/work').iterdir() if p.is_dir() and all((p/name).is_file() and not (p/name).is_symlink() for name in FILES))
    directory=paths[replica];files={name:(directory/name).read_text() for name in FILES}
    if any(len(v.encode())>64000 for v in files.values()):raise ValueError('Project file too large')
    verification=verify_project(run,config,files)
    image=config.get('python_image','python:3.12-alpine')
    if image.startswith('-'):raise ValueError('Invalid image')
    name='dshpreview-'+secrets.token_hex(16);nonce=secrets.token_urlsafe(24);url=f'http://127.0.0.1:{port}';receipt={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def handle_request(self):
            origin=self.headers.get('Origin')
            if origin and origin!=url:self.send_error(403);return
            if self.path=='/__preview_stop' and self.command=='POST':
                if not secrets.compare_digest(self.headers.get('X-Preview-Key',''),nonce):self.send_error(403);return
                self.send_response(200);self.end_headers();self.wfile.write(b'stopping')
                threading.Thread(target=server.shutdown,daemon=True).start();return
            if not local_path(self.path):self.send_error(404);return
            try:size=int(self.headers.get('Content-Length','0'))
            except ValueError:self.send_error(400);return
            if not 0<=size<=8192:self.send_error(413);return
            body=self.rfile.read(size) if size else b''
            payload={'method':self.command,'path':self.path,'body':base64.b64encode(body).decode(),'content_type':self.headers.get('Content-Type','application/json')}
            try:
                result=json.loads(docker(config,['exec','-i',name,'python','-I','-S','-c',RELAY],json.dumps(payload)))
                content=base64.b64decode(result['body']);path=urllib.parse.urlsplit(self.path).path
                ctype='application/json'
                if path=='/':content=safe_html(content.decode(errors='replace')).encode();ctype='text/html; charset=utf-8'
                elif path=='/static/style.css':ctype='text/css; charset=utf-8'
                self.send_response(result['status']);self.send_header('Content-Type',ctype)
                self.send_header('Content-Security-Policy',CSP);self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
                if result.get('location') and local_path(result['location']):self.send_header('Location',result['location'])
                self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.send_error(503,'Preview unavailable')
        do_GET=do_POST=do_PATCH=do_DELETE=handle_request
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True
    serving=False
    def shutdown(*args):
        if serving:threading.Thread(target=server.shutdown,daemon=True).start()
        else:raise KeyboardInterrupt('Preview startup cancelled')
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    try:
        docker(config,['run','-d','--rm','--pull=never','--name',name,'--label','dsh-arena-preview='+name,'--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=32','--memory=256m','--memory-swap=256m','--cpus=1','--user=65534:65534','--tmpfs=/tmp:rw,noexec,nosuid,size=16m',image,'python','-I','-S','-c','import time;time.sleep(3600)'])
        docker(config,['exec','-i',name,'python','-I','-S','-c',START],json.dumps(files))
        receipt={'url':url,'container':name,'key':nonce,'workspace':str(directory),'status':'running','started':time.time(),'pid':os.getpid(),'ttl_seconds':3600,'verification':verification}
        state.write_text(json.dumps(receipt,indent=2)+'\n');state.chmod(0o600)
        print(json.dumps({'url':url,'state':str(state),'scope':'interactive loopback proxy; app executes in networkless Docker'}),flush=True)
        serving=True
        server.serve_forever()
    finally:
        server.server_close()
        try:
            label=docker(config,['inspect',name,'--format','{{index .Config.Labels "dsh-arena-preview"}}']).strip()
            if label==name:docker(config,['rm','-f',name])
        except Exception:pass
        if receipt:receipt.update(status='stopped',ended=time.time());state.write_text(json.dumps(receipt,indent=2)+'\n')

def stop(state):
    data=json.loads(state.read_text());url=data['url']
    parsed=urllib.parse.urlsplit(url)
    if parsed.hostname!='127.0.0.1' or parsed.scheme!='http':raise ValueError('Not a local preview URL')
    request=urllib.request.Request(url+'/__preview_stop',data=b'',headers={'X-Preview-Key':data['key']},method='POST')
    with urllib.request.urlopen(request,timeout=5) as response:print(response.read().decode())

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['serve','stop']);p.add_argument('--state',type=Path,required=True);p.add_argument('--run',type=Path);p.add_argument('--config',type=Path);p.add_argument('--port',type=int,default=19020);p.add_argument('--replica',type=int,default=0);a=p.parse_args()
    if a.action=='stop':stop(a.state)
    else:
        if not a.run or not a.config:p.error('serve requires --run and --config')
        serve(a.run.resolve(),json.loads(a.config.read_text()),a.state.resolve(),a.port,a.replica)
