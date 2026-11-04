#!/usr/bin/env python3
"""Run two zero-label high-effort Tickets and one labeled revision, then score.

Use a Python environment with torch/numpy. Never overwrite failed model trials;
--resume may reuse completed, passing workflows after an orchestration failure.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
from arena import run
from snapshot import freeze_runtime, snapshot_inputs
from summarize import summarize
from weight_lab import prepare, train, score, save

def archive(arm, root, kind, labels):
    tickets=json.loads((arm/'tickets.json').read_text())['items']
    fixtures=json.loads((arm/'fixture-plan.json').read_text());sessions={}
    for line in (arm/'state/arena-events.jsonl').read_text().splitlines():
        item=json.loads(line);event=item['event']
        if event['type']!='tool/result':continue
        for block in event.get('data',{}).get('message',{}).get('content',[]):
            if block.get('type')!='text':continue
            try:value=json.loads(block['text'])
            except ValueError:continue
            if isinstance(value,dict) and 'fixture' in value:sessions[value['fixture']]=item['sessionId']
    outputs=[]
    for fixture in fixtures:
        matched=[t for t in tickets if t['sessionId']==sessions.get(fixture['id'])]
        if len(matched)!=1:raise ValueError('Cannot unambiguously map fixture to a worker')
        ticket=matched[0];directory=Path(ticket['directory']).resolve()
        if not directory.is_relative_to((arm/'state/work').resolve()):raise ValueError('Workspace escaped trial')
        text=(directory/'weights.json').read_text()
        name='zero-'+fixture['id'].split('--')[-1] if kind=='zero-label' else 'revision'
        target=root/(name+'.weights.json')
        if target.exists() and target.read_text()!=text:raise ValueError('Frozen artifact changed')
        target.write_text(text)
        writes=[]
        for line in (arm/'state/arena-events.jsonl').read_text().splitlines():
            item=json.loads(line);event=item['event']
            if item['sessionId']==ticket['sessionId'] and event['type']=='tool/call' and event.get('data',{}).get('name')=='arena_write':writes.append(event)
        outputs.append({'name':name,'kind':kind,'labels_seen':labels,'artifact':str(target),
                       'ticket_id':ticket['id'],'session':ticket['sessionId'],'write_attempts':len(writes),
                       'sha256':hashlib.sha256(text.encode()).hexdigest()})
    return outputs

def revision_fixture(original, prior, training):
    fixture=copy.deepcopy(original)
    fixture['prompt']='ONE LABELED REVISION. You are given the frozen first candidate weights in prior.weights.json and exactly 5,062 labeled development rows in labeled.csv. Revise or retain that construction using mathematical reasoning. No gradient training or code execution is available. This is your only labeled revision. Final held-out data and scores remain inaccessible. If the prior construction is already correct, retaining it is acceptable; explain the audit. The same architecture, JSON schema and structural-only test apply.\n\n'+fixture['prompt'].replace('This is a zero-labeled-example mathematical construction experiment.','This is a 5,062-labeled-example revision experiment.').replace('No labeled examples or test distribution samples are supplied.','Labeled development examples are supplied in labeled.csv; no final test examples are supplied.')
    fixture['files']={'weights.json':'','prior.weights.json':prior,
                      'labeled.csv':'digits,valid\n'+''.join(f'{s},{y}\n' for s,y,_ in training[:5062])}
    return fixture

def experiment(config,root,model='blackfold',resume=False,epochs=300):
    root=root.resolve()
    if root.exists() and not resume:raise FileExistsError('Use a new output or explicitly resume completed workflows')
    if not root.exists():prepare(root)
    if (root/'comparison.json').exists():raise FileExistsError('Final evaluation already exists; read it without rerunning/tuning')
    state={'schema':1,'status':'preparing','started':time.time(),'primary_candidate':'zero-00','heldout_evaluated':False,'arms':[]}
    def receipt():save(root/'experiment.json',state)
    receipt()
    try:
        private=root/'fleet.frozen.private.json'
        if private.exists():frozen=freeze_runtime(json.loads(private.read_text()),root/'runtime')
        else:
            frozen=freeze_runtime(config,root/'runtime');save(private,frozen);private.chmod(0o600)
        state['runtime_snapshot_sha256']=frozen['runtime_snapshot_sha256'];receipt()
        def trial(name,count,settings):
            target=root/name
            if not target.exists():run(settings,model,model,count,[],target,2400,'on','weights-v1','high',count)
            elif not resume:raise FileExistsError('Trial output exists')
            summary=summarize(target);save(target/'summary.json',summary)
            if not summary['passed']:raise RuntimeError(name+' did not finish and pass structure validation; do not automatically retry')
            state['arms'].append({'arm':name,'status':summary['status'],'wall_s':summary['wall_s']});receipt()
            return target
        state['status']='zero-label';receipt();zero=trial('zero-high',2,frozen)
        submissions=archive(zero,root,'zero-label',0)
        primary=next(s for s in submissions if s['name']=='zero-00')
        original=json.loads((snapshot_inputs(frozen)/'tasks-weights-v1.json').read_text())[0]
        revised=copy.deepcopy(frozen);revised['fixture_input']=[revision_fixture(original,Path(primary['artifact']).read_text(),json.loads((root/'train.json').read_text()))]
        state['status']='revision';receipt();arm=trial('revision-high',1,revised)
        submissions+=archive(arm,root,'one-labeled-revision',5062);save(root/'submissions.json',submissions)
        state['artifacts_frozen_at']=time.time();state['status']='training';receipt()
        if not (root/'training.json').exists():train(root,epochs)
        state['status']='scoring-frozen-artifacts';receipt();score(root,submissions)
        state.update(status='finished',heldout_evaluated=True)
    except BaseException as e:state.update(status='stopped',error=str(e));raise
    finally:state['finished']=time.time();receipt()
    return state

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default='blackfold');p.add_argument('--resume',action='store_true');p.add_argument('--epochs',type=int,default=300);a=p.parse_args()
    print(json.dumps(experiment(json.loads(a.config.read_text()),a.output,a.model,a.resume,a.epochs),indent=2))
