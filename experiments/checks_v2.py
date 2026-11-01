"""Frozen v2 grading cases. Seeded oracles are independent of model solutions."""

def v2_checks(base):
    checks=dict(base)
    checks['ledger'] += r'''
check('reject newline amount',lambda:rejects(lambda:f([e('x','1.00\n')])) )
check('leading zero amount',lambda:eq(f([e('x','001.20')]),{'a':120}))
for name,amount in [('space',' 1.00'),('exponent','1e2'),('plus','+1'),('nan','NaN'),('infinity','Infinity'),('empty',''),('trailing dot','1.'),('missing whole','.10'),('unicode','١.٠٠')]:
    check('reject '+name,lambda amount=amount:rejects(lambda:f([e('x',amount)])))
check('large exact amount',lambda:eq(f([e('x','999999999999999999999999999999.99')]),{'a':99999999999999999999999999999999}))
check('reject empty id',lambda:rejects(lambda:f([e('')])) )
check('reject empty account',lambda:rejects(lambda:f([e('x',account='')])) )
check('reject null collection',lambda:rejects(lambda:f(None)))
check('reject mapping event',lambda:rejects(lambda:f([None])))
check('conflict after prefix',lambda:rejects(lambda:f([e('x'),e('y'),e('x',account='b')])))
check('debit negative balance',lambda:eq(f([e('x','2.03','debit')]),{'a':-203}))
check('duplicate formatting conflict',lambda:rejects(lambda:f([e('x','1.0'),e('x','1.00')])))
import random
rng=random.Random(1901)
for case in range(20):
    events=[];expected={}
    for j in range(30):
        cents=rng.randrange(100000);account=rng.choice(['a','b','c']);kind=rng.choice(['credit','debit'])
        event=e(str(j),str(cents//100)+'.'+str(cents%100).zfill(2),kind,account)
        events.append(event)
        if j%7==0:events.append(copy.deepcopy(event))
        expected[account]=expected.get(account,0)+(cents if kind=='credit' else -cents)
    check('seeded ledger '+str(case),lambda events=events,expected=expected:eq(f(events),expected))
'''
    checks['intervals'] += r'''
n=10**400
check('large finite integers',lambda:eq(f([(n,n+2),(n+1,n+3)]),[(n,n+3)]))
check('reject mapping pair',lambda:rejects(lambda:f([{1:'a',2:'b'}])))
check('reject set pair',lambda:rejects(lambda:f([{1,2}])))
check('reject null input',lambda:rejects(lambda:f(None)))
check('reject tuple input',lambda:rejects(lambda:f(((1,2),))))
check('reject nan',lambda:rejects(lambda:f([[0,float('nan')]])))
check('reject string endpoint',lambda:rejects(lambda:f([['1',2]])))
check('zero cannot bridge',lambda:eq(f([[1,2],[3,4],[2.5,2.5]]),[(1,2),(3,4)]))
import random
rng=random.Random(2003)
for case in range(30):
    pairs=[]
    for _ in range(20):
        a,b=sorted([rng.randrange(-15,16),rng.randrange(-15,16)]);pairs.append([a,b])
    covered={i for a,b in pairs for i in range(a,b)};expected=[]
    for i in sorted(covered):
        if expected and i==expected[-1][1]:expected[-1]=(expected[-1][0],i+1)
        else:expected.append((i,i+1))
    check('seeded coverage '+str(case),lambda pairs=pairs,expected=expected:eq(f(pairs),expected))
'''
    checks['pagination'] += r'''
check('reject null items',lambda:rejects(lambda:f(None)))
check('reject tuple items',lambda:rejects(lambda:f(tuple(x))))
check('reject mapping items',lambda:rejects(lambda:f({'id':'a'})))
for name,cursor in [('empty',''),('boolean',True),('list',[]),('number',1)]:
    check('reject cursor '+name,lambda cursor=cursor:rejects(lambda:f(x,cursor)))
for name,limit in [('zero',0),('negative',-1),('float',2.0),('string','2'),('null',None)]:
    check('reject limit '+name,lambda limit=limit:rejects(lambda:f(x,limit=limit)))
check('reject missing id',lambda:rejects(lambda:f([{}])))
check('reject boolean id',lambda:rejects(lambda:f([{'id':True}])))
check('reject null id',lambda:rejects(lambda:f([{'id':None}])))
items=[{'id':str(i),'payload':{'v':i}} for i in range(57)]
for size in [1,2,3,7,20,56,57,58,100]:
    def walk(size=size):
        seen=[];cursor=None;prior=set()
        for _ in range(60):
            result=f(items,cursor,size);seen.extend(result['items']);cursor=result['next_cursor']
            if cursor is None:break
            assert cursor not in prior,'cursor cycle';prior.add(cursor)
        else:raise AssertionError('page walk did not end')
        eq(seen,items)
    check('complete page walk '+str(size),walk)
check('validate beyond page',lambda:rejects(lambda:f([{'id':'a'},{'id':'a'}],limit=1)))
'''
    checks['dependencies']=r'''
f=ns['dependency_order']
check('empty',lambda:eq(f({}),[]))
check('lexical independent',lambda:eq(f({'z':[],'a':[],'m':[]}),['a','m','z']))
check('chain',lambda:eq(f({'c':['b'],'b':['a'],'a':[]}),['a','b','c']))
check('new frontier priority',lambda:eq(f({'b':[],'a':['b'],'z':[]}),['b','a','z']))
check('diamond',lambda:eq(f({'d':['b','c'],'c':['a'],'b':['a'],'a':[]}),['a','b','c','d']))
check('cycle',lambda:rejects(lambda:f({'a':['b'],'b':['a']})))
check('disconnected cycle',lambda:rejects(lambda:f({'x':[],'a':['b'],'b':['a']})))
check('self dependency',lambda:rejects(lambda:f({'a':['a']})))
check('unknown dependency',lambda:rejects(lambda:f({'a':['missing']})))
check('duplicate dependency',lambda:rejects(lambda:f({'a':[],'b':['a','a']})))
for label,value in [('null',None),('list',[]),('string','a')]:
    check('reject jobs '+label,lambda value=value:rejects(lambda:f(value)))
for label,value in [('empty',''),('integer',1),('boolean',True)]:
    check('reject job id '+label,lambda value=value:rejects(lambda:f({value:[]})))
for label,value in [('tuple',()),('null',None),('string','a'),('set',set())]:
    check('reject deps '+label,lambda value=value:rejects(lambda:f({'a':value})))
check('reject nonstring dep',lambda:rejects(lambda:f({'a':[],'b':[True]})))
x={'a':[],'c':['a'],'b':[]};before=copy.deepcopy(x)
check('preserves input',lambda:(f(x),eq(x,before)))
import random,itertools
rng=random.Random(2111)
for case in range(24):
    names=['a','b','c','d','e','f'];hidden=names[:];rng.shuffle(hidden)
    jobs={n:[] for n in reversed(names)}
    for j,node in enumerate(hidden):jobs[node]=[dep for dep in hidden[:j] if rng.random()<.35]
    expected=next(list(order) for order in itertools.permutations(names) if all(order.index(dep)<order.index(n) for n,deps in jobs.items() for dep in deps))
    check('seeded DAG '+str(case),lambda jobs=jobs,expected=expected:eq(f(jobs),expected))
'''
    checks['inventory']=r'''
f=ns['apply_batch']
initial={'a':{'stock':10,'reserved':2},'b':{'stock':5,'reserved':0}}
def op(i,kind,qty,sku='a'):return dict(id=i,sku=sku,kind=kind,qty=qty)
check('empty state',lambda:eq(f({},[]),{}))
check('empty batch',lambda:eq(f(initial,[]),initial))
check('restock',lambda:eq(f(initial,[op('1','restock',3)])['a'],{'stock':13,'reserved':2}))
check('reserve',lambda:eq(f(initial,[op('1','reserve',8)])['a'],{'stock':10,'reserved':10}))
check('release',lambda:eq(f(initial,[op('1','release',2)])['a'],{'stock':10,'reserved':0}))
check('ship',lambda:eq(f(initial,[op('1','ship',2)])['a'],{'stock':8,'reserved':0}))
check('ordered batch',lambda:eq(f(initial,[op('1','reserve',3),op('2','ship',4),op('3','restock',2),op('4','release',1)])['a'],{'stock':8,'reserved':0}))
check('idempotent duplicate',lambda:eq(f(initial,[op('1','reserve',2),op('1','reserve',2)])['a'],{'stock':10,'reserved':4}))
check('conflicting duplicate',lambda:rejects(lambda:f(initial,[op('1','reserve',2),op('1','reserve',3)])))
check('unknown sku',lambda:rejects(lambda:f(initial,[op('1','restock',2,'unknown')])))
check('insufficient available',lambda:rejects(lambda:f(initial,[op('1','reserve',9)])))
check('insufficient reserved release',lambda:rejects(lambda:f(initial,[op('1','release',3)])))
check('insufficient reserved ship',lambda:rejects(lambda:f(initial,[op('1','ship',3)])))
for kind in ['invalid','',None,True]:
    check('reject kind '+str(kind),lambda kind=kind:rejects(lambda:f(initial,[op('1',kind,1)])))
for qty in [0,-1,True,1.0,'1',None]:
    check('reject quantity '+repr(qty),lambda qty=qty:rejects(lambda:f(initial,[op('1','restock',qty)])))
check('reject missing op key',lambda:rejects(lambda:f(initial,[{}])))
check('reject extra op key',lambda:rejects(lambda:f(initial,[dict(op('1','restock',1),extra=1)])))
check('reject null operations',lambda:rejects(lambda:f(initial,None)))
check('reject null state',lambda:rejects(lambda:f(None,[])))
for label,value in [('negative',{'stock':-1,'reserved':0}),('overreserved',{'stock':1,'reserved':2}),('bool',{'stock':True,'reserved':0}),('missing',{'stock':1}),('extra',{'stock':1,'reserved':0,'extra':1})]:
    check('reject state '+label,lambda value=value:rejects(lambda:f({'a':value},[])))
x=copy.deepcopy(initial);ops=[op('1','reserve',2)];before=copy.deepcopy((x,ops))
check('preserve success inputs',lambda:(f(x,ops),eq((x,ops),before)))
def atomic_failure():
    x=copy.deepcopy(initial);ops=[op('1','restock',5),op('2','ship',999)];before=copy.deepcopy((x,ops))
    rejects(lambda:f(x,ops));eq((x,ops),before)
check('atomic failure',atomic_failure)
def independent_copy():
    x=copy.deepcopy(initial);result=f(x,[]);result['a']['stock']=999;eq(x,initial)
check('independent nested result',independent_copy)
import random
rng=random.Random(2203)
for case in range(24):
    expected=copy.deepcopy(initial);ops=[]
    for j in range(25):
        sku=rng.choice(['a','b']);row=expected[sku];kind=rng.choice(['restock','reserve','release','ship'])
        room=30 if kind=='restock' else row['stock']-row['reserved'] if kind=='reserve' else row['reserved']
        if room<=0:kind='restock';room=30
        qty=rng.randint(1,room);ops.append(op(str(j),kind,qty,sku))
        if kind=='restock':row['stock']+=qty
        elif kind=='reserve':row['reserved']+=qty
        elif kind=='release':row['reserved']-=qty
        else:row['stock']-=qty;row['reserved']-=qty
        if j%9==0:ops.append(copy.deepcopy(ops[-1]))
    check('seeded transaction '+str(case),lambda ops=ops,expected=expected:eq(f(initial,ops),expected))
'''
    return checks
