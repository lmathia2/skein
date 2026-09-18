import ast
import json
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "docs/experiments"
A = ROOT / ".artifacts"
roots = {
    "Code Tool": A / "e13-ptc-isolation-pi-code",
    "v2": A / "e13-pi-skein-v2",
    "v3": A / "e13-pi-skein-v3",
}
rows = []
for arm, root in roots.items():
    for p in sorted(root.glob("*/*/*/result.json")):
        d = json.loads(p.read_text())
        a = d.get("agent_result") or {}
        r = (d.get("verifier_result") or {}).get("rewards")
        if not r:
            continue

        def dt(x):
            return datetime.fromisoformat(x.replace("Z", "+00:00"))

        ex = d["agent_execution"]
        i = a.get("n_input_tokens") or 0
        c = a.get("n_cache_tokens") or 0
        o = a.get("n_output_tokens") or 0
        row = {
            "arm": arm,
            "task": p.parents[2].name[4:].split("-attempt-")[0],
            "trial": p.parent.name,
            "result": str(p.relative_to(ROOT)),
            **r,
            "input": i,
            "cache": c,
            "uncached": i - c,
            "output": o,
            "cost": ((i - c) * 0.1 + c * 0.002 + o * 0.2) / 1e6,
            "provider_cost": a.get("cost_usd"),
            "seconds": (dt(ex["finished_at"]) - dt(ex["started_at"])).total_seconds(),
            "steps": a.get("n_agent_steps"),
        }
        if arm in ("v2", "v3"):
            trace = {
                "cells": 0,
                "retrievals": 0,
                "visible_bytes": 0,
                "errors": 0,
                "hard_timeouts": 0,
                "shell_timeouts": 0,
                "shell_calls": 0,
                "diagnostic_notices": 0,
                "omitted_exit_supplied": 0,
                "truncated": 0,
                "reuse_cells": 0,
                "compactions": 0,
                "source_rejections": [],
                "batch_cells": 0,
            }
            pending = {}
            for line in (p.parent / "agent/pi-events.jsonl").open():
                e = json.loads(line)
                kind = e.get("type")
                if kind == "compaction_end":
                    trace["compactions"] += 1
                if kind == "tool_execution_start" and e.get("toolName") == "code":
                    args = e.get("args", {})
                    pending[e["toolCallId"]] = args
                    if "code" in args:
                        trace["cells"] += 1
                        try:
                            tree = ast.parse(args["code"])
                            n = sum(
                                isinstance(x, ast.Call)
                                and ast.unparse(x.func)
                                in [
                                    "bash",
                                    "read",
                                    "write",
                                    "edit",
                                    "agent.fs.read",
                                    "agent.fs.write",
                                    "agent.fs.edit",
                                    "agent.shell.run",
                                ]
                                for x in ast.walk(tree)
                            )
                            trace["batch_cells"] += n > 1
                        except SyntaxError:
                            pass
                    else:
                        trace["retrievals"] += 1
                if kind == "tool_execution_end" and e.get("toolName") == "code":
                    result = e.get("result", {})
                    details = result.get("details", {})
                    text = "\n".join(x.get("text", "") for x in result.get("content", []))
                    trace["visible_bytes"] += len(text.encode())
                    trace["errors"] += details.get("status") == "error"
                    trace["hard_timeouts"] += details.get("status") == "timeout"
                    trace["truncated"] += bool(details.get("output_truncated"))
                    trace["reuse_cells"] += bool(
                        details.get("prior_bindings_read_before_assignment")
                    )
                    for b in details.get("broker_outcomes", []):
                        if b.get("operation") == "bash":
                            trace["shell_calls"] += 1
                            trace["shell_timeouts"] += bool(b.get("timeout"))
                            trace["diagnostic_notices"] += bool(b.get("notice_supplied"))
                            trace["omitted_exit_supplied"] += bool(
                                b.get("selected_omitted_exit") and b.get("notice_supplied")
                            )
                    if details.get("failure_stage") == "source_validation":
                        trace["source_rejections"].append(
                            {"code": pending.get(e["toolCallId"]), "message": text[:1000]}
                        )
            row["trace"] = trace
        rows.append(row)
assert {arm: sum(r["arm"] == arm for r in rows) for arm in roots} == {
    "Code Tool": 24,
    "v2": 24,
    "v3": 23,
}


def agg(rs):
    return {
        "n": len(rs),
        "passes": sum(r["reward"] for r in rs),
        "mean_f2p": statistics.mean(r["f2p"] for r in rs),
        "mean_p2p": statistics.mean(r["p2p"] for r in rs),
        "median_seconds": statistics.median(r["seconds"] for r in rs),
        "mean_seconds": statistics.mean(r["seconds"] for r in rs),
        **{k: sum(r[k] for r in rs) for k in ["input", "cache", "uncached", "output", "cost"]},
    }


summary = {
    "status": "stopped with one budget-interrupted trial",
    "arms": {arm: agg([r for r in rows if r["arm"] == arm]) for arm in roots},
    "seven_completed_tasks": {
        arm: agg([r for r in rows if r["arm"] == arm and not r["task"].startswith("scriggo")])
        for arm in roots
    },
    "tasks": {
        task: {
            arm: agg([r for r in rows if r["arm"] == arm and r["task"] == task]) for arm in roots
        }
        for task in sorted({r["task"] for r in rows})
    },
    "trials": rows,
}
(D / "e13-pi-skein-v3-comparison-metrics.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: v for k, v in summary.items() if k not in ["trials", "tasks"]}, indent=2))
for arm in ["v2", "v3"]:
    print(
        "TRACE",
        arm,
        {
            k: sum(r["trace"][k] for r in rows if r["arm"] == arm)
            for k in [
                "cells",
                "batch_cells",
                "retrievals",
                "visible_bytes",
                "errors",
                "hard_timeouts",
                "shell_timeouts",
                "shell_calls",
                "diagnostic_notices",
                "omitted_exit_supplied",
                "truncated",
                "reuse_cells",
                "compactions",
            ]
        },
    )
for task, arms in summary["tasks"].items():
    print(
        "TASK",
        task,
        [
            (arm, d["passes"], round(d["median_seconds"]), round(d["cost"], 3))
            for arm, d in arms.items()
        ],
    )
