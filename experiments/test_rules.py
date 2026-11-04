"""Rule semantics, control compiler and hidden-test construction qualification."""
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import unittest
import tempfile
from rules_spec import policy, compare, logical, oracle, compile_weights, datasets, fixture
from weight_spec import parse, logits
from rules_experiment import training_environment

class RuleTests(unittest.TestCase):
    def test_training_helper_does_not_mutate_imported_frozen_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);module=root/'frozen_example.py';module.write_text('VALUE=17\n');module.chmod(0o444)
            subprocess.run([sys.executable,'-c','import frozen_example; assert frozen_example.VALUE==17'],cwd=root,env=training_environment(),check=True)
            self.assertEqual([p.name for p in root.iterdir()],['frozen_example.py'])
    def test_fresh_instance_generation_is_reproducible_and_distinct(self):
        a=policy(117);self.assertEqual(a,policy(117));self.assertNotEqual(a,policy(118))
        self.assertEqual(len(a['clauses']),12);self.assertEqual(len(a['vetoes']),2)
        self.assertEqual({g['operator'] for g in a['groups']},{'at_least','at_most','exactly'})
        for c in a['clauses']+a['vetoes']:self.assertEqual(len({t['position'] for t in c['all']}),len(c['all']))

    def test_threshold_boundaries_and_veto_precedence(self):
        for op,expected in [('at_least',[False,True,True]),('exactly',[False,True,False]),('at_most',[True,True,False])]:
            self.assertEqual([compare(i,op,1) for i in range(3)],expected)
        r={'clauses':[{'id':'c','all':[{'position':0,'digits':[1]}]}],
           'vetoes':[{'id':'v','all':[{'position':1,'digits':[2]}]}],
           'groups':[{'id':'g'+str(i),'clauses':['c'],'operator':op,'count':k} for i,(op,k) in enumerate([('at_least',1),('exactly',1),('at_most',0)])],
           'required_groups':2}
        layers=parse(json.dumps(compile_weights(r)))
        for x,y in itertools.product(range(10),repeat=2):
            text=str(x)+str(y)+'0'*11;expected=int(x==1 and y!=2)
            self.assertEqual(oracle(r,text)[0],expected)
            self.assertEqual(logits(layers,text)>=0,bool(expected))

    def test_compiler_matches_oracle_on_raw_random_digits(self):
        rng=random.Random(7474)
        for seed in (117,118):
            rule=policy(seed);layers=parse(json.dumps(compile_weights(rule)))
            for _ in range(300):
                text=''.join(str(rng.randrange(10)) for _ in range(13))
                self.assertEqual(logits(layers,text)>=0,bool(oracle(rule,text)[0]))

    def test_compiler_composition_covers_every_abstract_clause_state(self):
        rule=policy(118);layers=parse(json.dumps(compile_weights(rule)));names=[c['id'] for c in rule['clauses']+rule['vetoes']]
        for bits in itertools.product((0,1),repeat=14):
            hidden={i:v for i,v in enumerate(bits)}
            for n,layer in enumerate(layers[1:]):
                hidden={k:(max(0.,sum(w*hidden.get(j,0.) for j,w in weights.items())+bias) if n==0 else sum(w*hidden.get(j,0.) for j,w in weights.items())+bias) for k,(weights,bias) in layer.items()}
            self.assertEqual(hidden[0]>=0,bool(logical(rule,dict(zip(names,bits)))[0]))

    def test_boundary_pairs_are_isolated_and_no_train_stem_leaks(self):
        rule=policy(117);train,test=datasets(rule,1901,training_count=32,uniform_count=40,boundary_count=20)
        self.assertEqual((len(train),len(test)),(32,80))
        self.assertFalse({s[:12] for s,_,_ in train}&{s[:12] for s,_,_ in test})
        self.assertEqual(len({s for s,_,_ in test}),80)
        for family in ('group_boundary','veto_boundary'):
            part=[r for r in test if r[2]==family]
            for a,b in zip(part[::2],part[1::2]):
                self.assertNotEqual(a[1],b[1]);self.assertEqual(sum(x!=y for x,y in zip(a[0],b[0])),1)
                left=oracle(rule,a[0]);right=oracle(rule,b[0])
                if family=='veto_boundary':self.assertEqual(left[1],right[1]);self.assertNotEqual(left[2],right[2])
                else:self.assertEqual(left[2],False);self.assertEqual(right[2],False)

    def test_model_fixture_has_only_rules_and_structural_feedback(self):
        task=fixture(policy(117),'a');self.assertEqual(set(task['files']),{'rules.json','weights.json'})
        self.assertNotIn('compiler.weights.json',json.dumps(task))
        zero={'schema':1,'first':[],'second':[],'output':{'weights':[],'bias':0},'notes':''}
        result=subprocess.run([sys.executable,str(Path(__file__).parent/'check.py'),'--suite','rules-v1','--task','policy13'],input=json.dumps({'files':{'weights.json':json.dumps(zero)}}),text=True,capture_output=True,check=True)
        value=json.loads(result.stdout);self.assertTrue(value['passed']);self.assertNotIn('accuracy',value)

if __name__=='__main__':unittest.main()
