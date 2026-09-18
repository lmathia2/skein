#!/usr/bin/env python3
"""Build the durable E13 PTC isolation comparison from Harbor trial results."""

from __future__ import annotations

import json
import math
import random
import statistics
import ast
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".artifacts"
OUTPUT = ARTIFACTS / "e13-ptc-isolation-analysis"
DOCS = ROOT / "docs" / "experiments"
ARMS = {
    "pi-code": [ARTIFACTS / "e13-ptc-isolation-pi-code"],
    "pi-skein": [
        ARTIFACTS / "e13-ptc-isolation-pi-skein",
        ARTIFACTS / "e13-ptc-isolation-pi-skein-replacement-2",
    ],
    "skein": [ARTIFACTS / "e13-ptc-isolation-skein"],
}
RATES = {"uncached_input": 0.10 / 1_000_000, "cache_read": 0.002 / 1_000_000, "output": 0.20 / 1_000_000}


def seconds(start: str, finish: str) -> float:
    return (datetime.fromisoformat(finish.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()


def patch_stats(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"files": 0, "additions": 0, "deletions": 0}
    files, additions, deletions = set(), 0, 0
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("diff --git "):
            files.add(line.split(" b/", 1)[-1])
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"files": len(files), "additions": additions, "deletions": deletions}


def collect() -> list[dict]:
    rows = []
    for arm, roots in ARMS.items():
        for root in roots:
            for path in sorted(root.glob("*/*/*/result.json")):
                data = json.loads(path.read_text())
                if data.get("exception_info") or not data.get("verifier_result"):
                    continue
                reward = data["verifier_result"]["rewards"]
                agent = data.get("agent_result") or {}
                input_tokens = int(agent.get("n_input_tokens") or 0)
                cache_tokens = int(agent.get("n_cache_tokens") or 0)
                output_tokens = int(agent.get("n_output_tokens") or 0)
                uncached = max(0, input_tokens - cache_tokens)
                repriced = uncached * RATES["uncached_input"] + cache_tokens * RATES["cache_read"] + output_tokens * RATES["output"]
                metadata = agent.get("metadata") or {}
                skein = metadata.get("skein") or {}
                metrics = skein.get("metrics") or {}
                task = data["task_name"].split("/", 1)[-1]
                rows.append({
                    "arm": arm,
                    "task": task,
                    "trial": data["trial_name"],
                    "result_path": str(path.relative_to(ROOT)),
                    "reward": float(reward.get("reward") or 0),
                    "partial": float(reward.get("partial") or 0),
                    "f2p": float(reward.get("f2p") or 0),
                    "p2p": float(reward.get("p2p") or 0),
                    "input_tokens": input_tokens,
                    "cache_read_tokens": cache_tokens,
                    "uncached_input_tokens": uncached,
                    "output_tokens": output_tokens,
                    "cache_read_ratio": cache_tokens / input_tokens if input_tokens else 0,
                    "provider_reported_cost_usd": float(agent.get("cost_usd") or 0),
                    "repriced_cost_usd": repriced,
                    "agent_steps": int(agent.get("n_agent_steps") or data.get("n_agent_steps") or 0),
                    "agent_seconds": seconds(data["agent_execution"]["started_at"], data["agent_execution"]["finished_at"]),
                    "end_to_end_seconds": seconds(data["started_at"], data["finished_at"]),
                    "skein_status": skein.get("status"),
                    "skein_error_code": skein.get("error_code"),
                    "reasoning_tokens": int(metrics.get("reasoning_tokens") or 0),
                    "work_batches": int(metrics.get("outcome_iterations") or 0),
                    "patch": patch_stats(path.parent / "artifacts" / "model.patch"),
                })
    counts = Counter(row["arm"] for row in rows)
    assert counts == {"pi-code": 24, "pi-skein": 24, "skein": 24}, counts
    return rows


def percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] if lo == hi else values[lo] + (values[hi] - values[lo]) * (pos - lo)


