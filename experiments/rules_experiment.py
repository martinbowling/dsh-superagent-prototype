#!/usr/bin/env python3
"""Freeze first, generate fresh policies, run real high-effort DSH, then score all.

The rule-informed compiler is the fair knowledge comparator. Supervised fits are
explicitly example-only baselines. Final labels never enter an arena fixture.
"""
import argparse
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
import time
from arena import run
from snapshot import freeze_runtime, snapshot_inputs, verify_snapshot
from summarize import summarize
from rules_spec import policy, compile_weights, qualify, datasets, fixture
from weight_lab import save, train, evaluate, materialize, network, metrics
from weights_data import digest

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def training_environment():
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';return env

def prepare(root,frozen):
    """Called only after copying/hash-freezing code; seeds are then freshly drawn."""
    if (root/'generation.json').exists():raise FileExistsError('Policies already generated')
    verify_snapshot(frozen)
    receipt={'generated_at':time.time(),'runtime_snapshot_sha256':frozen['runtime_snapshot_sha256'],
             'policy_seeds':{tag:secrets.randbits(64) for tag in ('a','b','c')},'policies':[]}
    save(root/'generation.json',receipt)
    fixtures=[]
    for tag,seed in receipt['policy_seeds'].items():
        directory=root/('policy-'+tag);directory.mkdir()
        rule=policy(seed);save(directory/'rules.json',rule)
        compiler=compile_weights(rule);save(directory/'compiler.weights.json',compiler)
        save(directory/'qualification.json',qualify(rule))
        started=time.time();training,heldout=datasets(rule,seed^0x631C8BDA4261)
        save(directory/'train.json',training);save(directory/'heldout.json',heldout)
        record={'tag':tag,'seed':seed,'rule_sha256':sha(directory/'rules.json'),'train_sha256':digest(training),'test_sha256':digest(heldout),
                'train_count':len(training),'heldout_count':len(heldout),'uniform_train_positive_fraction':sum(r[1] for r in training)/len(training),
                'strata':{f:sum(r[2]==f for r in heldout) for f in sorted({r[2] for r in heldout})},'generated_s':time.time()-started,
                'train_test_stems_disjoint':True,'compiler_sha256':sha(directory/'compiler.weights.json')}
        save(directory/'data-receipt.json',record);receipt['policies'].append(record);save(root/'generation.json',receipt)
        fixtures.append(fixture(rule,tag))
        print(json.dumps({'prepared':tag,'train':len(training),'heldout':len(heldout),'seconds':record['generated_s']}),flush=True)
    save(root/'fixture-input.json',fixtures);return fixtures

def train_batch(root):
    for tag in ('a','b','c'):train(root/('policy-'+tag),epochs=300,budgets=(512,8192))

def archive(root,arm):
    """Bind each rule fixture to its actual worker and last successful test hash."""
    tickets=json.loads((arm/'tickets.json').read_text())['items'];sessions={};tests={};writes={}
    for line in (arm/'state/arena-events.jsonl').read_text().splitlines():
        item=json.loads(line);event=item['event'];session=item['sessionId'];data=event.get('data',{})
        if event['type']=='tool/call' and data.get('name')=='arena_write':writes[session]=writes.get(session,0)+1
        if event['type']!='tool/result':continue
        for block in data.get('message',{}).get('content',[]):
            if block.get('type')!='text':continue
            try:value=json.loads(block['text'])
            except ValueError:continue
            if not isinstance(value,dict):continue
            if 'fixture' in value:sessions[value['fixture']]=session
            if 'passed_cases' in value:tests[session]=value
    submissions=[]
    for task in json.loads((arm/'fixture-plan.json').read_text()):
        tag=task['id'].split('--')[1];session=sessions.get(task['id']);matched=[t for t in tickets if t['sessionId']==session]
        if len(matched)!=1:raise ValueError('Cannot map fresh policy to a unique worker')
        ticket=matched[0];directory=Path(ticket['directory']).resolve()
        if not directory.is_relative_to((arm/'state/work').resolve()):raise ValueError('Unexpected worker path')
        source=directory/'weights.json';target=root/('policy-'+tag)/'model.weights.json'
        if target.exists():raise FileExistsError('Model weights already archived')
        text=source.read_text();digest_=hashlib.sha256(text.encode()).hexdigest()
        if tests.get(session,{}).get('sha256')!=digest_ or not tests[session].get('passed'):raise ValueError('Final model weights differ from structural test')
        target.write_text(text)
        submissions.append({'policy':tag,'name':'blackfold-high','kind':'rule-informed-llm','labels_seen':0,'artifact':str(target),'sha256':digest_,
                            'write_attempts':writes.get(session,0),'ticket':ticket['id'],'session':session})
    save(root/'submissions.json',submissions);return submissions

