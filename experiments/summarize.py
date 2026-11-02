#!/usr/bin/env python3
"""Summarize saved facts; missing metrics stay null and truncations never pass."""
import argparse
import collections
import json
from pathlib import Path
import statistics

def read_lines(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []

def summarize(root):
    manifest=json.loads((root/'manifest.json').read_text())
    events=read_lines(root/'state/arena-events.jsonl')
    calls=read_lines(root/'state/arena-calls.jsonl')
    usage=collections.defaultdict(lambda:{'requests':0,'input_tokens':0,'output_tokens':0})
    tools=collections.Counter();truncated=[];test_results=[];roles={}
    for row in events:
        event=row['event'];data=event.get('data',{});sid=row['sessionId'];role='worker' if sid.startswith('worker-') else 'coordinator'
        roles[sid]=role
        if event['type']=='assistant/message':
            u=data.get('usage')
            if u:
                usage[role]['requests']+=1
                usage[role]['input_tokens']+=u.get('inputTokens',0)
                usage[role]['output_tokens']+=u.get('outputTokens',0)
        elif event['type']=='turn/end':
            reason=data.get('reason');kind=reason.get('kind') if isinstance(reason,dict) else reason
            if kind=='max-tokens':truncated.append(sid)
        elif event['type']=='tool/call':tools[data['name']]+=1
        elif event['type']=='tool/result':
            for block in data.get('message',{}).get('content',[]):
                if block.get('type')!='text':continue
                try:value=json.loads(block['text'])
                except (ValueError,KeyError):continue
                if isinstance(value,dict) and 'passed_cases' in value:test_results.append({'session':sid,'completed_at_ms':event.get('time'),**value})
    tickets=json.loads((root/'tickets.json').read_text()).get('items',[]) if (root/'tickets.json').exists() else []
    final_tests={r['session']:r for r in test_results}
    first_tests={}
    for r in test_results:first_tests.setdefault(r['session'],r)
    successful=[t for t in tickets if t.get('state')=='ended' and t.get('ended',{}).get('outcome')=='completed'
                and final_tests.get(t.get('sessionId'),{}).get('passed')]
    started={r['id']:r for r in calls if r['phase']=='start'}
    first={r['id']:r['at'] for r in calls if r['phase']=='first_delta'}
    ended={r['id']:r for r in calls if r['phase']=='end'}
    latency=[(e['at']-started[k]['at'])/1000 for k,e in ended.items() if k in started]
    ttft=[(at-started[k]['at'])/1000 for k,at in first.items() if k in started]
    total=sum(v['output_tokens'] for v in usage.values());wall=manifest.get('wall_s')
    accounted=sum(v['requests'] for v in usage.values())
    origin=manifest.get('work_started')
    first_at=min((x['completed_at_ms'] for x in test_results if x.get('completed_at_ms') is not None),default=None)
    final_at=max((x['completed_at_ms'] for x in final_tests.values() if x.get('completed_at_ms') is not None),default=None)
    final_ready=len(final_tests)==manifest.get('expected_tickets') and all(x['passed'] for x in final_tests.values())
    complete=bool(started) and len(started)==accounted and set(ended)==set(started) and all(e.get('completed',False) for e in ended.values())
    review=json.loads((root/'code-review.json').read_text()) if (root/'code-review.json').exists() else 'pending blind code review; no overall quality score inferred from throughput'
    artifacts_passed=bool(manifest.get('expected_tickets')) and len(successful)==manifest.get('expected_tickets') and not truncated
    workflow_complete=manifest['status']=='finished' and manifest.get('reporting_drained') is not False
    return {'run':manifest['id'],'status':manifest['status'],'scope':manifest['scope'],
      'artifacts_passed':artifacts_passed,'workflow_complete':workflow_complete,
      'reporting_drained':manifest.get('reporting_drained'),
      'fixture_suite':manifest.get('fixture_suite','v1'),'reasoning_effort':manifest.get('reasoning_effort'),
      'checker_sha256':manifest.get('checker_sha256'),
      'thinking_mode':manifest.get('requested_thinking_mode'),'thinking_controls':manifest.get('thinking_controls'),
      'thinking_transport':manifest.get('thinking_transport'),
      'worker_count':manifest['workers'],'fixture_sha256':manifest['fixture_sha256'],
      'tickets':len(tickets),'verified_completed_tickets':len(successful),'expected_tickets':manifest.get('expected_tickets'),
      'wall_s':wall,'usage_by_role':dict(usage),'all_role_output_tokens':total,
      'usage_complete':complete,'observed_model_requests':len(started) if calls else None,
      'all_role_output_tokens_per_wall_second':total/wall if wall and total and complete else None,
      'completed_tickets_per_minute':len(successful)*60/wall if wall else None,
      'observed_peak_model_requests':max((r.get('active',0) for r in calls),default=None),
      'request_latency_median_s':statistics.median(latency) if latency else None,
      'first_delta_median_s':statistics.median(ttft) if ttft else None,
      'tool_calls':dict(tools),'max_token_endings':truncated,'test_results':test_results,
      'objective_test_attempts':len(test_results),'first_test_results':list(first_tests.values()),
      'first_attempt_passed_tickets':sum(bool(x['passed']) for x in first_tests.values()),
      'time_to_first_test_s':first_at/1000-origin if origin and first_at else None,
      'time_to_final_passing_tests_s':final_at/1000-origin if origin and final_at and final_ready else None,
      'passed':workflow_complete and artifacts_passed,
      'quality_review':review}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('runs',type=Path,nargs='+');a=p.parse_args()
    results=[]
    for root in a.runs:
        d=summarize(root);(root/'summary.json').write_text(json.dumps(d,indent=2)+'\n');results.append(d)
    print(json.dumps(results,indent=2))
