"""Versioned UI acceptance checks; app-v1 source and historical scores stay unchanged."""
from app_checks import program as v1_program

UI_CHECKS = r'''
    class Page(HTMLParser):
        def __init__(self,text):
            super().__init__();self.tags=[];self.text=[];self.feed(text)
        def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))
        def handle_data(self,data):self.text.append(data)
        def visible(self):return ' '.join(self.text)
    def page(path='/'):
        status,headers,body=request('GET',path);eq(status,200);assert 'text/html' in headers.get('content-type','');return Page(body.decode())
    def ui_filter(path,present,absent):
        text=page(path).visible();assert present in text,'matching task missing';assert absent not in text,'nonmatching task remains visible'
    check('UI open filter excludes completed tasks',lambda:ui_filter('/?status=open','CAFÉ notes','First task'))
    check('UI done filter excludes open tasks',lambda:ui_filter('/?status=done','First task','CAFÉ notes'))
    check('UI Unicode search',lambda:ui_filter('/?q='+urllib.parse.quote('café'),'CAFÉ notes','First task'))
    check('UI literal percent search',lambda:ui_filter('/?q=%25','100% done','CAFÉ notes'))
    def empty_filter():
        text=page('/?q=UNMATCHED_QUERY_187').visible();assert 'First task' not in text and 'CAFÉ notes' not in text
        assert any(word in text.lower() for word in ['no tasks','no matching','nothing','empty','no results']), 'explain an empty filtered result'
    check('UI filtered empty state',empty_filter)
    def labels():
        p=page();labels={a.get('for') for t,a in p.tags if t=='label'}
        title=next(a for t,a in p.tags if t=='input' and a.get('name')=='title')
        search=next(a for t,a in p.tags if t=='input' and a.get('name')=='q')
        assert title.get('id') and title['id'] in labels,'associate title label with input id'
        assert search.get('id') and search['id'] in labels,'associate search label with input id'
        eq(title.get('maxlength'),'120')
    check('UI associated labels and title length hint',labels)
    def active_filter():
        p=page('/?status=open')
        active=[a for t,a in p.tags if t=='a' and a.get('aria-current')=='page']
        assert len(active)==1,'exactly one active status link needs aria-current=page'
        eq(urllib.parse.parse_qs(urllib.parse.urlsplit(active[0].get('href','')).query).get('status'),['open'])
    check('UI active status is accessible',active_filter)
    def preserve_search():
        p=page('/?q='+urllib.parse.quote('café'))
        links=[urllib.parse.parse_qs(urllib.parse.urlsplit(a.get('href','')).query) for t,a in p.tags if t=='a']
        for status in ['open','done']:
            assert any(q.get('status')==[status] and q.get('q')==['café'] for q in links),'status links must retain the search'
    check('UI status links preserve search',preserve_search)
    def invalid_form():
        bad='Z'*121
        status,headers,body=request('POST','/tasks',raw=urllib.parse.urlencode({'title':bad}).encode(),ctype='application/x-www-form-urlencoded')
        eq(status,400);assert 'text/html' in headers.get('content-type',''),'HTML forms need an HTML error page'
        p=Page(body.decode());assert any(a.get('role')=='alert' for t,a in p.tags),'visible form error needs role=alert'
        assert any(t=='input' and a.get('name')=='title' and a.get('value')==bad for t,a in p.tags),'retain invalid title for correction'
        assert all(t['title']!=bad for t in api('GET','/api/tasks')['tasks'])
    check('UI form errors explain failure and preserve input',invalid_form)
    def form_markup():
        p=page()
        actions={a.get('action') for t,a in p.tags if t=='form' and a.get('method','').lower()=='post'}
        assert f'/tasks/{id2}/toggle' in actions and f'/tasks/{id2}/delete' in actions,'render per-task toggle and delete forms'
    check('UI per-task actions use real forms',form_markup)
    def form_workflow():
        title='UI workflow 109';status,headers,_=request('POST','/tasks',raw=urllib.parse.urlencode({'title':title}).encode(),ctype='application/x-www-form-urlencoded');eq(status,303)
        item=next(t for t in api('GET','/api/tasks')['tasks'] if t['title']==title)
        assert title in page('/?status=open').visible()
        eq(request('POST',f'/tasks/{item["id"]}/toggle',raw=b'',ctype='application/x-www-form-urlencoded')[0],303)
        assert title not in page('/?status=open').visible() and title in page('/?status=done').visible()
        eq(request('POST',f'/tasks/{item["id"]}/delete',raw=b'',ctype='application/x-www-form-urlencoded')[0],303)
        assert title not in page('/?status=done').visible()
    check('UI create complete filter delete workflow',form_workflow)
    def query_escape():
        query='<img src=x onerror=alert(9)>';p=page('/?q='+urllib.parse.quote(query))
        assert not any(t=='img' or any(k.lower().startswith('on') for k in a) for t,a in p.tags),'query must stay data'
        assert any(t=='input' and a.get('name')=='q' and a.get('value')==query for t,a in p.tags),'retain literal search in the search input'
    check('UI search reflects escaped data',query_escape)
'''

def program(files):
    source=v1_program(files)
    marker="    check('render accessible escaped UI',html_ui)\n"
    if source.count(marker)!=1:raise ValueError('Unexpected base grader; review the versioned extension')
    return source.replace(marker,marker+UI_CHECKS)
