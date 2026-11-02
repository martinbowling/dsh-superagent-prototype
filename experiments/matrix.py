#!/usr/bin/env python3
"""Fixed-workload repeated runs; model services stay under their existing owners."""
import argparse,hashlib,json,random,time
from pathlib import Path
from arena import run,task_file,ROOT
from summarize import summarize
from snapshot import freeze_runtime,snapshot_inputs,GRADER_FILES


def schedule(counts,repeats,seed):
    order=list(counts);random.Random(seed).shuffle(order)
    return [(repeat+1,count) for repeat in range(repeats) for count in order[repeat%len(order):]+order[:repeat%len(order)]]


def matrix(config,model,counts,repeats,tickets,output,timeout,suite='app-v1',effort='low',seed=17):
    if not counts or len(set(counts))!=len(counts) or any(x not in (1,2,4,8) for x in counts):raise ValueError('Use distinct worker counts from 1,2,4,8')
    if not 1<=repeats<=10 or not max(counts)<=tickets<=64:raise ValueError('Invalid repeats or fixed ticket count')
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    config=freeze_runtime(config,output/'runtime');inputs=snapshot_inputs(config)
    frozen=hashlib.sha256(task_file(suite,inputs).read_bytes()+b''.join((inputs/name).read_bytes() for name in GRADER_FILES)).hexdigest()
    manifest={'schema':1,'model':model,'suite':suite,'effort':effort,'tickets_per_run':tickets,'worker_counts':counts,
      'repeats':repeats,'seed':seed,'started':time.time(),'status':'running','runs':[],'input_bundle_sha256':frozen,'runtime_snapshot_sha256':config['runtime_snapshot_sha256']}
    path=output/'matrix.json'
    try:
        for repeat,count in schedule(counts,repeats,seed):
            current=hashlib.sha256(task_file(suite,inputs).read_bytes()+b''.join((inputs/name).read_bytes() for name in GRADER_FILES)).hexdigest()
            if current!=frozen:raise RuntimeError('Suite or grader changed during matrix; start a new matrix')
            target=output/f'r{repeat:02d}-w{count}'
            path.write_text(json.dumps(manifest,indent=2)+'\n')
            run(config,model,model,count,[],target,timeout,'on',suite,effort,tickets)
            result=summarize(target);(target/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
            manifest['runs'].append({'repeat':repeat,'workers':count,'directory':str(target),'summary':result})
            path.write_text(json.dumps(manifest,indent=2)+'\n')
            print(json.dumps({'repeat':repeat,'workers':count,'passed':result['passed'],'wall_s':result['wall_s']}),flush=True)
            if not result['passed']:raise RuntimeError('Run incomplete or failed; matrix stopped without an automatic retry')
        hashes={x['summary']['fixture_sha256'] for x in manifest['runs']}
        if len(hashes)!=1:raise RuntimeError('Matrix workload drifted')
        manifest['status']='finished'
    except BaseException as e:
        manifest.update(status='stopped',error=str(e));raise
    finally:
        manifest['finished']=time.time();path.write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--model',default='blackfold');p.add_argument('--workers',default='1,2,4')
    p.add_argument('--repeats',type=int,default=3);p.add_argument('--tickets',type=int,default=4);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--timeout',type=int,default=1800);p.add_argument('--suite',choices=['v1','v2','app-v1','app-v2','shared-app-v2'],default='app-v1');p.add_argument('--reasoning-effort',choices=['low','high'],default='low');p.add_argument('--seed',type=int,default=17);a=p.parse_args()
    matrix(json.loads(a.config.read_text()),a.model,[int(x) for x in a.workers.split(',')],a.repeats,a.tickets,a.output,a.timeout,a.suite,a.reasoning_effort,a.seed)
