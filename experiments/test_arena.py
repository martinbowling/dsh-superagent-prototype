import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import io
from summarize import summarize
from arena import overlay, request, ManagementError, run, harness_port_lock, preflight, with_effort, task_file, reporting_idle, drain_reporting, complete_jsonl
from check import bootstrap, CHECKS, CHECKS_V2
import ast
from reasoning_gateway import closed_think_prefill, off_prefill_gateway
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request

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
    def test_passing_artifacts_do_not_hide_a_reporting_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);p=root/'manifest.json';m=json.loads(p.read_text())
            m.update(status='reporting_timeout',reporting_drained=False);p.write_text(json.dumps(m));r=summarize(root)
            self.assertTrue(r['artifacts_passed']);self.assertFalse(r['workflow_complete']);self.assertFalse(r['passed'])
    def test_drain_waits_for_a_late_request_and_uses_remaining_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);clock=[0.0]
            def write():
                rows=[{'id':1,'phase':'start','at':0},{'id':1,'phase':'end','at':1000}]
                if clock[0]>=1:rows.append({'id':2,'phase':'start','at':1000})
                if clock[0]>=75:rows.append({'id':2,'phase':'end','at':75000})
                (root/'arena-calls.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
            def sleep(seconds):clock[0]+=seconds;write()
            write();result=drain_reporting(root,90,0,lambda:True,monotonic=lambda:clock[0],wall=lambda:clock[0],sleep=sleep)
            self.assertTrue(result);self.assertEqual(clock[0],77)
    def test_drain_respects_total_deadline_and_partial_append(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'arena-calls.jsonl';p.write_text('{"id":1,"phase":"start","at":0}\n{"id":')
            self.assertEqual(len(complete_jsonl(p)),1);clock=[0.0]
            result=drain_reporting(root,3,0,lambda:True,monotonic=lambda:clock[0],wall=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))
            self.assertFalse(result);self.assertEqual(clock[0],3)
    def test_missing_usage_never_becomes_a_throughput_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root,usage=False);r=summarize(root)
            self.assertFalse(r['usage_complete']);self.assertIsNone(r['all_role_output_tokens_per_wall_second'])
    def test_manual_review_is_preserved_when_resummarizing(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);review={'overall_status':'not_scored','code_quality_component':'reviewed'}
            (root/'code-review.json').write_text(json.dumps(review))
            self.assertEqual(summarize(root)['quality_review'],review)
    def test_repairs_do_not_hide_first_attempt_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);p=root/'state/arena-events.jsonl'
            rows=p.read_text().splitlines();bad=json.loads(rows[-1]);value={'passed':False,'passed_cases':5,'total_cases':11}
            bad['event']['time']=4000;last=json.loads(rows[-1]);last['event']['time']=8000;rows[-1]=json.dumps(last)
            m=root/'manifest.json';manifest=json.loads(m.read_text());manifest['work_started']=1;m.write_text(json.dumps(manifest))
            bad['event']['data']['message']['content'][0]['text']=json.dumps(value)
            p.write_text(json.dumps(bad)+'\n'+'\n'.join(rows)+'\n');result=summarize(root)
            self.assertTrue(result['passed']);self.assertEqual(result['objective_test_attempts'],2)
            self.assertEqual(result['first_attempt_passed_tickets'],0)
            self.assertEqual(result['time_to_first_test_s'],3);self.assertEqual(result['time_to_final_passing_tests_s'],7)
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
    def test_thinking_defaults_on_and_can_be_explicitly_disabled(self):
        c={'models':{'m':{'base_url':'http://127.0.0.1:1234/v1','model':'fixture','context_window':32768,'max_tokens':8192}}}
        row=next(x for x in overlay(c,Path('/tmp/state'),'m') if x['id']=='llm-pi-ai')
        self.assertTrue(row['config']['providers']['arena-m']['compat']['chatTemplateKwargs']['enable_thinking'])
        off=next(x for x in overlay(c,Path('/tmp/state'),'m','off') if x['id']=='llm-pi-ai')
        self.assertFalse(off['config']['providers']['arena-m']['compat']['chatTemplateKwargs']['enable_thinking'])
    def test_high_native_effort_preserves_default_config(self):
        c={'models':{'m':{'supported_reasoning_efforts':['low','high'],'thinking_controls':{'on':{'reasoning_effort':'low'}}}}}
        high=with_effort(c,{'m'},'on','high')
        self.assertEqual(high['models']['m']['thinking_controls']['on']['reasoning_effort'],'high')
        self.assertEqual(c['models']['m']['thinking_controls']['on']['reasoning_effort'],'low')
        with self.assertRaises(ValueError):with_effort(c,{'m'},'off','high')
        with self.assertRaises(ValueError):with_effort(c,{'m'},'on','unsupported')
    def test_versioned_suites_keep_original_tasks_and_match_grader_ids(self):
        old=json.loads(task_file('v1').read_text());new=json.loads(task_file('v2').read_text())
        self.assertEqual([x['id'] for x in old],['ledger','intervals','pagination'])
        self.assertEqual(set(CHECKS),{x['id'] for x in old})
        self.assertEqual(set(CHECKS_V2),{x['id'] for x in new})
        self.assertEqual(len(new),5);ast.parse(bootstrap('inventory','def apply_batch(a,b): return a','v2'))
    def test_preflight_rejects_context_larger_than_live_backend(self):
        c={'models':{'m':{'base_url':'http://127.0.0.1:1234/v1','model':'fixture','context_window':262144,'max_tokens':131072}}}
        response=io.BytesIO(json.dumps({'data':[{'id':'fixture','max_model_len':32768}]}).encode())
        with patch('arena.urllib.request.urlopen',return_value=response):r=preflight(c)['m']
        self.assertFalse(r['available']);self.assertIn('32768',r['error'])
    def test_unsupported_off_mode_is_refused_before_launch(self):
        c={'models':{'m':{'context_window':32768,'max_tokens':8192,'thinking_controls':{'off':None}}}}
        with self.assertRaisesRegex(ValueError,'unavailable'):
            run(c,'m','m',1,[],Path('/tmp/not-created-by-test'),1,'off')
    def test_parallel_launches_cannot_claim_the_same_harness_port(self):
        with tempfile.TemporaryDirectory() as d:
            config={'dsh_home':d,'port':3086}
            with harness_port_lock(config):
                with self.assertRaisesRegex(RuntimeError,'owns this Harness port'):
                    with harness_port_lock(config):self.fail('second runner entered')
    def test_prefill_preserves_the_caller_and_prior_tool_transcript(self):
        original={'messages':[{'role':'tool','content':'passed'}],'stream':True}
        result=closed_think_prefill(original)
        self.assertEqual(len(original['messages']),1)
        self.assertEqual(result['messages'][-1],{'role':'assistant','content':''})
        self.assertEqual(result['messages'][0],original['messages'][0])
        self.assertFalse(result['add_generation_prompt']);self.assertTrue(result['continue_final_message'])
    def test_loopback_gateway_forwards_the_prefill_and_records_transform(self):
        class Echo(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Echo)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                log=Path(d)/'transforms.jsonl'
                c={'models':{'m':{'base_url':f'http://127.0.0.1:{server.server_port}/v1','thinking_off_prefill':True}}}
                with off_prefill_gateway(c,'off',{'m'},log) as routed:
                    req=urllib.request.Request(routed['models']['m']['base_url']+'/chat/completions',
                        data=json.dumps({'messages':[{'role':'user','content':'READY'}]}).encode())
                    with urllib.request.urlopen(req,timeout=3) as response:body=json.load(response)
                    self.assertEqual(body['messages'][-1],{'role':'assistant','content':''})
                    self.assertEqual(json.loads(log.read_text())['mode'],'closed-think-prefill')
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
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