def score(root,submissions):
    import torch
    if (root/'comparison.json').exists():raise FileExistsError('Final test already scored')
    torch.set_num_threads(4);results=[]
    for tag in ('a','b','c'):
        directory=root/('policy-'+tag);receipt=json.loads((directory/'data-receipt.json').read_text())
        heldout=json.loads((directory/'heldout.json').read_text())
        if digest(heldout)!=receipt['test_sha256']:raise ValueError('Heldout dataset changed')
        submission=next(s for s in submissions if s['policy']==tag)
        if sha(Path(submission['artifact']))!=submission['sha256']:raise ValueError('Model submission changed')
        for metadata in [submission,{'policy':tag,'name':'trusted-compiler','kind':'rule-informed-compiler','labels_seen':0,
                                      'artifact':str(directory/'compiler.weights.json'),'sha256':receipt['compiler_sha256']}]:
            if sha(Path(metadata['artifact']))!=metadata['sha256']:raise ValueError('Frozen weights changed')
            model,counts=materialize(Path(metadata['artifact']).read_text());results.append({**metadata,**counts,'metrics':evaluate(model,heldout)})
        training=json.loads((directory/'train.json').read_text())
        majority=sum(r[1] for r in training)>=len(training)/2
        results.append({'policy':tag,'name':'train-majority','kind':'training-majority','labels_seen':len(training),
                        'allocated_parameters':99969,'nonzero_parameters':1,'predicted_class':int(majority),'metrics':metrics([majority]*len(heldout),heldout)})
        for path in sorted((directory/'trained').glob('*.json')):
            metadata=json.loads(path.read_text())
            if sha(Path(metadata['artifact']))!=metadata['sha256']:raise ValueError('Trained checkpoint changed')
            model=network();model.load_state_dict(torch.load(metadata['artifact'],map_location='cpu',weights_only=True))
            results.append({k:v for k,v in metadata.items() if k!='curve'}|{'policy':tag,'kind':'example-only-trained','metrics':evaluate(model,heldout)})
    value={'schema':1,'results':results,'policies':json.loads((root/'generation.json').read_text()),
           'primary_metric':'uniform heldout accuracy; stress strata reported separately',
           'limitations':['Fresh program instances, not invention of a new algorithm.','LLM and compiler receive identical explicit rules; training baselines see labels only.','Three policies, one high-effort proposal each, no test-guided revision.','Accuracy does not imply advantages over the trusted compiler.']}
    save(root/'comparison.json',value);return value

