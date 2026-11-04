"""Trusted CPU training/evaluation for the label-blind hand-written weight arena.

Run this outside the agent workspace. Test labels never enter fixtures or arena_test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from weights_data import make_splits, digest
from weight_spec import parse, stats, SHAPES

def save(path, data): path.write_text(json.dumps(data, indent=2) + '\n')

def prepare(root):
    root.mkdir(exist_ok=False, parents=True)
    train, test = make_splits()
    save(root/'train.json', train); save(root/'heldout.json', test)
    save(root/'data-receipt.json', {'train_count':len(train),'test_count':len(test),
        'train_sha256':digest(train),'test_sha256':digest(test),
        'test_strata':{f:sum(r[2]==f for r in test) for f in sorted({r[2] for r in test})},
        'disjoint_12_digit_stems':True,'label_definition':'ISBN-13 alternating 1/3 sum divisible by 10; no existence claim',
        'source':'https://www.isbn-international.org/index.php/node/10'})

def features(rows):
    import torch
    digits=torch.tensor([[int(d) for d in s] for s,_,_ in rows],dtype=torch.long)
    return torch.nn.functional.one_hot(digits,10).float().flatten(1), torch.tensor([y for _,y,_ in rows],dtype=torch.float32).reshape(-1,1)

def network():
    import torch
    return torch.nn.Sequential(torch.nn.Linear(130,512),torch.nn.ReLU(),torch.nn.Linear(512,64),torch.nn.ReLU(),torch.nn.Linear(64,1))

def materialize(text):
    import torch
    layers=parse(text); model=network()
    with torch.no_grad():
        for layer,target in zip(layers,(model[0],model[2],model[4])):
            target.weight.zero_();target.bias.zero_()
            for k,(weights,bias) in layer.items():
                for j,w in weights.items():target.weight[k,j]=w
                target.bias[k]=bias
    declared=stats(layers)
    return model,{**declared,'declared_nonzero_parameters':declared['nonzero_parameters'],
                  'nonzero_parameters':sum(torch.count_nonzero(p).item() for p in model.parameters()),'inference_dtype':'float32'}

def metrics(predictions, rows):
    def score(indices):
        tp=sum(predictions[i] and rows[i][1] for i in indices)
        tn=sum(not predictions[i] and not rows[i][1] for i in indices)
        fp=sum(predictions[i] and not rows[i][1] for i in indices)
        fn=sum(not predictions[i] and rows[i][1] for i in indices)
        return {'n':len(indices),'accuracy':(tp+tn)/len(indices),'tp':int(tp),'tn':int(tn),'fp':int(fp),'fn':int(fn),
                'balanced_accuracy':(tp/(tp+fn)+tn/(tn+fp))/2,
                'precision':tp/(tp+fp) if tp+fp else 0.,'recall':tp/(tp+fn),
                'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.}
    return {'overall':score(list(range(len(rows)))), 'strata':{f:score([i for i,r in enumerate(rows) if r[2]==f]) for f in sorted({r[2] for r in rows})}}

def evaluate(model, rows):
    import torch
    x,y=features(rows)
    with torch.no_grad():logits=model(x).flatten()
    if not torch.isfinite(logits).all():raise ValueError('Nonfinite logits')
    return metrics((logits>=0).tolist(),rows)

def train(root, epochs=300, seeds=(11,29,47)):
    import torch
    torch.set_num_threads(4);torch.use_deterministic_algorithms(True)
    rows=json.loads((root/'train.json').read_text());out=root/'trained';out.mkdir(exist_ok=True)
    results=[]
    for n in (512,5062,8192):
        # Nested label budgets. Split by 12-digit stem, so paired positive/negative
        # variants cannot leak from fitting into checkpoint selection.
        subset=rows[:n]; stems=sorted({s[:12] for s,_,_ in subset})
        validation_stems=set(stems[::5]); fit=[r for r in subset if r[0][:12] not in validation_stems];val=[r for r in subset if r[0][:12] in validation_stems]
        x,y=features(fit);vx,vy=features(val)
        for seed in seeds:
            path=out/f'n{n}-s{seed}.pt'
            if path.exists():raise FileExistsError('Refusing to overwrite trained weights')
            start=time.time();torch.manual_seed(seed);model=network();optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
            best=float('inf');chosen=None;best_epoch=0;curve=[]
            for epoch in range(1,epochs+1):
                model.train();order=torch.randperm(len(x));loss_sum=0.
                for ids in order.split(256):
                    optimizer.zero_grad(set_to_none=True);loss=torch.nn.functional.binary_cross_entropy_with_logits(model(x[ids]),y[ids]);loss.backward();optimizer.step();loss_sum+=loss.item()*len(ids)
                model.eval()
                with torch.no_grad(): vl=torch.nn.functional.binary_cross_entropy_with_logits(model(vx),vy).item();acc=((model(vx)>=0)==vy.bool()).float().mean().item()
                curve.append({'epoch':epoch,'fit_loss':loss_sum/len(x),'validation_loss':vl,'validation_accuracy':acc})
                if vl < best:
                    best=vl;best_epoch=epoch;chosen={k:v.detach().clone() for k,v in model.state_dict().items()}
            model.load_state_dict(chosen);torch.save(chosen,path)
            result={'name':f'trained-{n}-seed-{seed}','kind':'trained','labels_seen':n,'fit_labels':len(fit),'validation_labels':len(val),'seed':seed,
                    'allocated_parameters':sum(p.numel() for p in model.parameters()),'nonzero_parameters':sum(torch.count_nonzero(p).item() for p in model.parameters()),
                    'epochs_run':epochs,'selected_epoch':best_epoch,'selection':'minimum internal validation BCE, stem-disjoint, final heldout never consulted',
                    'optimizer':'AdamW lr=0.001 weight_decay=0.01 batch=256','wall_s':time.time()-start,'artifact':str(path),
                    'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'curve':curve}
            save(out/f'n{n}-s{seed}.json',result);results.append(result)
            print(json.dumps({k:result[k] for k in ('name','selected_epoch','wall_s')}),flush=True)
    save(root/'training.json',{'torch':torch.__version__,'device':'cpu','threads':4,'runs':results})

def score(root, submissions):
    import torch
    if (root/'comparison.json').exists():raise FileExistsError('Final test already evaluated; do not tune against it')
    torch.set_num_threads(4)
    heldout=json.loads((root/'heldout.json').read_text());results=[]
    receipt=json.loads((root/'data-receipt.json').read_text())
    if digest(heldout)!=receipt['test_sha256']:raise ValueError('Held-out dataset changed')
    for submission in submissions:
        text=Path(submission['artifact']).read_text()
        if hashlib.sha256(text.encode()).hexdigest()!=submission['sha256']:raise ValueError('Frozen submission changed')
        model,counts=materialize(text)
        results.append({**submission,**counts,'sha256':hashlib.sha256(text.encode()).hexdigest(),'metrics':evaluate(model,heldout)})
    for metadata in sorted((root/'trained').glob('*.json')):
        value=json.loads(metadata.read_text())
        if hashlib.sha256(Path(value['artifact']).read_bytes()).hexdigest()!=value['sha256']:raise ValueError('Trained weights changed')
        model=network();model.load_state_dict(torch.load(value['artifact'],weights_only=True,map_location='cpu'))
        results.append({k:v for k,v in value.items() if k!='curve'}|{'metrics':evaluate(model,heldout)})
    save(root/'comparison.json',{'schema':1,'data':json.loads((root/'data-receipt.json').read_text()),'results':results,
        'oracle_accuracy':1.,'constant_baseline_accuracy':.5,'limitations':['Known checksum algorithm, not legal citation authenticity or book existence.','The hand-written arm has algorithmic prior knowledge; the trained arm gets labeled examples and the same raw inputs.','One task, two zero-label proposals, one revision; no claim about neural networks generally.','Structural arena success is not classification accuracy.']})
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','train','score']);p.add_argument('root',type=Path);p.add_argument('--submissions',type=Path);p.add_argument('--epochs',type=int,default=300);a=p.parse_args()
    if a.action=='prepare':prepare(a.root)
    elif a.action=='train':train(a.root,a.epochs)
    else:score(a.root,json.loads(a.submissions.read_text()))
