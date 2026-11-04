"""Trusted contract peers used only inside component-test containers, never supplied as starter solutions."""
REFERENCE = {}
REFERENCE['store.py'] = r'''
import sqlite3

def connect(path):
 c=sqlite3.connect(path,timeout=5);c.row_factory=sqlite3.Row;return c

def init_db(path):
 with connect(path) as c:c.execute('CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0)')

def task(r):return {'id':r['id'],'title':r['title'],'done':bool(r['done'])} if r else None

def list_tasks(path,status='all',query=''):
 if status not in ('all','open','done') or not isinstance(query,str):raise ValueError('invalid filter')
 with connect(path) as c:rows=c.execute('SELECT * FROM tasks ORDER BY id').fetchall()
 return [task(r) for r in rows if (status=='all' or bool(r['done'])==(status=='done')) and query.casefold() in r['title'].casefold()]

def create_task(path,title):
 if not isinstance(title,str) or not 1<=len(title.strip())<=120:raise ValueError('title must have 1..120 characters')
 with connect(path) as c:
  cursor=c.execute('INSERT INTO tasks(title) VALUES(?)',(title.strip(),));return task(c.execute('SELECT * FROM tasks WHERE id=?',(cursor.lastrowid,)).fetchone())

def set_done(path,id,done):
 if not isinstance(done,bool):raise ValueError('done must be boolean')
 with connect(path) as c:
  c.execute('UPDATE tasks SET done=? WHERE id=?',(int(done),id));return task(c.execute('SELECT * FROM tasks WHERE id=?',(id,)).fetchone())

def delete_task(path,id):
 with connect(path) as c:return c.execute('DELETE FROM tasks WHERE id=?',(id,)).rowcount>0
'''
REFERENCE['app.py'] = r'''
import os,json,html,re,urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import store
ROOT=Path(__file__).resolve().parent;DB=os.environ.get('APP_DB','/tmp/taskboard.sqlite')
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,value=None,ctype='application/json',location=None):
  body=b'' if status in (204,303) else json.dumps(value).encode() if ctype=='application/json' else value.encode()
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)))
  if location:self.send_header('Location',location)
  self.end_headers();self.wfile.write(body)
 def error(self,status,message):self.send(status,{'error':message})
 def body(self):
  try:length=int(self.headers.get('Content-Length','0'))
  except ValueError:raise ValueError('invalid length')
  if length<0:raise ValueError('invalid length')
  if length>8192:self.error(413,'body too large');return None
  return self.rfile.read(length)
 def json_body(self):
  if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':self.error(415,'expected JSON');return None
  raw=self.body()
  if raw is None:return None
  try:
   value=json.loads(raw)
   if not isinstance(value,dict):raise ValueError()
   return value
  except (ValueError,UnicodeError):self.error(400,'invalid JSON');return None
 def page(self,query='',error='',title='',status_code=200):
  q=urllib.parse.parse_qs(query,keep_blank_values=True);status=q.get('status',['all'])[0];search=q.get('q',[''])[0]
  tasks=store.list_tasks(DB,status,search)
  rows=''.join('<li class="task"><span class="task-title">'+html.escape(t['title'])+'</span> <span class="task-state">'+('Done' if t['done'] else 'Open')+'</span><span class="task-actions"><form method="post" action="/tasks/'+str(t['id'])+'/toggle"><button>'+('Reopen' if t['done'] else 'Complete')+'</button></form><form method="post" action="/tasks/'+str(t['id'])+'/delete"><button>Delete</button></form></span></li>' for t in tasks) or '<li class="empty-state">No matching tasks</li>'
  filters=''.join('<a class="filter'+(' active' if key==status else '')+'" href="/?'+html.escape(urllib.parse.urlencode({'status':key,'q':search}),quote=True)+'"'+(' aria-current="page"' if key==status else '')+'>'+key.title()+'</a>' for key in ['all','open','done'])
  filters+='<form class="search" method="get" action="/"><label for="search">Search tasks</label><input type="search" id="search" name="q" value="'+html.escape(search,quote=True)+'"><input type="hidden" name="status" value="'+status+'"><button>Search</button></form>'
  values={'TASKS':rows,'FILTERS':filters,'STATUS':str(len(tasks))+' tasks','ERROR':'<p role="alert" class="form-error">'+html.escape(error)+'</p>' if error else '', 'TITLE_VALUE':html.escape(title,quote=True),'QUERY':html.escape(search,quote=True)}
  text=(ROOT/'templates/index.html').read_text();text=re.sub(r'\{\{([A-Z_]+)\}\}',lambda m:values.get(m[1],''),text)
  self.send(status_code,text,'text/html')
 def do_GET(self):
  u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query,keep_blank_values=True)
  try:
   if u.path=='/health':return self.send(200,{'status':'ok'})
   if u.path=='/static/style.css':return self.send(200,(ROOT/'static/style.css').read_text(),'text/css')
   if u.path=='/api/tasks':return self.send(200,{'tasks':store.list_tasks(DB,q.get('status',['all'])[0],q.get('q',[''])[0])})
   if u.path=='/':return self.page(u.query)
   self.error(404,'not found')
  except ValueError as e:self.error(400,str(e))
 def do_POST(self):
  path=urllib.parse.urlsplit(self.path).path
  try:
   if path=='/api/tasks':
    data=self.json_body()
    if data is None:return
    if set(data)!={'title'}:raise ValueError('expected title only')
    return self.send(201,{'task':store.create_task(DB,data['title'])})
   if path=='/tasks':
    raw=self.body()
    if raw is None:return
    data=urllib.parse.parse_qs(raw.decode(),keep_blank_values=True);title=data.get('title',[''])[0]
    try:store.create_task(DB,title)
    except ValueError as e:return self.page(error=str(e),title=title,status_code=400)
    return self.send(303,location='/')
   match=re.fullmatch(r'/tasks/([1-9][0-9]*)/(toggle|delete)',path)
   if match:
    id=int(match[1]);old=next((t for t in store.list_tasks(DB) if t['id']==id),None)
    if old is None:return self.error(404,'not found')
    if match[2]=='delete':store.delete_task(DB,id)
    else:store.set_done(DB,id,not old['done'])
    return self.send(303,location='/')
   self.error(404,'not found')
  except (ValueError,UnicodeError) as e:self.error(400,str(e))
 def do_PATCH(self):
  match=re.fullmatch(r'/api/tasks/([1-9][0-9]*)',urllib.parse.urlsplit(self.path).path)
  if not match:return self.error(404,'not found')
  try:
   data=self.json_body()
   if data is None:return
   if set(data)!={'done'}:raise ValueError('expected done only')
   value=store.set_done(DB,int(match[1]),data['done'])
   if value is None:return self.error(404,'not found')
   self.send(200,{'task':value})
  except ValueError as e:self.error(400,str(e))
 def do_DELETE(self):
  match=re.fullmatch(r'/api/tasks/([1-9][0-9]*)',urllib.parse.urlsplit(self.path).path)
  if not match or not store.delete_task(DB,int(match[1])):return self.error(404,'not found')
  self.send(204)
 def do_PUT(self):self.error(405,'method not allowed')
 def do_OPTIONS(self):self.error(405,'method not allowed')
 def do_HEAD(self):self.error(405,'method not allowed')
if __name__=='__main__':
 store.init_db(DB);ThreadingHTTPServer((os.environ.get('APP_HOST','127.0.0.1'),int(os.environ.get('APP_PORT','8765'))),Handler).serve_forever()
'''
REFERENCE['templates/index.html'] = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Taskboard</title><link rel="stylesheet" href="/static/style.css"></head><body><main><h1>Taskboard</h1><p>{{STATUS}}</p>{{ERROR}}<form method="post" action="/tasks"><label for="title">Task title</label><input id="title" name="title" maxlength="120" required value="{{TITLE_VALUE}}"><button>Add task</button></form><nav aria-label="Task filters">{{FILTERS}}</nav><ul>{{TASKS}}</ul></main></body></html>'''
REFERENCE['static/style.css'] = '''*{box-sizing:border-box}body{margin:0;padding:16px;background:#f5f7fa;font:16px/1.5 system-ui;color:#182030}main{max-width:960px;margin:auto}form,nav{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}input{min-width:0;max-width:100%;font:inherit}button,a{padding:8px}li{overflow-wrap:anywhere}.task-actions{display:flex;gap:8px}.active{font-weight:bold}.form-error{color:#b11}a:focus,input:focus,button:focus{outline:3px solid #2563eb;outline-offset:2px}@media(max-width:480px){form{align-items:stretch;flex-direction:column}input,button{width:100%}}'''
