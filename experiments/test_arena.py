import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import io
from summarize import summarize
from arena import overlay, request, ManagementError
from check import bootstrap
import ast

class ArenaEvidenceTests(unittest.TestCase):
    def fixture(self, root, truncated=False, usage=True):
        (root/'state').mkdir()
        (root/'manifest.json').write_text(json.dumps({'id':'trial','status':'finished','scope':'local fixtures',
            'workers':1,'fixture_sha256':'fixed','wall_s':10,'expected_tickets':1}))
        (root/'tickets.json').write_text(json.dumps({'items':[{'id':'x','state':'ended','ended':{'outcome':'completed'},'sessionId':'worker-x'}]}))
        data={'message':{}}
        if usage:data['usage']={'inputTokens':100,'outputTokens':200}
        rows=[{'sessionId':'worker-x','event':{'type':'assistant/message','data':data}},
              {'sessionId':'worker-x','event':{'type':'turn/end','data':{'reason':{'kind':'max-tokens' if truncated else 'completed'}}}},
              {'sessionId':'worker-x','event':{'type':'tool/result','data':{'message':{'content':[{'type':'text','text':json.dumps({'passed':True,'passed_cases':11,'total_cases':11})}]}}}}]
        (root/'state/arena-events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
        calls=[{'id':1,'phase':'start','at':1000,'active':1},{'id':1,'phase':'first_delta','at':1500},{'id':1,'phase':'end','at':2500,'active':0,'completed':True}]
        (root/'state/arena-calls.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in calls))
    def test_counts_usage_and_verified_completion(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);r=summarize(root)
            self.assertTrue(r['passed']);self.assertEqual(r['all_role_output_tokens_per_wall_second'],20)
            self.assertEqual(r['first_delta_median_s'],.5)
    def test_truncation_is_incomplete_even_with_passing_tests(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,truncated=True);self.assertFalse(summarize(root)['passed'])
    def test_missing_usage_never_becomes_a_throughput_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,usage=False);r=summarize(root)
            self.assertFalse(r['usage_complete']);self.assertIsNone(r['all_role_output_tokens_per_wall_second'])
    def test_interrupted_request_never_becomes_a_throughput_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root)
            p=root/'state/arena-calls.jsonl'
            p.write_text(p.read_text().replace('"completed": true','"completed": false'))
            self.assertIsNone(summarize(root)['all_role_output_tokens_per_wall_second'])
    def test_bootstrap_preserves_source_containing_checker_like_text(self):
        source='text = "exec(CHECKS) and exec(compile(SOURCE"\n'
        tree=ast.parse(bootstrap('intervals',source))
        self.assertEqual(ast.literal_eval(tree.body[0].value),source)
    def test_local_overlay_disables_external_sources_and_preserves_preset_identity(self):
        c={'models':{'m':{'base_url':'http://127.0.0.1:1234/v1','model':'fixture','context_window':32768,'max_tokens':4096}}}
        rows={x['id']:x for x in overlay(c,Path('/tmp/arena-state'),'m')}
        self.assertTrue(rows['dsh-superagent-github']['disabled'])
        self.assertTrue(rows['dsh-superagent']['config']['localFixtures'])
        self.assertEqual(rows['dsh-superagent-worker-preset']['config']['id'],'dsh-superagent-worker')
    def test_startup_404_preserves_status_for_bounded_readiness_retry(self):
        failure=HTTPError('http://127.0.0.1/state',404,'Not ready',{},io.BytesIO(b'{}'))
        with patch('arena.urllib.request.urlopen',side_effect=failure):
            with self.assertRaises(ManagementError) as ctx:request('http://127.0.0.1:3086','state')
        self.assertEqual(ctx.exception.status,404)
        self.assertIn('GET state',str(ctx.exception))

if __name__=='__main__':unittest.main()
