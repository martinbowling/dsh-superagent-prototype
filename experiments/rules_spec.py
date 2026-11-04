"""Fresh finite policy programs, an independent Boolean oracle, and a NN compiler.

Only the policy/spec is model-visible. Compiler weights and datasets are private.
"""
import itertools
import json
import random
from weight_spec import parse, stats
from weights_data import digest

def policy(seed):
    rng=random.Random(seed)
    def clause(name,size,width):
        return {'id':name,'all':[{'position':p,'digits':sorted(rng.sample(range(10),width))} for p in rng.sample(range(13),size)]}
    clauses=[clause(f'c{i:02d}',3,5) for i in range(12)]
    vetoes=[clause(f'v{i:02d}',2,3) for i in range(2)]
    operators=[('at_least',2),('exactly',1),('at_most',1)];rng.shuffle(operators)
    groups=[{'id':f'g{i}','clauses':rng.sample([c['id'] for c in clauses],5),'operator':op,'count':k} for i,(op,k) in enumerate(operators)]
    return {'schema':1,'clauses':clauses,'groups':groups,'vetoes':vetoes,'required_groups':2}

def compare(count,operator,k):
    if operator=='at_least':return count>=k
    if operator=='exactly':return count==k
    if operator=='at_most':return count<=k
    raise ValueError('Unknown operator')

def logical(rule,bits):
    groups=[compare(sum(bits[c] for c in g['clauses']),g['operator'],g['count']) for g in rule['groups']]
    veto=any(bits[v['id']] for v in rule['vetoes'])
    return int(sum(groups)>=rule['required_groups'] and not veto),tuple(groups),veto

def oracle(rule,text):
    if len(text)!=13 or any(c not in '0123456789' for c in text):raise ValueError('Expected 13 ASCII digits')
    digits=[int(c) for c in text]
    bits={c['id']:all(digits[t['position']] in t['digits'] for t in c['all']) for c in rule['clauses']+rule['vetoes']}
    return logical(rule,bits)

def compile_weights(rule):
    """Deterministic comparator with exactly the same policy knowledge as the LLM."""
    first=[];ids={}
    for clause in rule['clauses']+rule['vetoes']:
        ids[clause['id']]=len(first)
        first.append({'index':len(first),'bias':1-len(clause['all']),
                      'weights':[[10*t['position']+d,1] for t in clause['all'] for d in t['digits']]})
    second=[];output=[];bias=.5-rule['required_groups']
    def hinge(weights,threshold,coefficient):
        i=len(second);second.append({'index':i,'bias':-threshold,'weights':weights});output.append([i,coefficient])
    for group in rule['groups']:
        w=[[ids[c],1] for c in group['clauses']];k=group['count']
        if group['operator']=='at_least':hinge(w,k-1,1);hinge(w,k,-1)
        elif group['operator']=='exactly':hinge(w,k-1,1);hinge(w,k,-2);hinge(w,k+1,1)
        elif group['operator']=='at_most':bias+=1;hinge(w,k,-1);hinge(w,k+1,1)
        else:raise ValueError('Unknown operator')
    for veto in rule['vetoes']:hinge([[ids[veto['id']],1]],0,-(len(rule['groups'])+1))
    value={'schema':1,'first':first,'second':second,'output':{'weights':output,'bias':bias},'notes':'Trusted deterministic rule compiler, excluded from model inputs.'}
    parse(json.dumps(value));return value

def qualify(rule):
    """Check all abstract clause assignments, plus every digit tuple per clause."""
    import torch
    torch.set_num_threads(4)
    from weight_lab import materialize,features
    model,_=materialize(json.dumps(compile_weights(rule)))
    names=[c['id'] for c in rule['clauses']+rule['vetoes']]
    assignments=list(itertools.product((0,1),repeat=len(names)))
    hidden=torch.zeros(len(assignments),512);hidden[:,:len(names)]=torch.tensor(assignments,dtype=torch.float32)
    expected=torch.tensor([logical(rule,dict(zip(names,a)))[0] for a in assignments],dtype=torch.bool)
    with torch.no_grad():actual=(model[2:](hidden).flatten()>=0)
    if not torch.equal(actual,expected):raise AssertionError('Compiler composition failed')
    cases=0
    for i,c in enumerate(rule['clauses']+rule['vetoes']):
        rows=[];truth=[]
        for values in itertools.product(range(10),repeat=len(c['all'])):
            digits=['0']*13
            for t,d in zip(c['all'],values):digits[t['position']]=str(d)
            rows.append((''.join(digits),0,'qualification'));truth.append(int(all(d in t['digits'] for t,d in zip(c['all'],values))))
        x,_=features(rows)
        with torch.no_grad():observed=torch.relu(model[0](x))[:,i]
        if not torch.equal(observed,torch.tensor(truth,dtype=torch.float32)):raise AssertionError('Compiler literal stage failed')
        cases+=len(rows)
    return {'abstract_assignments':len(assignments),'literal_combinations':cases,'passed':True}