def aggregate(rows: list[dict]) -> dict:
    def total(key: str) -> float:
        return sum(row[key] for row in rows)

    rewards = [row["reward"] for row in rows]
    passes = int(sum(rewards))
    status = Counter(row["skein_status"] for row in rows if row["skein_status"])
    status_by_reward = Counter(f"{row['skein_status']}|reward={int(row['reward'])}" for row in rows if row["skein_status"])
    return {
        "trials": len(rows),
        "passes": passes,
        "pass_rate": statistics.mean(rewards),
        "task_pass_at_3": sum(any(row["reward"] for row in rows if row["task"] == task) for task in {row["task"] for row in rows}),
        "mean_partial": statistics.mean(row["partial"] for row in rows),
        "mean_f2p": statistics.mean(row["f2p"] for row in rows),
        "mean_p2p": statistics.mean(row["p2p"] for row in rows),
        "median_agent_seconds": statistics.median(row["agent_seconds"] for row in rows),
        "mean_agent_seconds": statistics.mean(row["agent_seconds"] for row in rows),
        "p90_agent_seconds": percentile([row["agent_seconds"] for row in rows], .9),
        "median_end_to_end_seconds": statistics.median(row["end_to_end_seconds"] for row in rows),
        "total_input_tokens": int(total("input_tokens")),
        "total_cache_read_tokens": int(total("cache_read_tokens")),
        "total_uncached_input_tokens": int(total("uncached_input_tokens")),
        "total_output_tokens": int(total("output_tokens")),
        "cache_read_ratio": total("cache_read_tokens") / total("input_tokens"),
        "median_agent_steps": statistics.median(row["agent_steps"] for row in rows),
        "mean_agent_steps": statistics.mean(row["agent_steps"] for row in rows),
        "median_work_batches": statistics.median(row["work_batches"] for row in rows) if status else None,
        "total_repriced_cost_usd": total("repriced_cost_usd"),
        "cost_per_trial_usd": total("repriced_cost_usd") / len(rows),
        "cost_per_pass_usd": total("repriced_cost_usd") / passes if passes else None,
        "provider_reported_cost_usd": total("provider_reported_cost_usd"),
        "mean_patch_files": statistics.mean(row["patch"]["files"] for row in rows),
        "mean_patch_churn": statistics.mean(row["patch"]["additions"] + row["patch"]["deletions"] for row in rows),
        "skein_statuses": dict(status),
        "skein_statuses_by_reward": dict(status_by_reward),
    }


def task_aggregates(rows: list[dict]) -> dict:
    result = {}
    for task in sorted({row["task"] for row in rows}):
        result[task] = {}
        for arm in ARMS:
            subset = [row for row in rows if row["task"] == task and row["arm"] == arm]
            result[task][arm] = {
                "passes": int(sum(row["reward"] for row in subset)),
                "mean_partial": statistics.mean(row["partial"] for row in subset),
                "mean_agent_seconds": statistics.mean(row["agent_seconds"] for row in subset),
                "mean_agent_steps": statistics.mean(row["agent_steps"] for row in subset),
                "repriced_cost_usd": sum(row["repriced_cost_usd"] for row in subset),
                "output_tokens": sum(row["output_tokens"] for row in subset),
                "patch_files_mean": statistics.mean(row["patch"]["files"] for row in subset),
            }
    return result


def cluster_bootstrap(rows: list[dict], left: str, right: str, samples: int = 20_000) -> dict:
    tasks = sorted({row["task"] for row in rows})
    rates = {(arm, task): statistics.mean(row["reward"] for row in rows if row["arm"] == arm and row["task"] == task) for arm in (left, right) for task in tasks}
    rng = random.Random(13)
    diffs = []
    for _ in range(samples):
        picked = [rng.choice(tasks) for _ in tasks]
        diffs.append(statistics.mean(rates[left, task] - rates[right, task] for task in picked))
    diffs.sort()
    observed = statistics.mean(rates[left, task] - rates[right, task] for task in tasks)
    return {"left": left, "right": right, "pass_rate_difference": observed, "task_cluster_bootstrap_95pct": [diffs[int(.025 * samples)], diffs[int(.975 * samples)]]}


def skein_binding_reuse() -> dict:
    cells = reused = syntax_errors = 0
    notebooks = list((ARTIFACTS / "e13-ptc-isolation-skein").glob("*/*/*/agent/skein-state/runs/*/notebooks/*.ipynb"))
    for path in notebooks:
        known: set[str] = set()
        for cell in json.loads(path.read_text()).get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            cells += 1
            try:
                tree = ast.parse("".join(cell.get("source", [])))
            except SyntaxError:
                syntax_errors += 1
                continue
            loads = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
            stores = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Param))}
            reused += bool(loads & known)
            known.update(stores)
    return {"notebooks": len(notebooks), "code_cells": cells, "cells_reusing_prior_binding": reused,
            "reuse_rate": reused / cells, "syntax_error_cells": syntax_errors}


