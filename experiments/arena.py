#!/usr/bin/env python3
"""Drive real dsh-superagent local fixture tickets; never switch model services."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import socket
import fcntl
from contextlib import contextmanager
from reasoning_gateway import off_prefill_gateway

ROOT=Path(__file__).resolve().parent

class ManagementError(RuntimeError):
    def __init__(self,status,message):
        super().__init__(message);self.status=status

def preflight(config):
    results={}
    for name,model in config['models'].items():
        try:
            if not 0<model['max_tokens']<model['context_window']:raise ValueError('Output reservation must leave context room for the prompt')
            with urllib.request.urlopen(model['base_url'].rstrip('/')+'/models',timeout=5) as r:data=json.load(r)
            ids=[x['id'] for x in data.get('data',[])]
            served=next((x for x in data.get('data',[]) if x['id']==model['model']),{})
            advertised=served.get('max_model_len')
            if isinstance(advertised,int) and model['context_window']>advertised:
                raise ValueError(f'Configured context exceeds backend-advertised max_model_len={advertised}')
            results[name]={'available':model['model'] in ids,'configured_model':model['model'],'served_models':ids}
        except Exception as e:results[name]={'available':False,'error':str(e)}
    return results

def request(base,path,body=None,method=None):
    req=urllib.request.Request(base+'/orchestrator/api/'+path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'x-orchestrator-request':'1','Content-Type':'application/json'},method=method)
    try:
        with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        raise ManagementError(e.code,f'{req.get_method()} {path}: HTTP {e.code}: {e.read(1600).decode(errors="replace")}') from e

def save(base,block,value):
    settings=request(base,'settings')
    if block.startswith('source:'):
        revision=next(x['revision'] for x in settings['sources'] if x['id']==block[7:])
    else:revision=settings['blocks'][block]['revision']
    return request(base,'settings/'+block,{'value':value,'revision':revision},method='PUT')

def plan(config):
    available=list(config['models'])
    return {'schema':1,'tasks':[t['id'] for t in json.loads((ROOT/'tasks.json').read_text())],
      'experiments':[
        {'id':'solo','coordinators':available,'workers':available,'worker_counts':[1],'repeats':3},
        {'id':'scaling','homogeneous_models':available,'worker_counts':[1,2,4,8],'repeats':3},
        {'id':'mixed-roles','coordinator_worker_pairs':[[a,b] for a in available for b in available if a!=b],'worker_counts':[2,4],'repeats':3},
        {'id':'output-budget','models':available,'budgets':[8192,32768,65536,131072],
         'gate':'Only budgets that fit the backend context with measured prompt headroom; stop on repeated output.'}],
      'reasoning_experiment':{'modes':['off','on'],'repeats':3,
        'gate':'Verify native template controls. Unsupported modes are unavailable, not relabeled. Keep tasks and output reservations identical.'},
      'metrics':['objective test pass rate','completed tickets per minute','wall latency p50/p95','all-role output tokens per second',
                 'per-role prompt/output tokens','tool failures','max-token endings','actual peak model requests'],
      'rubric':{'correctness':60,'tool_workflow':15,'code_quality_blind_review':15,'scope_and_reporting':10},
      'rules':['Fresh state and workspace for every run.','Use identical fixture hashes and budgets for each matched comparison.',
               'Record hardware, quantization, backend context and actual concurrency; this is a fleet comparison, not hardware-normalized model ranking.',
               'Count coordinator, workers and any helpers in resource totals.','A token-limited ending is incomplete, never a pass.',
               'No GPU service restarts, external messages, deployments, or automated merges.']}

def thinking_kwargs(model,mode):
    controls=model.get('thinking_controls',{'off':{'enable_thinking':False},'on':{'enable_thinking':True}})
    return controls.get(mode)

def overlay(config,state,coordinator,thinking='off'):
    providers={}
    for name,m in config['models'].items():
        providers['arena-'+name]={'api':'openai-completions','baseURL':m['base_url'],'apiKeyEnv':'ARENA_LOCAL_KEY',
          'compat':{'supportsDeveloperRole':False,'maxTokensField':'max_tokens',
                    'thinkingFormat':'chat-template','chatTemplateKwargs':thinking_kwargs(m,thinking) or {}},
          'models':[{'id':m['model'],'contextWindow':m['context_window'],'maxTokens':m['max_tokens'],
                     'reasoningEfforts':{'off':None,'low':'low'}}], 'retryPolicy':{'mode':'normal','maxRetries':0}}
    persona={'id':'persona','name':'@deepseek-ai/dsh-persona','config':{'prefix':'You are a software engineer completing bounded local experiment tickets.'}}
    return [
      {'id':'webserver','config':{'host':'127.0.0.1','port':config.get('port',3086)}},
      {'id':'dsh-superagent','config':{'stateDir':str(state),'localFixtures':True}},
      {'id':'dsh-superagent-github','disabled':True},{'id':'dsh-superagent-feishu','disabled':True},
      {'id':'dsh-superagent-fixtures','disabled':False},
      {'id':'llm-deepseek','disabled':True},{'id':'deepseek-account','disabled':True},
      {'id':'dsh-superagent-worker-preset','config':{'id':'dsh-superagent-worker','name':'Local experiment worker','order':90,'plugins':[persona]}},
      {'id':'dsh-superagent-router-preset','config':{'id':'dsh-superagent-router','name':'Local experiment coordinator','order':91,'plugins':[persona]}},
      {'id':'llm-pi-ai','config':{'providers':providers}},
      {'id':'agent-default-model','config':{'provider':'arena-'+coordinator,'model':config['models'][coordinator]['model']}},
      {'id':'compaction-basic','config':{'headroomTokens':1024,'maxTokens':2048}},
    ]

@contextmanager
def harness_port_lock(config):
    path=Path(config['dsh_home'])/f'arena-port-{config.get("port",3086)}.lock'
    fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another experiment owns this Harness port; no process was changed')
        yield
    finally:os.close(fd)

def run(config,coordinator,worker,count,tasks,output,timeout,thinking='off'):
    for name in {coordinator,worker}:
        model=config['models'][name]
        if not 0<model['max_tokens']<model['context_window']:
            raise ValueError('Output reservation must leave context room for the prompt')
        if thinking_kwargs(model,thinking) is None:
            raise ValueError(f'{name} has no verified native thinking={thinking} control; this arm is unavailable')
    checks=preflight({'models':{name:config['models'][name] for name in {coordinator,worker}}})
    unavailable={name:check for name,check in checks.items() if not check['available']}
    if unavailable:raise RuntimeError('Selected backend unavailable or incompatible: '+json.dumps(unavailable))
    with harness_port_lock(config):
        with off_prefill_gateway(config,thinking,{coordinator,worker},output.resolve()/'request-transforms.jsonl') as routed:
            return run_owned(routed,coordinator,worker,count,tasks,output,timeout,thinking)

def run_owned(config,coordinator,worker,count,tasks,output,timeout,thinking='off'):
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',config.get('port',3086)))==0:raise RuntimeError('Dedicated Harness port is already in use; no process was changed')
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    state=output/'state';state.mkdir()
    run_id=f'{coordinator}-{worker}-w{count}-thinking-{thinking}'
    selected=[t for t in json.loads((ROOT/'tasks.json').read_text()) if not tasks or t['id'] in tasks]
    if not selected:raise ValueError('No matching tasks')
    # At least count tickets for a scaling run; names remain disjoint and deterministic.
    fixtures=[]
    for i in range(max(count,len(selected))):
        t=copy.deepcopy(selected[i%len(selected)]);t['id']+=f'--{i:02d}';fixtures.append(t)
    encoded=json.dumps(fixtures,sort_keys=True).encode()
    manifest={'schema':1,'id':run_id,'coordinator':coordinator,'worker':worker,'workers':count,
        'models':config['models'],'fixture_sha256':hashlib.sha256(encoded).hexdigest(),
        'dsh_version':config.get('dsh_version'),'upstream_revision':config.get('upstream_revision'),
        'started':time.time(),'status':'starting','scope':'real superagent local fixture tickets'}
    manifest['requested_thinking_mode']=thinking
    manifest['thinking_controls']={name:thinking_kwargs(config['models'][name],thinking) for name in {coordinator,worker}}
    manifest['thinking_transport']={name:'closed-think-prefill' if thinking=='off' and config['models'][name].get('thinking_off_prefill') else 'native-template-kwargs' for name in {coordinator,worker}}
    patch=output/'patch.json';patch.write_text(json.dumps(overlay(config,state,coordinator,thinking),indent=2))
    # Session ids are deterministic in the prototype. Every run needs a fresh
    # HOME, not just a fresh SQLite database, to avoid cross-arm history/model reuse.
    home=output/'harness-home';profile=config.get('profile','arena')
    template=Path(config['dsh_home']).resolve()/'profiles'/profile
    target=home/'profiles'/profile;target.mkdir(parents=True)
    for name in ('package.json','cordis.patch.yml'):
        if (template/name).exists():shutil.copy2(template/name,target/name)
    (target/'node_modules').symlink_to(template/'node_modules',target_is_directory=True)
    env=os.environ.copy();env.update(DSH_HOME=str(home),ARENA_LOCAL_KEY='local-only',
      DSH_ARENA_TEST_DRIVER=str(ROOT/'check.py'),ARENA_DOCKER_SSH=config.get('docker_ssh',''),
      ARENA_PYTHON_IMAGE=config.get('python_image','python:3.12-alpine'))
    base=f'http://127.0.0.1:{config.get("port",3086)}'
    command=[config['dsh_bin'],'--profile',config.get('profile','arena'),'--patch',str(patch),'--no-open']
    with (output/'harness.log').open('w') as log:
        proc=subprocess.Popen(command,cwd=ROOT.parent,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    manifest['harness_pid']=proc.pid
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    try:
        for _ in range(90):
            if proc.poll() is not None:raise RuntimeError('Harness exited; inspect harness.log')
            try:
                status=request(base,'state')
                if status.get('setup',{}).get('problem'):raise RuntimeError(str(status['setup']['problem']))
                settings=request(base,'settings')
                if any(s['id']=='fixtures' and s.get('installed') for s in settings.get('sources',[])):break
                time.sleep(1)
            except ManagementError as e:
                if e.status not in (404,503):raise
                time.sleep(1)
            except (OSError,ValueError):time.sleep(1)
        else:raise TimeoutError('Harness did not start in 90 seconds')
        save(base,'source:fixtures',{'enabled':True,'config':{}})
        save(base,'policy',{'rules':[],'sources':{'fixtures':{'allowed':[{'id':'experiment'}]}}})
        save(base,'executor',{'model':{'provider':'arena-'+worker,'model':config['models'][worker]['model']},
           'maxWorkers':count,'directory':'','prepare':'','blockedCommands':[]})
        save(base,'eval',{'mode':'observe','timeoutSeconds':120,
            'model':{'provider':'arena-'+coordinator,'model':config['models'][coordinator]['model']}})
        prompts=request(base,'prompts');value=prompts['current']['config']
        value.update(communication='One brief acknowledgement and a concise final result; replies stay in the local fixture outbox.',
          scope='Complete only the assigned synthetic fixture. Use arena_read, arena_write and arena_test. External tools are unavailable.',
          delivery='This is a local experiment. Do not commit, push, open pull requests, merge, deploy, or contact external services. Run arena_test; repair failures within the budget. Report ready with the orchestrator report tool only after the tests pass; otherwise report blocked or failed. The local solution.py is the deliverable.')
        request(base,'prompts',{'config':value,'baseRevision':prompts['current']['revision']},method='PUT')
        (state/'fixtures.json').write_text(encoded.decode())
        manifest.update(status='running',work_started=time.time())
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if proc.poll() is not None:raise RuntimeError('Harness exited during work')
            active=request(base,'tickets?state=open&limit=64')
            ended=request(base,'tickets?state=ended&limit=64')
            result={'items':active.get('items',[])+ended.get('items',[])}
            (output/'tickets.json').write_text(json.dumps(result,indent=2))
            # The endpoint returns a bare list (current prototype) or a paginated items list.
            rows=result if isinstance(result,list) else result.get('items',result.get('tickets',[]))
            if len(rows)>=len(fixtures) and all(t.get('state')=='ended' or (t.get('stage')=='person' and t.get('action') in ('failed','blocked')) for t in rows):
                manifest['status']='finished'
                # Drain coordinator reporting and the final worker turn before
                # ending the process or counting the experiment's wall time.
                manifest['reporting_drained']=False
                for _ in range(60):
                    calls=state/'arena-calls.jsonl'
                    entries=[json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []
                    ends=[x for x in entries if x['phase']=='end']
                    starts={x['id'] for x in entries if x['phase']=='start'}
                    ended_ids={x['id'] for x in ends}
                    if ends and starts==ended_ids and time.time()-ends[-1]['at']/1000>=2:
                        manifest['reporting_drained']=True
                        break
                    time.sleep(1)
                break
            time.sleep(2)
        else:manifest['status']='timeout'
        manifest['finished']=time.time();manifest['wall_s']=manifest['finished']-manifest['work_started']
        manifest['expected_tickets']=len(fixtures)
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
        return manifest
    except KeyboardInterrupt:
        manifest.update(status='interrupted',finished=time.time())
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2));raise
    except Exception as e:
        manifest.update(status='infrastructure_failed',error=str(e),finished=time.time())
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2));raise
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','preflight','run']);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--coordinator',default='blackfold');p.add_argument('--worker',default='blackfold')
    p.add_argument('--workers',type=int,choices=[1,2,4,8],default=1);p.add_argument('--tasks',default='')
    p.add_argument('--output',type=Path);p.add_argument('--timeout',type=int,default=600)
    p.add_argument('--thinking',choices=['off','on'],default='off');p.add_argument('--max-tokens',type=int);a=p.parse_args()
    c=json.loads(a.config.read_text())
    if a.max_tokens is not None:
        for name in {a.coordinator,a.worker}:c['models'][name]['max_tokens']=a.max_tokens
    if a.action=='plan':print(json.dumps(plan(c),indent=2))
    elif a.action=='preflight':print(json.dumps(preflight(c),indent=2))
    else:
        if not a.output:p.error('--output is required for a run')
        print(json.dumps(run(c,a.coordinator,a.worker,a.workers,a.tasks.split(',') if a.tasks else [],a.output,a.timeout,a.thinking),indent=2))
