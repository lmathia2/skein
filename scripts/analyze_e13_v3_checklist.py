"""Recompute the completed checklist comparison from immutable results and traces."""
import ast
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / '.artifacts/e13-v3-checklist'


def elapsed(span):
    return (datetime.fromisoformat(span['finished_at'].replace('Z', '+00:00')) - datetime.fromisoformat(span['started_at'].replace('Z', '+00:00'))).total_seconds()


def summarize(rows):
    return dict(n=len(rows), passes=sum(r['reward'] for r in rows),
                mean_f2p=statistics.mean(r['f2p'] for r in rows),
                mean_p2p=statistics.mean(r['p2p'] for r in rows),
                median_agent_seconds=statistics.median(r['agent_seconds'] for r in rows),
                median_total_seconds=statistics.median(r['total_seconds'] for r in rows),
                **{k: sum(r[k] for r in rows) for k in ('uncached', 'cache', 'output', 'repriced_usd', 'provider_usd')},
                trace=dict(sum((Counter(r['trace']) for r in rows), Counter())))


def main():
    rows = []
    for arm in ('off', 'on'):
        for p in sorted((RESULTS / ('checklist-' + arm)).glob('*/*/*/result.json')):
            d = json.loads(p.read_text())
            assert d['exception_info'] is None
            u = d['agent_result']['metadata']['pi_code_tool']['usage']
            row = dict(arm=arm, task=d['task_name'].split('/')[-1], trial=p.parent.name,
                       result=str(p.relative_to(ROOT)), **d['verifier_result']['rewards'],
                       uncached=u['input'], cache=u['cache'], output=u['output'],
                       repriced_usd=(u['input']*.1+u['cache']*.002+u['output']*.2)/1e6,
                       provider_usd=u['cost'], agent_seconds=elapsed(d['agent_execution']),
                       total_seconds=elapsed(d), trace={}, errors=[], final='', failures=[])
            counts = Counter()
            for line in (p.parent/'agent/pi-events.jsonl').read_text().splitlines():
                e = json.loads(line); kind = e.get('type')
                if kind in ('compaction_end', 'auto_compaction_end'):
                    counts['compactions'] += 1
                if kind == 'message_end':
                    m = e.get('message', {})
                    if m.get('role') == 'assistant':
                        text = '\n'.join(b.get('text', '') for b in m.get('content', []) if b.get('type') == 'text')
                        if text: row['final'] = text
                    if m.get('stopReason') == 'error': row['errors'].append(m.get('errorMessage'))
                if kind == 'tool_execution_start' and e.get('toolName') == 'code':
                    code = e.get('args', {}).get('code')
                    if code is None: counts['retrievals'] += 1; continue
                    counts['cells'] += 1
                    try:
                        tree = ast.parse(code)
                        calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
                        counts['batch_cells'] += sum(c in ('bash','read','write','edit','agent.fs.read','agent.fs.write','agent.fs.edit','agent.shell.run') for c in calls) > 1
                    except SyntaxError: pass
                if kind == 'tool_execution_end' and e.get('toolName') == 'code':
                    result=e.get('result',{});details=result.get('details',{})
                    counts['visible_bytes'] += sum(len(b.get('text','').encode()) for b in result.get('content',[]))
                    counts['cell_errors'] += details.get('status') == 'error'
                    counts['hard_timeouts'] += details.get('status') == 'timeout'
                    counts['preflight_rejections'] += details.get('failure_stage') == 'source_validation'
                    counts['reuse_cells'] += bool(details.get('prior_bindings_read_before_assignment'))
                    counts['truncated'] += bool(details.get('output_truncated'))
                    for b in details.get('broker_outcomes',[]):
                        if b.get('operation') == 'bash':
                            counts['shell_calls'] += 1
                            counts['shell_timeouts'] += bool(b.get('timeout'))
                            counts['diagnostic_notices'] += bool(b.get('notice_supplied'))
            row['trace'] = dict(counts)
            tests=json.loads((p.parent/'verifier/ctrf.json').read_text())['results']['tests']
            row['failures']=[t for t in tests if t['status']=='failed']
            rows.append(row)
    assert len(rows)==24
    assert all(sum(r['arm']==a and r['task']==t for r in rows)==3 for a in ('off','on') for t in {r['task'] for r in rows})
    output=dict(rows=rows, arms={a:summarize([r for r in rows if r['arm']==a]) for a in ('off','on')},
                tasks={t:{a:summarize([r for r in rows if r['task']==t and r['arm']==a]) for a in ('off','on')} for t in sorted({r['task'] for r in rows})})
    target=ROOT/'docs/experiments/e13-pi-skein-v3-checklist-metrics.json'
    target.write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k!='rows'},indent=2))


if __name__ == '__main__':
    main()