def fmt(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


def report(summary: dict) -> str:
    a = summary["arms"]
    lines = [
        "# E13 PTC isolation: final comparison",
        "",
        "All arms used `meta/muse-spark-1.3-contributor`, OpenRouter, `xhigh`, and `max_output_tokens=32768`. Each arm has 8 tasks × 3 fresh trials. The successful Scriggo replacement is included as Pi + Skein trial 24; the timed-out launcher attempt is excluded from outcome and latency aggregates because it produced no trial result or captured usage.",
        "",
        "## Aggregate results",
        "",
        "| Arm | Pass | Task pass@3 | Mean partial | Median agent time | P90 | Steps median | Input/cache/output | Cache ratio | Repriced cost | Cost/pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {"pi-code": "Pi + Code Tool", "pi-skein": "Pi + Skein PTC", "skein": "Skein + Skein PTC"}
    for arm in ARMS:
        x = a[arm]
        lines.append(f"| {labels[arm]} | {x['passes']}/24 ({x['pass_rate']:.1%}) | {x['task_pass_at_3']}/8 | {x['mean_partial']:.3f} | {x['median_agent_seconds']/60:.1f}m | {x['p90_agent_seconds']/60:.1f}m | {x['median_agent_steps']:.0f} | {x['total_input_tokens']/1e6:.1f}M / {x['total_cache_read_tokens']/1e6:.1f}M / {x['total_output_tokens']/1e6:.1f}M | {x['cache_read_ratio']:.1%} | ${x['total_repriced_cost_usd']:.3f} | ${x['cost_per_pass_usd']:.3f} |")
    lines += [
        "",
        "The strongest result is the host-loop effect. Pi + Skein PTC passes 17/24 while Skein + the same PTC runtime passes 9/24, a 33-point gap. The outer loop, prompt/state policy, and termination behavior therefore explain more of Skein's deficit than persistent CPython alone.",
        "",
        f"Pi + Code Tool also leads Pi + Skein PTC by {summary['comparisons'][0]['pass_rate_difference']:+.1%}, but their mean partial scores are nearly identical ({a['pi-code']['mean_partial']:.3f} versus {a['pi-skein']['mean_partial']:.3f}) and the task-cluster bootstrap interval crosses zero ({summary['comparisons'][0]['task_cluster_bootstrap_95pct'][0]:+.1%} to {summary['comparisons'][0]['task_cluster_bootstrap_95pct'][1]:+.1%}). This suggests a real reliability edge worth testing, not proof that the Skein runtime is intrinsically less capable.",
        "",
        f"The observed Pi + Skein minus Skein pass-rate difference is {summary['comparisons'][1]['pass_rate_difference']:+.1%}; its task-cluster bootstrap 95% interval is {summary['comparisons'][1]['task_cluster_bootstrap_95pct'][0]:+.1%} to {summary['comparisons'][1]['task_cluster_bootstrap_95pct'][1]:+.1%}. With only eight task clusters, this remains directional rather than a precise population estimate.",
        "",
        "## Per-task outcomes",
        "",
        "Each cell is `passes/3 · mean partial · mean agent minutes · task cost`.",
        "",
        "| Task | Pi + Code Tool | Pi + Skein PTC | Skein + PTC |",
        "|---|---:|---:|---:|",
    ]
    for task, arms in summary["tasks"].items():
        cells = []
        for arm in ARMS:
            x = arms[arm]
            cells.append(f"{x['passes']}/3 · {x['mean_partial']:.3f} · {x['mean_agent_seconds']/60:.1f}m · ${x['repriced_cost_usd']:.3f}")
        lines.append(f"| `{task}` | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## What changes in the traces",
        "",
        f"Pi + Code Tool and Pi + Skein execute long iterative loops: median {a['pi-code']['median_agent_steps']:.0f} and {a['pi-skein']['median_agent_steps']:.0f} model/tool steps. Skein uses a median {a['skein']['median_agent_steps']:.0f} model calls across {a['skein']['median_work_batches']:.1f} work batches. This makes Skein much cheaper and usually faster, while giving it fewer outer-loop opportunities to reassess failures and redirect the implementation.",
        "",
        f"Skein's own terminal state disagrees with the external verifier frequently: {json.dumps(a['skein']['skein_statuses'], sort_keys=True)}. Eight of nine externally passing submissions are internally marked `blocked`, while 11 blocked runs fail externally; two runs stop `failed`. `Blocked` therefore mixes successful and incomplete work and is not a reliable completion decision. The Pi-hosted arms continue their turn loop and let Harbor verify the final workspace. This termination-policy mismatch is the clearest mechanism consistent with the accuracy difference.",
        "",
        f"Skein is dramatically more economical: ${a['skein']['total_repriced_cost_usd']:.3f} versus ${a['pi-skein']['total_repriced_cost_usd']:.3f} for the same PTC runtime, and its median latency is {a['skein']['median_agent_seconds']/60:.1f} minutes versus {a['pi-skein']['median_agent_seconds']/60:.1f}. Pi buys more repair/retest cycles with much more context traffic. Pi + Skein has a {a['pi-skein']['cache_read_ratio']:.1%} cache-read ratio, but repeated cached context still dominates nominal input volume; caching lowers cost without lowering request size or latency proportionally.",
        "",
        f"The quality loss is concentrated in feature completion: Skein's mean F2P is {a['skein']['mean_f2p']:.3f}, versus {a['pi-skein']['mean_f2p']:.3f} for Pi + Skein, while P2P remains {a['skein']['mean_p2p']:.3f}. Skein usually avoids regressions but more often stops before the requested behavior is fully implemented.",
        "",
        "Patch scope also differs. Mean changed files / line churn are " + ", ".join(f"{labels[arm]} {a[arm]['mean_patch_files']:.1f}/{a[arm]['mean_patch_churn']:.0f}" for arm in ARMS) + ". Larger patches are not inherently better, but they indicate that the Pi loops explore and revise more of the repository before stopping.",
        "",
        "## Prompt, state, and batching differences",
        "",
        "**Pi + Code Tool** gives Pi a compact `code` contract, explicit persistence, generated helper signatures, clear print/return behavior, and catchable Python exceptions. The Pi transcript retains the code that created variables, while serialized interpreter state rides in tool-result details. There is no separate Skein work-packet or `AgentStep` completion protocol.",
        "",
        "**Pi + Skein PTC** keeps Pi's same outer loop and swaps only the runtime. Its extension explicitly says that variables persist and advertises `agent.state.list/describe/reuse`. Each response also exposes `state_count`, `state_delta`, `state_preserved`, and `failure_stage` in model-visible JSON. This arm isolates the resident CPython implementation from Skein's orchestration.",
        "",
        f"**Skein + Skein PTC** adds a large notebook instruction covering capability envelopes, citations, recovery, freshness, artifact paging, mutation rules, and structured `AgentStep` output. Much of that is useful but competes with the task for attention. The full runtime computes namespace deltas and manifests, then `compact_tool_result` removes `state_count` and `state_delta`; the tested profile leaves `emit_state_updates: false`. After a work-batch boundary, the model receives a reconstructed packet without a concise list of live bindings. This is a real interface defect, but it is not evidence that persistence is unused: an AST heuristic finds prior-binding references in {summary['trace']['skein_binding_reuse']['cells_reusing_prior_binding']}/{summary['trace']['skein_binding_reuse']['code_cells']} Skein cells ({summary['trace']['skein_binding_reuse']['reuse_rate']:.1%}). Treat namespace visibility as a targeted efficiency/recovery fix, not the primary cause of the 33-point host-loop gap.",
        "",
        "Batching differs too. The tested Skein profile allows 48 cells per batch, yields after 24 read-only/no-progress cells, and permits only one review cell before forcing a structured decision. Pi has no equivalent outer work-batch protocol and continues one code turn at a time. Skein's median run used 66 model calls across 9.5 batches, versus roughly 120 Pi turns. The savings are substantial, but the forced boundary combines lossy state handoff with an unreliable `blocked` decision.",
        "",
        "## Task-level reading",
        "",
    ]
    task_notes = {
        "ink-grid-box-layout": "Pi Code leads 2/3 to 1/3 for both Skein-runtime arms. The Pi host does not remove the gap here, so this task is evidence for a runtime/interface reliability difference; full Skein also has lower partial credit and fewer repair steps.",
        "koota-pair-relation-tracking": "Pi Code is 3/3 while both Skein-runtime arms are 1/3. Pi + Skein spends the same large step budget as Pi Code, so under-iteration alone cannot explain this task; the Code Tool contract/runtime is the leading difference.",
        "obsidian-linter-scoped-ignore-markers": "Pi + Skein is the only 3/3 arm and is much faster and cheaper than Pi Code. The Skein runtime is fully capable here; full Skein's 2/3 result points to host-loop variance rather than an executor ceiling.",
        "query-persist-restored-query-state": "Both Pi arms are 3/3. Full Skein is 2/3 with 0.993 partial credit and is slower than either Pi arm, so its lower overall cost/latency pattern does not hold on this task.",
        "scriggo-method-declarations": "Pi Code / Pi + Skein / Skein score 2/3, 1/3, 0/3. Pi + Skein's 0.996 partial score shows near-complete failures around a strict verifier edge; it also has the longest latency and highest cost, so extra iteration is not sufficient by itself.",
        "tengo-destructuring-bindings": "Both Pi arms are 3/3, while full Skein is 1/3 with 0.710 partial credit. Holding the runtime fixed, the Pi loop recovers the whole gap; Skein stops with broad new-feature semantics unfinished.",
        "testem-per-launcher-reports": "Both Pi arms are 3/3 and full Skein is 2/3 with 0.973 partial credit at a fraction of the cost. This is the closest case to a favorable Skein efficiency tradeoff.",
        "textual-richlog-follow-state": "Pi Code / Pi + Skein / Skein score 3/3, 2/3, 0/3. Full Skein stops after 5.7 mean minutes with only 0.295 partial credit, the clearest trace of premature termination or insufficient repair cycles.",
    }
    for task, arms in summary["tasks"].items():
        lines.append(f"- `{task}`: {task_notes[task]} Mean steps were {arms['pi-code']['mean_agent_steps']:.0f} / {arms['pi-skein']['mean_agent_steps']:.0f} / {arms['skein']['mean_agent_steps']:.0f}.")
    lines += [
        "",
        "## Recommended next experiments",
        "",
        "1. Keep the Pi extension arm. It is the clean isolation boundary: Pi + Code Tool versus Pi + Skein PTC measures the runtime, and Pi + Skein versus Skein + PTC measures the host loop. This experiment shows that moving Skein PTC into Pi is useful and already implemented.",
        "2. Make namespace awareness the first fix: expose bounded `state_delta` after every cell and a compact live-binding summary at every batch handoff. The data already exists; do not replay full cell output. Test this as one isolated toggle (`emit_state_updates: true`) before redesigning memory.",
        "3. Fix Skein's termination contract: a tool/runtime failure should return control to the model with a compact error, and `blocked` without a concrete human question should route to replan/verify rather than terminate. Re-run the task strata where Pi+Skein beats Skein.",
        "4. Shorten the always-on notebook prompt. Keep persistence, capability signatures, print discipline, mutation safety, and failure semantics in the static prompt; move citation/recovery recipes behind targeted `agent.help` calls.",
        "5. Tune work-batch size as the efficiency knob. Compare 8-12 cell batches with the current 48-cell ceiling and add a targeted-test checkpoint after mutations. The goal is to retain most of Skein's cost advantage while adding repair opportunities.",
        "6. Use at least 20 diverse tasks and three trials for the confirmatory run. Report task-cluster intervals and partial credit alongside pass rate; do not interpret 24 trials from eight tasks as 24 independent task samples.",
        "",
        "## Data notes",
        "",
        "Repriced cost uses $0.10/M uncached input, $0.002/M cache reads, and $0.20/M output. Provider-reported cost is retained in the JSON but omitted from the headline because it does not match this experiment's comparison tariff. The original Pi + Skein Scriggo job timed out after two valid trials and left one stale `running` marker; it emitted no third trial result or agent usage, so its wasted cost cannot be measured from retained artifacts.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = collect()
    summary = {
        "schema_version": "e13-ptc-isolation-analysis-v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "pricing_usd_per_token": RATES,
        "arms": {arm: aggregate([row for row in rows if row["arm"] == arm]) for arm in ARMS},
        "tasks": task_aggregates(rows),
        "trace": {"skein_binding_reuse": skein_binding_reuse()},
        "comparisons": [
            cluster_bootstrap(rows, "pi-code", "pi-skein"),
            cluster_bootstrap(rows, "pi-skein", "skein"),
            cluster_bootstrap(rows, "pi-code", "skein"),
        ],
        "excluded": [{"arm": "pi-skein", "task": "scriggo-method-declarations", "reason": "job timeout before third trial result; no captured trial usage"}],
        "trials": rows,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    metrics = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    prose = report(summary)
    for path, content in (
        (OUTPUT / "metrics.json", metrics),
        (OUTPUT / "report.md", prose),
        (DOCS / "e13-ptc-isolation-metrics.json", metrics),
        (DOCS / "e13-ptc-isolation-analysis.md", prose),
    ):
        path.write_text(content)
    print(DOCS / "e13-ptc-isolation-analysis.md")


if __name__ == "__main__":
    main()
