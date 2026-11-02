"""Trusted, container-only build and HTTP grading for the four-file Taskboard."""
import json

FILES=('app.py','store.py','templates/index.html','static/style.css')

def program(files):
    if not isinstance(files,dict) or set(files)!=set(FILES):raise ValueError('Provide exactly the four writable app files')
    if any(not isinstance(v,str) or len(v.encode())>64000 for v in files.values()):raise ValueError('App files must be text of at most 64000 bytes each')
    return 'FILES='+repr(files)+'\n'+BOOTSTRAP

BOOTSTRAP=r'''
import ast,concurrent.futures,html,json,os,pathlib,py_compile,sqlite3,subprocess,sys,time,urllib.request,urllib.error,urllib.parse
from html.parser import HTMLParser
ROOT=pathlib.Path('/tmp/project');ROOT.mkdir();rows=[];server=None;log=None;store=None;preview=''

def eq(a,b):assert a==b,f'{a!r} != {b!r}'
def rejects(fn):
    try:fn()
    except ValueError:return
    raise AssertionError('expected ValueError')
def check(name,fn):
    try:fn();rows.append({'name':name,'passed':True})
    except BaseException as e:rows.append({'name':name,'passed':False,'error':str(e)[:240]})
for path,content in FILES.items():
    p=ROOT/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
def build():
    for p in ['app.py','store.py']:py_compile.compile(str(ROOT/p),doraise=True)
    for p in FILES:assert FILES[p].strip(),p+' is empty'
    tree=ast.parse(FILES['app.py'])
    assert any(isinstance(n,ast.Import) and any(a.name=='store' for a in n.names) or isinstance(n,ast.ImportFrom) and n.module=='store' for n in ast.walk(tree)), 'app.py must import store'
check('build four-file application',build)
DB='/tmp/taskboard.sqlite';BASE='http://127.0.0.1:8765'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPHandler())
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
plain=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def request(method,path,payload=None,raw=None,ctype='application/json'):
    data=raw if raw is not None else None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=data,method=method,headers={'Content-Type':ctype})
    try:r=plain.open(req,timeout=2)
    except urllib.error.HTTPError as e:r=e
    with r:return r.status,dict((k.lower(),v) for k,v in r.headers.items()),r.read(65536)
def api(method,path,payload=None,expected=200,**kw):
    status,headers,body=request(method,path,payload,**kw);eq(status,expected)
    if expected==204:eq(body,b'');return None
    assert 'application/json' in headers.get('content-type',''),'expected JSON response'
    value=json.loads(body)
    if expected>=400:assert isinstance(value,dict) and isinstance(value.get('error'),str)
    return value

def start():
    global server,log
    env=os.environ.copy();env.update(APP_HOST='127.0.0.1',APP_PORT='8765',APP_DB=DB)
    log=open('/tmp/server.log','ab')
    server=subprocess.Popen([sys.executable,'-S','app.py'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    end=time.monotonic()+5
    while time.monotonic()<end:
        if server.poll() is not None:raise AssertionError('server exited: '+pathlib.Path('/tmp/server.log').read_text()[-800:])
        try:
            if api('GET','/health').get('status')=='ok':return
        except Exception:pass
        time.sleep(.05)
    raise AssertionError('server did not become ready')
def stop():
    global server,log
    if server and server.poll() is None:
        server.terminate()
        try:server.wait(timeout=2)
        except subprocess.TimeoutExpired:server.kill();server.wait()
    if log:log.close()
    server=None;log=None

try:
    check('start server and health',start)
    if server is None or server.poll() is not None:raise RuntimeError('Server unavailable; remaining integration checks cannot run')
    check('empty task list',lambda:eq(api('GET','/api/tasks'),{'tasks':[]}))
    one=api('POST','/api/tasks',{'title':'  First task  '},201)['task']
    check('create trimmed typed task',lambda:(eq(one['title'],'First task'),eq(one['done'],False),eq(type(one['done']),bool),eq(type(one['id']),int),eq(set(one),{'id','title','done'})))
    id1=one['id'];two=api('POST','/api/tasks',{'title':'CAFÉ notes'},201)['task'];id2=two['id']
    check('monotonic ids',lambda:(_ for _ in ()).throw(AssertionError('ids must increase')) if id2<=id1 else None)
    check('list ordered ids',lambda:eq([x['id'] for x in api('GET','/api/tasks')['tasks']],[id1,id2]))
    check('mark complete',lambda:eq(api('PATCH',f'/api/tasks/{id1}',{'done':True})['task']['done'],True))
    check('filter open',lambda:eq([x['id'] for x in api('GET','/api/tasks?status=open')['tasks']],[id2]))
    check('filter done',lambda:eq([x['id'] for x in api('GET','/api/tasks?status=done')['tasks']],[id1]))
    check('Unicode casefold search',lambda:eq([x['id'] for x in api('GET','/api/tasks?q='+urllib.parse.quote('café'))['tasks']],[id2]))
    percent=api('POST','/api/tasks',{'title':'100% done'},201)['task']
    check('literal wildcard query',lambda:eq([x['id'] for x in api('GET','/api/tasks?q=%25')['tasks']],[percent['id']]))
    for label,value in [('empty',''),('spaces','  '),('long','x'*121),('null',None),('bool',True),('number',4)]:
        check('reject title '+label,lambda value=value:api('POST','/api/tasks',{'title':value},400))
    check('reject extra create fields',lambda:api('POST','/api/tasks',{'title':'x','done':True},400))
    check('reject malformed JSON',lambda:api('POST','/api/tasks',expected=400,raw=b'{bad'))
    check('reject JSON array',lambda:api('POST','/api/tasks',[],400))
    check('reject wrong media type',lambda:api('POST','/api/tasks',{'title':'x'},415,ctype='text/plain'))
    check('reject oversized body',lambda:api('POST','/api/tasks',expected=413,raw=b'x'*8193))
    for label,value in [('integer',1),('null',None),('string','true'),('list',[])]:
        check('reject done '+label,lambda value=value:api('PATCH',f'/api/tasks/{id1}',{'done':value},400))
    check('reject extra patch fields',lambda:api('PATCH',f'/api/tasks/{id1}',{'done':False,'title':'x'},400))
    check('unknown patch',lambda:api('PATCH','/api/tasks/999999',{'done':False},404))
    check('unknown delete',lambda:api('DELETE','/api/tasks/999999',expected=404))
    check('unknown route',lambda:api('GET','/not-a-route',expected=404))
    check('unsupported method',lambda:api('PUT','/api/tasks',{'title':'x'},405))
    check('invalid filter',lambda:api('GET','/api/tasks?status=invalid',expected=400))
    check('block traversal',lambda:eq(request('GET','/static/../../app.py')[0],404))
    injection='<script>alert(1)</script>'
    escaped=api('POST','/api/tasks',{'title':injection},201)['task']
    def html_ui():
        global preview
        status,headers,body=request('GET','/');eq(status,200);text=body.decode()
        assert 'text/html' in headers.get('content-type','')
        class Tags(HTMLParser):
            def __init__(self):super().__init__();self.tags=[]
            def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))
        tags=Tags();tags.feed(text)
        assert any(t=='main' for t,a in tags.tags),'main element required'
        assert any(t=='form' and a.get('action')=='/tasks' and a.get('method','').lower()=='post' for t,a in tags.tags),'create form required'
        assert any(t=='input' and a.get('name')=='title' for t,a in tags.tags),'title input required'
        assert any(t=='label' for t,a in tags.tags),'label required'
        assert any(t=='meta' and a.get('name')=='viewport' for t,a in tags.tags),'viewport required'
        assert any(t=='link' and a.get('href')=='/static/style.css' for t,a in tags.tags),'stylesheet link required'
        assert 'Taskboard' in text
        assert injection not in text and html.escape(injection) in text,'task title must be HTML escaped'
        assert '<script' not in text.lower(),'no JavaScript required for this server-rendered app'
        preview=text[:30000]
    check('render accessible escaped UI',html_ui)
    check('serve actual stylesheet',lambda:(eq(request('GET','/static/style.css')[0],200),eq(request('GET','/static/style.css')[2].decode(),FILES['static/style.css'])))
    check('responsive keyboard styles',lambda:(_ for _ in ()).throw(AssertionError('include media and focus styles')) if '@media' not in FILES['static/style.css'] or ':focus' not in FILES['static/style.css'] else None)
    def template_loaded():
        p=ROOT/'templates/index.html';original=p.read_text();p.write_text(original+'\n<!-- TEMPLATE_CANARY_811 -->')
        try:assert 'TEMPLATE_CANARY_811' in request('GET','/')[2].decode(),'serve the actual template file'
        finally:p.write_text(original)
    check('load template from file',template_loaded)
    def form_create():
        status,headers,_=request('POST','/tasks',raw=urllib.parse.urlencode({'title':'From form'}).encode(),ctype='application/x-www-form-urlencoded')
        eq(status,303);eq(headers.get('location'),'/');assert any(x['title']=='From form' for x in api('GET','/api/tasks')['tasks'])
    check('create through HTML form',form_create)
    check('reject invalid form title',lambda:eq(request('POST','/tasks',raw=b'title=++',ctype='application/x-www-form-urlencoded')[0],400))
    def form_toggle():
        status,headers,_=request('POST',f'/tasks/{id2}/toggle',raw=b'',ctype='application/x-www-form-urlencoded');eq(status,303)
        eq(next(x for x in api('GET','/api/tasks')['tasks'] if x['id']==id2)['done'],True)
    check('toggle through HTML form',form_toggle)
    def form_delete():
        status,_,_=request('POST',f'/tasks/{escaped["id"]}/delete',raw=b'',ctype='application/x-www-form-urlencoded');eq(status,303)
        assert all(x['id']!=escaped['id'] for x in api('GET','/api/tasks')['tasks'])
    check('delete through HTML form',form_delete)
    sql=api('POST','/api/tasks',{'title':"Robert'); DROP TABLE tasks;--"},201)['task']
    check('SQL text stays data',lambda:eq(next(x for x in api('GET','/api/tasks')['tasks'] if x['id']==sql['id'])['title'],sql['title']))
    def concurrency():
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            tasks=list(pool.map(lambda i:api('POST','/api/tasks',{'title':f'Parallel {i}'},201)['task'],range(8)))
        eq(len({t['id'] for t in tasks}),8)
    check('concurrent creates are unique',concurrency)
    def persistence():
        before=api('GET','/api/tasks')['tasks'];stop();start();eq(api('GET','/api/tasks')['tasks'],before)
        assert pathlib.Path(DB).is_file();conn=sqlite3.connect(DB);assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok';conn.close()
    check('SQLite persistence after restart',persistence)
    def module_contract():
        import importlib.util
        spec=importlib.util.spec_from_file_location('grader_store',ROOT/'store.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        path='/tmp/direct.sqlite';module.init_db(path);item=module.create_task(path,'  Module task  ');eq(item['title'],'Module task')
        eq(module.set_done(path,item['id'],True)['done'],True);eq(module.list_tasks(path,status='done',query='module'),[module.set_done(path,item['id'],True)])
        rejects(lambda:module.create_task(path,''));rejects(lambda:module.set_done(path,item['id'],1))
        eq(module.delete_task(path,item['id']),True);eq(module.set_done(path,item['id'],False),None);eq(module.delete_task(path,item['id']),False)
        rejects(lambda:module.list_tasks(path,status='bad'))
    check('independent store module contract',module_contract)
    check('delete API',lambda:api('DELETE',f'/api/tasks/{id1}',expected=204))
    check('deleted item stays absent',lambda:api('PATCH',f'/api/tasks/{id1}',{'done':False},404))
    def no_reuse():
        item=api('POST','/api/tasks',{'title':'Temporary'},201)['task'];api('DELETE',f'/api/tasks/{item["id"]}',expected=204)
        new=api('POST','/api/tasks',{'title':'After delete'},201)['task'];assert new['id']>item['id'],'ids must not be reused'
    check('deleted ids are not reused',no_reuse)
except BaseException as e:rows.append({'name':'integration infrastructure','passed':False,'error':str(e)[:500]})
finally:stop()
print(json.dumps({'passed':bool(rows) and all(x['passed'] for x in rows),'passed_cases':sum(x['passed'] for x in rows),'total_cases':len(rows),'cases':rows,'build':'py_compile app.py + store.py','scope':'isolated four-file app with loopback HTTP and SQLite','preview_html':preview,'preview_css':FILES['static/style.css'][:16000]}))
'''