def report(root):
    data=json.loads((root/'comparison.json').read_text());rows=[]
    for tag in ('a','b','c'):
        group=[r for r in data['results'] if r['policy']==tag]
        def cells(label,records):
            values=[statistics.median(r['metrics']['strata'][f]['accuracy'] for r in records) for f in ('uniform','group_boundary','veto_boundary')]
            return '<tr><td>'+tag.upper()+'</td><td>'+html.escape(label)+'</td>'+''.join(f'<td>{v:.2%}</td>' for v in values)+f'<td>{records[0]["labels_seen"]:,}</td><td>{records[0]["nonzero_parameters"]:,}</td></tr>'
        rows.append(cells('BLACKFOLD high, zero labels',[r for r in group if r['kind']=='rule-informed-llm']))
        rows.append(cells('Trusted compiler, same rules',[r for r in group if r['kind']=='rule-informed-compiler']))
        rows.append(cells('Training-majority constant',[r for r in group if r['kind']=='training-majority']))
        for n in (512,8192):rows.append(cells(f'Trained, {n:,} labels · median of 3',[r for r in group if r['kind']=='example-only-trained' and r['labels_seen']==n]))
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fresh random rules → neural weights</title><style>body{font:16px/1.55 system-ui,sans-serif;background:#f4f7fb;color:#172338;max-width:1160px;margin:40px auto;padding:0 20px}h1{font-size:clamp(2rem,4vw,3.2rem);line-height:1.15}.box{background:white;border:1px solid #d8e1ec;border-radius:12px;padding:20px;margin:22px 0}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:.9rem}td,th{text-align:left;padding:11px;border-bottom:1px solid #dfe5ee}th{background:#e7eef8}a{color:#1954a6}code{word-break:break-all}</style>
    <p>Actual superagent coordinator + Tickets + workers · native high effort</p><h1>Can DeepSeek compile rules generated after the setup was frozen?</h1>
    <div class="box">Three fresh policy programs combine randomized digit memberships, AND clauses, at-least/exactly/at-most counts and vetoes. Each worker receives its rules but zero examples. It emits numeric weights for the same fixed float32 130 → 512 → 64 → 1 ReLU network: <b>99,969 allocated parameters</b>. Only sparse numeric JSON is interpreted; no model-generated code or lookup runs.</div>
    <p>Primary test: 10,000 unseen uniform inputs per policy. Stress tests: 1,000 group-boundary cases and 1,000 veto-boundary cases per policy, in opposite-label pairs differing in one digit. All 36,000 final labels were hidden from the harness. Every policy and result is reported.</p>
    <div class="scroll"><table><thead><tr><th>Policy</th><th>Method</th><th>Uniform · 10,000</th><th>Group boundary · 1,000</th><th>Veto boundary · 1,000</th><th>Labels accessed</th><th>Nonzero params</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>
    <h2>How to interpret this</h2><p>The trusted compiler gets exactly the same explicit rules as DeepSeek. It is the comparator for knowledge access. Supervised models get examples only; those rows measure learning from labels under the tested recipe. Fresh rules prevent memorizing the exact policy, but do not make Boolean algebra a newly discovered algorithm. Matching the compiler demonstrates reliable translation, not a practical advantage over a dedicated compiler.</p>
    <p>Training uses the identical raw inputs/architecture, 300 AdamW epochs, learning rate 0.001, weight decay 0.01, batch 256, CPU, and three seeds (11/29/47). About 20% of each total label budget selects a checkpoint by internal validation BCE, with training/validation stems disjoint. Test stems are disjoint from training. Natural uniform class prevalence is preserved; balanced accuracy, confusion matrices and F1 are in the full metrics.</p>
    <p>Seeds were freshly drawn only after copying and hash-freezing the runtime, generator and evaluator. Policies, input fixtures and datasets were then hash-frozen before model calls. arena_test provides structural feedback only; every model artifact freezes before final scoring. No best-test candidate selection or test-guided repair occurred. Compiler qualification checks all 16,384 abstract clause assignments and every clause's digit combinations.</p>
    <p><a href="comparison.json">All metrics and hashes</a> · <a href="protocol.json">Frozen protocol</a> · <a href="generation.json">Fresh seeds and dataset receipts</a> · <a href="submissions.json">Model submissions / write attempts</a> · <a href="high/summary.json">Harness workflow</a></p>
    <p>Per-policy evidence: <a href="policy-a/rules.json">A rules</a> · <a href="policy-b/rules.json">B rules</a> · <a href="policy-c/rules.json">C rules</a>. Source, services and rollback setup remain retained; CPU training does not use inference GPUs.</p></html>'''
    (root/'report.html').write_text(content)

def experiment(root,config):
    root=root.resolve();root.mkdir(parents=True,exist_ok=False)
    state={'status':'freezing','started':time.time(),'heldout_evaluated':False}
    def receipt():save(root/'experiment.json',state)
    receipt();training=None;log=None
    try:
        frozen=freeze_runtime(config,root/'runtime');private=root/'fleet.frozen.private.json';save(private,frozen);private.chmod(0o600)
        save(root/'protocol.json',{'schema':1,'frozen_at':time.time(),'runtime_sha256':frozen['runtime_snapshot_sha256'],
                                 'rules':'12 clauses of 3 memberships, 3 count groups, 2 two-membership vetoes, require 2 groups',
                                 'policies':3,'worker_slots':2,'thinking':'on','reasoning_effort':'high','output_ceiling':config['models']['blackfold']['max_tokens'],
                                 'uniform_test_per_policy':10000,'group_boundary_per_policy':1000,'veto_boundary_per_policy':1000,
                                 'trained_budgets':[512,8192],'trained_seeds':[11,29,47],'trained_epochs':300,
                                 'rule_informed_comparator':'trusted deterministic NN compiler','labeled_feedback':False,'revisions':0})
        state['status']='generating-fresh-policies';receipt();fixtures=prepare(root,frozen)
        log=(root/'training-progress.log').open('w')
        training=subprocess.Popen([sys.executable,str(snapshot_inputs(frozen)/'rules_experiment.py'),'train-batch','--output',str(root)],stdout=log,stderr=subprocess.STDOUT,env=training_environment())
        routed=copy.deepcopy(frozen);routed['fixture_input']=fixtures
        state['status']='high-effort-harness';receipt();arm=root/'high'
        run(routed,'blackfold','blackfold',2,[],arm,4800,'on','rules-v1','high',3)
        summary=summarize(arm);save(arm/'summary.json',summary)
        if not summary['passed']:raise RuntimeError('Harness workflow incomplete; no automatic retry or test-based revision')
        submissions=archive(root,arm);state.update(status='waiting-for-cpu-baselines',artifacts_frozen_at=time.time());receipt()
        if training.wait()!=0:raise RuntimeError('CPU training failed; inspect saved log')
        state['status']='scoring-frozen-artifacts';receipt();score(root,submissions);report(root)
        state.update(status='finished',heldout_evaluated=True)
    except BaseException as e:state.update(status='stopped',error=str(e));raise
    finally:
        if training and training.poll() is None:training.terminate();training.wait()
        if log:log.close()
        state['finished']=time.time();receipt()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','train-batch','report']);p.add_argument('--output',type=Path,required=True);p.add_argument('--config',type=Path);a=p.parse_args()
    if a.action=='run':
        if not a.config:p.error('--config required')
        experiment(a.output,json.loads(a.config.read_text()))
    elif a.action=='train-batch':train_batch(a.output)
    else:report(a.output)
