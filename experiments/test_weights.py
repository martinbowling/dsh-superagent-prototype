"""Evaluator qualification, not model-visible feedback or benchmark accuracy."""
import copy
import json
import random
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path
from weight_spec import PARAMETERS, parse, logits, structural_result
from weights_data import complete, valid, make_splits, digest
from handweights import archive, revision_fixture, label_coverage

def reference():
    # Analyst-authored triangular-wave proof. NEVER supplied to the model.
    coeff=[1 if i%2==0 else 3 for i in range(13)]
    first=[{'index':i,'bias':-5*i,'digit_coefficients':coeff} for i in range(45)]
    weights=[[0,-2]]+[[i,4 if i%2 else -4] for i in range(1,45)]
    return {'schema':1,'first':first,'second':[{'index':0,'bias':1,'weights':weights},{'index':1,'bias':-1,'weights':[[i,-w] for i,w in weights]}],
            'output':{'weights':[[0,8],[1,-8]],'bias':0},'notes':'Trusted evaluator qualification only, not a contestant.'}

class WeightTests(unittest.TestCase):
    def test_reference_all_attainable_weighted_sums(self):
        layers=parse(json.dumps(reference()))
        # Dynamic programming constructs one input for EVERY attainable sum;
        # this checks dense-function behavior beyond random samples.
        values={0:''}
        for p in range(13):
            w=1 if p%2==0 else 3
            values={s+w*d:prefix+str(d) for s,prefix in values.items() for d in range(10)}
        self.assertEqual(set(values),set(range(226)))
        for s,digits in values.items():self.assertEqual(logits(layers,digits)>=0,s%10==0)
        self.assertEqual(PARAMETERS,99969)

    def test_parser_rejects_malformed_or_executable_data(self):
        original=reference()
        variants=[]
        for field,value in [('schema',True),('notes',{}),('first','exec(...)')]:
            item=copy.deepcopy(original);item[field]=value;variants.append(item)
        for mutation in ('duplicate','bounds','nan','boolean','extra','pairduplicate','bothforms'):
            item=copy.deepcopy(original)
            if mutation=='duplicate':item['first'].append(item['first'][0])
            elif mutation=='bounds':item['first'][0]['index']=512
            elif mutation=='nan':item['output']['bias']=float('nan')
            elif mutation=='boolean':item['output']['bias']=True
            elif mutation=='extra':item['program']='import os'
            elif mutation=='pairduplicate':item['output']['weights']=[[0,1],[0,2]]
            else:item['first'][0]['weights']=[]
            variants.append(item)
        for value in variants:self.assertFalse(structural_result(json.dumps(value))['passed'])
        self.assertFalse(structural_result('{"schema":1,"schema":1}')['passed'])
        self.assertFalse(structural_result(' '*64001)['passed'])

    def test_known_standard_arithmetic(self):
        self.assertTrue(valid('9780306406157'))
        self.assertFalse(valid('9780306406158'))
        self.assertEqual(complete('978030640615'),'9780306406157')
        self.assertFalse(valid('978０306406157'))

    def test_splits_disjoint_balanced_deterministic(self):
        train,test=make_splits()
        self.assertEqual((len(train),len(test)),(8192,16000))
        self.assertEqual(len({r[0] for r in test}),16000)
        self.assertFalse({s[:12] for s,_,_ in train}&{s[:12] for s,_,_ in test})
        for f in {r[2] for r in test}:
            part=[r for r in test if r[2]==f];self.assertEqual(sum(r[1] for r in part),len(part)//2)
        self.assertEqual(digest(test),digest(make_splits()[1]))

    def test_arena_feedback_is_label_blind(self):
        # An entirely zero network predicts every input positive. It MUST pass
        # structural validation just like a correct classifier, with no score.
        zero={'schema':1,'first':[],'second':[],'output':{'weights':[],'bias':0},'notes':''}
        root=Path(__file__).resolve().parent
        process=subprocess.run([sys.executable,str(root/'check.py'),'--suite','weights-v1','--task','isbn13'],input=json.dumps({'files':{'weights.json':json.dumps(zero)}}),text=True,capture_output=True,check=True)
        result=json.loads(process.stdout);self.assertTrue(result['passed'])
        self.assertEqual(result['nonzero_parameters'],0)
        self.assertNotIn('accuracy',result);self.assertNotIn('labels',result)
        self.assertNotIn('weights_data', (root/'weight_spec.py').read_text())

    def test_revision_contains_only_the_allowed_training_labels(self):
        original=json.loads((Path(__file__).parent/'tasks-weights-v1.json').read_text())[0]
        train,test=make_splits();revised=revision_fixture(original,'prior',train)
        csv=revised['files']['labeled.csv'];self.assertEqual(len(csv.splitlines()),5063)
        supplied={line.split(',')[0] for line in csv.splitlines()[1:]}
        self.assertFalse(supplied & {s for s,_,_ in test})
        self.assertEqual(revised['files']['prior.weights.json'],'prior')
        self.assertNotIn('zero-labeled-example mathematical',revised['prompt'])

    def test_archive_maps_fixture_evidence_and_refuses_changed_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);arm=root/'arm';work=arm/'state/work/ticket';work.mkdir(parents=True)
            (work/'weights.json').write_text('original')
            (arm/'tickets.json').write_text(json.dumps({'items':[{'id':'ticket','sessionId':'worker-ticket','directory':str(work)}]}))
            (arm/'fixture-plan.json').write_text(json.dumps([{'id':'isbn13--00'}]))
            events=[{'sessionId':'worker-ticket','event':{'type':'tool/result','data':{'message':{'content':[{'type':'text','text':json.dumps({'fixture':'isbn13--00'})}]}}}},
                    {'sessionId':'worker-ticket','event':{'type':'tool/call','data':{'name':'arena_write'}}}]
            (arm/'state/arena-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            value=archive(arm,root,'zero-label',0)[0];self.assertEqual(value['name'],'zero-00');self.assertEqual(value['write_attempts'],1)
            (work/'weights.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'changed'):archive(arm,root,'zero-label',0)

    def test_label_coverage_requires_every_character_and_checks_page_contents(self):
        with tempfile.TemporaryDirectory() as d:
            arm=Path(d);(arm/'state').mkdir();path=arm/'state/arena-events.jsonl'
            def events(pages):
                rows=[]
                for i,value in enumerate(pages):
                    rows.append({'sessionId':'worker-x','event':{'type':'tool/call','data':{'name':'arena_read','callId':str(i)}}})
                    rows.append({'sessionId':'worker-x','event':{'type':'tool/result','data':{'message':{'toolCallId':str(i),'content':[{'type':'text','text':json.dumps(value)}]}}}})
                path.write_text(''.join(json.dumps(v)+'\n' for v in rows))
            pages=[{'path':'labeled.csv','offset':0,'content':'abc'},{'path':'labeled.csv','offset':4,'content':'ef'}]
            events(pages);self.assertFalse(label_coverage(arm,'abcdef')['all_delivered'])
            pages.append({'path':'labeled.csv','offset':2,'content':'cd'});events(pages);self.assertTrue(label_coverage(arm,'abcdef')['all_delivered'])
            pages.append({'path':'labeled.csv','offset':0,'content':'wrong'});events(pages)
            with self.assertRaisesRegex(ValueError,'differs'):label_coverage(arm,'abcdef')

if __name__=='__main__':unittest.main()
