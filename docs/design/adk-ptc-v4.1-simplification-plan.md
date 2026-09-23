# ADK-native PTC v4.1 implementation tracker

Status: phases 1–8 implemented on `main`

Goal: retain ADK's runtime, make PTC v4.1 the default, and keep Skein's evidence,
effect, recovery, and verification concepts without a second structured agent loop.

## Completed phases

| Phase | Delivered | Primary evidence |
| --- | --- | --- |
| 1. Freeze contracts | One ADK loop, one public `code` tool, brokered effects, host verification | `docs/adr/adk-native-ptc-v4.1-core.md` |
| 2. Simplify prompt | Stable worker instruction plus bounded dynamic coding packet | `app/agent/config.py`, `harness/core/orchestration/core.py` |
| 3. Make PTC default | PTC v4.1 helper prompt and persistent worker selected by default composition | `app/agent/builders.py`, `app/agent/ptc.py` |
| 4. Remove structured mode | Ordinary prose ends a work batch; only optional blocker JSON remains | `app/agent/workflow.py`, `harness/core/orchestration/runtime.py` |
| 5. Preserve ADK lifecycle | Native messages, continuation, streaming, cancellation, resumability, and plugins stay ADK-owned | `app/agent/factory.py` |
| 6. Unify effects and evidence | Direct and nested capabilities share broker, receipts, redaction, artifacts, and events | `harness/execution/`, `harness/evidence/` |
| 7. Verify and recover | Host-only completion, failed-check feedback, recovery boundaries, safe PTC checkpoints | `harness/verification/`, `harness/ptc/`, `app/agent/workflow.py` |
| 8. Developer and eval path | One install script, focused test targets, Harbor runners, Pi parity adapters, grounded docs | `install.sh`, `Makefile`, `scripts/`, `README.md` |

## Invariants to protect

- Do not implement a second native tool-call loop outside ADK.
- Do not expose `execute_code` as a second model tool.
- Do not require structured model output for normal completion.
- Do not allow PTC helpers to bypass the execution broker.
- Do not let a model completion claim bypass host verification.
- Do not turn derived memory, notebook state, or the Python heap into conversation
  authority.
- Keep direct four-tool mode isolated as an ablation.

## Ongoing qualification

Future changes should be evaluated as matched experiments, not new architecture phases:

- PTC versus four direct tools on the same Harbor manifest;
- stable-prefix and dynamic-context token cost;
- tool calls, verification retries, elapsed time, and completion rate;
- resume behavior after model, tool, and PTC-worker interruption;
- trace completeness and absence of unknown effects.

Historical measurements are retained under `docs/audits/` and `docs/experiments/`.
They are evidence, not active implementation plans.