def datasets(rule,seed,training_count=8192,uniform_count=10000,boundary_count=1000):
    if min(training_count,uniform_count,boundary_count)<1 or boundary_count%2:raise ValueError('Positive counts and even boundary count required')
    rng=random.Random(seed);train=[];seen=set();train_stems=set()
    def draw():return ''.join(str(rng.randrange(10)) for _ in range(13))
    while len(train)<training_count:
        text=draw()
        if text[:12] in train_stems:continue
        train_stems.add(text[:12]);seen.add(text);train.append((text,oracle(rule,text)[0],'uniform'))
    test=[];stems=set(train_stems)
    while len(test)<uniform_count:
        text=draw()
        if text[:12] in stems:continue
        stems.add(text[:12]);seen.add(text);test.append((text,oracle(rule,text)[0],'uniform'))
    for family in ('group_boundary','veto_boundary'):
        count=0;attempts=0
        while count<boundary_count:
            attempts+=1
            if attempts>100000:raise RuntimeError('Boundary generation cap reached; preserve this policy, no seed retry')
            text=draw()
            if text[:12] in stems:continue
            base=oracle(rule,text);positions=list(range(13));rng.shuffle(positions);found=None
            for p in positions:
                values=list(range(10));rng.shuffle(values)
                for d in values:
                    if str(d)==text[p]:continue
                    neighbor=text[:p]+str(d)+text[p+1:]
                    if neighbor[:12] in stems or neighbor in seen:continue
                    changed=oracle(rule,neighbor)
                    if base[0]==changed[0]:continue
                    group=base[2]==changed[2]==False
                    veto=base[1]==changed[1] and base[2]!=changed[2]
                    if (family=='group_boundary' and group) or (family=='veto_boundary' and veto):found=(neighbor,changed);break
                if found:break
            if not found:continue
            neighbor,changed=found
            test.extend(((text,base[0],family),(neighbor,changed[0],family)))
            seen.update((text,neighbor));stems.update((text[:12],neighbor[:12]));count+=2
    assert not train_stems & {s[:12] for s,_,_ in test}
    return train,test

def fixture(rule,tag):
    schema='''Only numeric JSON weights.json is accepted. Schema exactly {"schema":1,"first":[{"index":0..511,"bias":number,"weights":[[input_index 0..129,number],...]}...],"second":[{"index":0..63,"bias":number,"weights":[[first_index 0..511,number],...]}...],"output":{"weights":[[second_index 0..63,number],...],"bias":number},"notes":"mathematical explanation <=8000 characters"}. Unlisted weights/biases/neurons are zero. No duplicate indices or keys, no booleans/nonfinite values, magnitude <=1e6, total file <=64000 UTF-8 bytes. Full neuron rows, not expressions or a generator. The alternative first-row digit_coefficients form exists but is unnecessary here.'''
    prompt='''Construct the actual numeric weights of a fixed neural network implementing the fresh random policy in rules.json. This is algorithm-to-weights compilation, zero labeled examples. No code execution, training, external services, tables of example predictions, or executable weight generators. Only the supplied policy and numeric weights may be used. Do not assume ISBN rules or any meaning for the digit string.
Input: any 13 ASCII digits, including leading zero. Raw input is 130 one-hot coordinates, index=position*10+digit, zero-based positions 0..12. Fixed float32 NN: Linear(130,512), ReLU, Linear(512,64), ReLU, Linear(64,1). Exactly 99,969 allocated parameters. Output logit >=0 means positive, <0 negative. No other features/computation occur at inference; prefer a nonzero margin.
Policy semantics: a clause is true iff ALL its position-membership tests are true. Each group counts how many referenced clause ids are true, then applies its operator (at_least means >= count, exactly means == count, at_most means <= count). Positive iff at least required_groups groups are true AND NONE of the veto clauses is true. Vetoes use the same ALL-of-memberships semantics. Repeated group references across groups count independently in each group. Exact 0/1 Boolean semantics, with no short-circuit feature hidden outside the NN. Read the complete rules.json before planning your indexing. Give your own neuron organization and proof in notes. No reference/compiler weights or labeled rows are provided.
Use arena_read, arena_write(path="weights.json",content=full JSON), then arena_test. Large reads return catalogs; use arena_read(path=...,offset=0,limit=4000) and follow nextOffset until null if needed. arena_test ONLY checks schema and finite inference; it never evaluates correctness, labels, or accuracy. Syntax repairs are allowed and counted. After structural success report ready and stop writing. Every final proposal will be frozen before any final scoring; do not claim a measured accuracy.
'''+schema
    return {'id':'policy13--'+tag,'prompt':prompt,'files':{'weights.json':'','rules.json':json.dumps(rule,indent=2)},'writableFiles':['weights.json']}
