"""Loopback-only request adapter for GLM's closed-think assistant prefill.

The retained GLM template always opens <think> for a new assistant turn. Its
assistant-history branch renders an empty assistant as <think></think>.
Continuing that message supplies an answer-channel prefix without changing the
serving process or its tokenizer files. This is an experimental prompt mode.
"""
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.request
import urllib.error


def closed_think_prefill(payload):
    result=copy.deepcopy(payload)
    result['messages'].append({'role':'assistant','content':''})
    result['continue_final_message']=True
    result['add_generation_prompt']=False
    return result


@contextmanager
def off_prefill_gateway(config,thinking,names,log_path):
    routed=copy.deepcopy(config)
    selected={name:config['models'][name] for name in names
              if thinking=='off' and config['models'][name].get('thinking_off_prefill')}
    if not selected:
        yield routed
        return
    lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            name=self.path.split('/')[1] if self.path.startswith('/') else ''
            if name not in selected or self.path!=f'/{name}/v1/chat/completions':
                self.send_error(404);return
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4*1024*1024:
                self.send_error(413);return
            try:
                payload=closed_think_prefill(json.loads(self.rfile.read(size)))
                with lock:
                    with log_path.open('a') as log:
                        log.write(json.dumps({'at':time.time(),'model':name,'mode':'closed-think-prefill',
                            'messages':len(payload['messages']),'continue_final_message':True,
                            'add_generation_prompt':False,'chat_template_kwargs':payload.get('chat_template_kwargs')})+'\n')
                headers={'Content-Type':'application/json'}
                if self.headers.get('Authorization'):headers['Authorization']=self.headers['Authorization']
                req=urllib.request.Request(selected[name]['base_url'].rstrip('/')+'/chat/completions',
                    data=json.dumps(payload).encode(),headers=headers)
                with urllib.request.urlopen(req,timeout=600) as response:
                    self.send_response(response.status)
                    self.send_header('Content-Type',response.headers.get('Content-Type','application/json'))
                    self.send_header('Connection','close');self.end_headers()
                    while block:=response.read1(65536):self.wfile.write(block);self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
            except urllib.error.HTTPError as e:
                self.send_response(e.code);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(e.read(8192))
            except Exception as e:
                self.send_response(502);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps({'error':{'message':str(e)[:1000]}}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    for name in selected:
        routed['models'][name]['upstream_base_url']=selected[name]['base_url']
        routed['models'][name]['base_url']=f'http://127.0.0.1:{server.server_port}/{name}/v1'
    try:yield routed
    finally:server.shutdown();server.server_close();thread.join(timeout=2)
