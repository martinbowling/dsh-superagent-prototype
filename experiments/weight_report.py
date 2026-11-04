"""Render measured hand-weight results without contacting a model or changing them."""
import argparse
import html
import json
from pathlib import Path
import statistics

def render(root):
    data=json.loads((root/'comparison.json').read_text());results=data['results']
    rows=[]
    for r in results:
        if r['kind']=='trained':continue
        score=r['metrics']['overall']
        rows.append(f'<tr><td>{html.escape(r["name"])}</td><td>{r["labels_seen"]:,}</td><td>{r["allocated_parameters"]:,}</td><td>{r["nonzero_parameters"]:,}</td><td><strong>{score["accuracy"]:.2%}</strong></td><td>{score["tp"]+score["tn"]:,} / {score["n"]:,}</td><td>{r.get("write_attempts","unknown")}</td></tr>')
    for n in (512,5062,8192):
        group=[r for r in results if r['kind']=='trained' and r['labels_seen']==n]
        values=[r['metrics']['overall']['accuracy'] for r in group]
        if group:rows.append(f'<tr><td>Trained · 3 seeds</td><td>{n:,}</td><td>99,969</td><td>{group[0]["nonzero_parameters"]:,}</td><td><strong>{statistics.median(values):.2%}</strong></td><td>{min(values):.2%}–{max(values):.2%}</td><td>300 epochs / seed</td></tr>')
    strata=[]
    for r in results:
        cells=''.join(f'<td>{r["metrics"]["strata"][f]["accuracy"]:.2%}</td>' for f in ('random','extremes','runs'))
        strata.append(f'<tr><td>{html.escape(r["name"])}</td>{cells}</tr>')
    workflows=[]
    for name in ('zero-high','revision-high'):
        path=root/name/'summary.json'
        if not path.exists():continue
        summary=json.loads(path.read_text());manifest=json.loads((root/name/'manifest.json').read_text())
        workflows.append(f'<p><b>{name}</b>: {summary["wall_s"]:.1f}s wall time; high effort; thinking on; {manifest["workers"]} worker slots; {manifest["ticket_count"]} Tickets. <a href="{name}/summary.json">Workflow evidence</a></p>')
    source='https://www.isbn-international.org/index.php/node/10'
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hand-written neural weights · BLACKFOLD high</title><style>
    :root{font-family:system-ui,sans-serif;color:#172338;background:#f3f6fb}body{max-width:1120px;margin:48px auto;padding:0 22px;line-height:1.55}h1{font-size:clamp(2rem,5vw,3.2rem);line-height:1.1;max-width:850px}h2{margin-top:2rem}p{max-width:950px}.tag{color:#345782;text-transform:uppercase;font-size:.8rem;font-weight:750;letter-spacing:.1em}.box{background:white;padding:24px;border:1px solid #dce4ee;border-radius:12px;margin:22px 0}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:.9rem}th,td{text-align:left;padding:12px;border-bottom:1px solid #e3e8f0}th{background:#edf2fa}a{color:#1954a6}code{word-break:break-all}li{margin:.5rem 0}footer{font-size:.8rem;margin-top:36px;color:#51627a}</style>
    <p class="tag">Measured local experiment · DeepSeek-1017-BLACKFOLD · native high effort</p><h1>Can a superagent hand-write a small neural network?</h1>
    <p>Two independent zero-label proposals, then one labeled revision of the preselected first proposal. A fixed 130 → 512 → 64 → 1 ReLU network receives raw one-hot digits. The agent supplies numeric weights only; our trusted evaluator runs the network.</p>
    <div class="box"><b>Task: ISBN-13 checksum validity.</b> This is arithmetic validity, not book registration or legal citation authenticity. The mathematical checksum rule is supplied to the agent. It must express that rule through the fixed network's weights. We did not reproduce the screenshot's legal-citation experiment.</div>
    <h2>Same architecture, held-out data</h2><p>16,000 balanced test examples; exact 12-digit stems are disjoint from training. Trained rows show the median and range across three seeds. Every network allocates 99,969 parameters; sparse constructions may use far fewer nonzero values.</p><div class="scroll"><table><thead><tr><th>Weights</th><th>Labels accessed</th><th>Allocated params</th><th>Nonzero params</th><th>Test accuracy</th><th>Correct / total or seed range</th><th>Attempts / budget</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></div>
    <p>Constant-class baseline: 50%. Direct checksum algorithm: 100%. A neural network is unnecessary for production checksum validation; this tests whether the harness can compile a known algorithm into weights.</p>
    <h2>Stress strata</h2><div class="scroll"><table><thead><tr><th>Proposal / seed</th><th>Random · 10,000</th><th>Extreme digits · 4,000</th><th>Repeated runs · 2,000</th></tr></thead><tbody>'''+''.join(strata)+'''</tbody></table></div>
    <h2>What the comparison establishes</h2><ul><li>The hand-written arm has the algorithmic rule and a pretrained model's mathematical prior knowledge. The trained arm fits raw inputs using the stated label budget. This is not an equal-knowledge comparison or a general claim that writing weights beats training.</li><li>Training: CPU PyTorch, 300 epochs for each of 3 seeds (11, 29, 47) at 512 / 5,062 / 8,192 total label budgets. AdamW, learning rate 0.001, weight decay 0.01, batch 256. About 20% of each budget is reserved for internal checkpoint selection, split by 12-digit stem; the table counts fitting and selection labels together.</li><li>Checkpoints use minimum internal validation binary cross-entropy. Final test labels select neither candidate, checkpoint, nor revision. Both independent zero-label proposals are reported.</li><li>arena_test reports schema and finite inference only. Structural success does not certify classification accuracy. Write attempts and all workflow tool feedback remain in the raw arm artifacts.</li><li>The revision receives 5,062 development rows and the frozen zero-00 weights. No test score or test row is supplied. We report a retained construction honestly if the revision keeps its weights.</li></ul>
    <h2>Actual harness workflow</h2>'''+''.join(workflows)+'''<p>Coordinator and worker requests use native <code>enable_thinking=true, reasoning_effort=high</code>, with a 131,072-token output ceiling. The zero-label arm has two worker slots and two Tickets; the revision has one. Model services, weights and rollback setup were retained.</p>
    <p><a href="comparison.json">Full metrics and artifact hashes</a> · <a href="data-receipt.json">Dataset receipt</a> · <a href="submissions.json">Frozen submissions</a> · <a href="training.json">Training curves and checkpoint evidence</a> · <a href="experiment.json">Experiment receipt</a></p>
    <footer>Checksum definition: <a href="'''+source+'''">International ISBN Agency</a>. Test samples are synthetic checksum strings. Model-created weight JSON is data, with no generated Python executed.</footer></html>'''
    (root/'report.html').write_text(content)
    return root/'report.html'

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args();print(render(a.root))
