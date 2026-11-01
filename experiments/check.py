#!/usr/bin/env python3
"""Execute generated Python only in a disposable, networkless Docker container."""
import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import uuid
from checks_v2 import v2_checks

CHECKS = {
 'ledger': '''
f = ns['summarize']
e = lambda i,a='0.10',t='credit',account='a':dict(id=i,account=account,type=t,amount=a)
check('empty', lambda: eq(f([]), {}))
check('decimal cents', lambda: eq(f([e('1','0.29'),e('2','0.01')]), {'a':30}))
check('debit', lambda: eq(f([e('1','1.00'),e('2','0.23','debit')]), {'a':77}))
check('accounts', lambda: eq(f([e('1'),e('2',account='b')]), {'a':10,'b':10}))
check('duplicate', lambda: eq(f([e('1'),e('1')]), {'a':10}))
check('conflict', lambda: rejects(lambda:f([e('1'),e('1','0.20')])) )
check('precision', lambda: rejects(lambda:f([e('1','0.001')])) )
check('negative', lambda: rejects(lambda:f([e('1','-1')])) )
check('type', lambda: rejects(lambda:f([e('1',t='other')])) )
check('amount type', lambda: rejects(lambda:f([e('1',True)])) )
check('missing field', lambda: rejects(lambda:f([{}])) )
x=[e('1')];before=copy.deepcopy(x)
check('preserves input', lambda:(f(x),eq(x,before)))
''',
 'intervals': '''
f=ns['merge_intervals']
check('empty',lambda:eq(f([]),[]))
check('overlap',lambda:eq(f([[3,5],[1,4],[8,9]]),[(1,5),(8,9)]))
check('touching',lambda:eq(f([[1,2],[2,3]]),[(1,3)]))
check('nested',lambda:eq(f([[1,9],[2,3],[5,8]]),[(1,9)]))
check('zero',lambda:eq(f([[2,2],[4,5]]),[(4,5)]))
check('float',lambda:eq(f([[0.5,1.5],[-1,0.5]]),[(-1,1.5)]))
check('inverted',lambda:rejects(lambda:f([[5,1]])))
check('bool',lambda:rejects(lambda:f([[False,3]])))
check('nonfinite',lambda:rejects(lambda:f([[0,float('inf')]])))
check('malformed',lambda:rejects(lambda:f([[1,2,3]])))
x=[[3,5],[1,2]];before=copy.deepcopy(x)
check('preserves input',lambda:(f(x),eq(x,before)))
''',
 'pagination': '''
f=ns['paginate'];x=[{'id':str(i),'value':i} for i in range(5)]
check('empty',lambda:eq(f([]),{'items':[],'next_cursor':None}))
check('first',lambda:eq(f(x,limit=2),{'items':x[:2],'next_cursor':'1'}))
check('middle',lambda:eq(f(x,'1',2),{'items':x[2:4],'next_cursor':'3'}))
check('last',lambda:eq(f(x,'3',2),{'items':x[4:],'next_cursor':None}))
check('after end',lambda:eq(f(x,'4',2),{'items':[],'next_cursor':None}))
check('unknown cursor',lambda:rejects(lambda:f(x,'missing')))
check('duplicate',lambda:rejects(lambda:f([{'id':'x'},{'id':'x'}])))
check('empty id',lambda:rejects(lambda:f([{'id':''}])))
check('bad item',lambda:rejects(lambda:f([None])))
check('boolean limit',lambda:rejects(lambda:f(x,limit=True)))
check('large limit',lambda:rejects(lambda:f(x,limit=101)))
before=copy.deepcopy(x)
check('preserves input',lambda:(f(x),eq(x,before)))
'''
}

CHECKS_V2=v2_checks(CHECKS)

def bootstrap(task, source, suite='v1'):
    checks=CHECKS if suite=='v1' else CHECKS_V2
    return 'SOURCE='+repr(source)+'\nCHECKS='+repr(checks[task])+'''\nimport json,copy,contextlib,io
rows=[]
def eq(a,b):
    assert a==b, f'{a!r} != {b!r}'
def rejects(fn):
    try: fn()
    except ValueError: return
    raise AssertionError('expected ValueError')
def check(name, fn):
    try: fn(); rows.append({'name':name,'passed':True})
    except BaseException as e: rows.append({'name':name,'passed':False,'error':str(e)[:180]})
ns={}
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        exec(compile(SOURCE,'solution.py','exec'),ns)
        exec(CHECKS)
except BaseException as e: rows.append({'name':'load','passed':False,'error':str(e)[:180]})
print(json.dumps({'passed':bool(rows) and all(r['passed'] for r in rows),'passed_cases':sum(r['passed'] for r in rows),'total_cases':len(rows),'cases':rows}))
'''

def run(task, source, suite='v1'):
    host = os.environ.get('ARENA_DOCKER_SSH', '')
    if host and not re.fullmatch(r'[A-Za-z0-9_.@-]+',host): raise ValueError('Invalid Docker SSH alias')
    image = os.environ.get('ARENA_PYTHON_IMAGE','python:3.12-alpine')
    if image.startswith('-'): raise ValueError('Invalid image')
    name='dsharena-'+uuid.uuid4().hex
    def command(args): return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',host,shlex.join(args)] if host else args
    argv=['docker','run','--pull=never','--rm','-i','--name',name,'--network=none','--read-only','--cap-drop=ALL',
          '--security-opt=no-new-privileges','--pids-limit=32','--memory=256m','--memory-swap=256m','--cpus=1',
          '--user=65534:65534','--tmpfs=/tmp:rw,noexec,nosuid,size=16m',image,'python','-I','-S','-']
    try:
        r=subprocess.run(command(argv),input=bootstrap(task,source,suite),text=True,capture_output=True,timeout=12)
        if r.returncode: return {'passed':False,'infrastructure_error':r.stderr[-1200:]}
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:return {'passed':False,'error':'test container exceeded 12-second limit'}
    finally:
        subprocess.run(command(['docker','rm','-f',name]),capture_output=True,timeout=6)

if __name__=='__main__':
    def cancelled(_signal,_frame):raise RuntimeError('Test driver was cancelled')
    signal.signal(signal.SIGTERM,cancelled)
    p=argparse.ArgumentParser();p.add_argument('--task',choices=CHECKS_V2,required=True)
    p.add_argument('--suite',choices=['v1','v2'],default=os.environ.get('DSH_ARENA_SUITE','v1'));a=p.parse_args()
    if a.task not in (CHECKS if a.suite=='v1' else CHECKS_V2):p.error('Task is not part of this suite')
    try: print(json.dumps(run(a.task,json.load(sys.stdin)['source'],a.suite)))
    except Exception as e: print(json.dumps({'passed':False,'infrastructure_error':str(e)[:1200]}))
