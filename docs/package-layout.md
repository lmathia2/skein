# Package layout

Skein separates its agent authority from the machinery used to run benchmarks.
The runtime ownership flow is:

```text
app/agent ──► adapters ──► core ──► execution
                              │          │
                              └────► evidence ◄──── ptc
                                          ▲
                                   verification

evals ──► app/agent + adapters/pier
```

## Harness packages

- `core/` contains configuration, task models, bounded context construction,
  skills, and the deterministic coding-loop state machine.
- `execution/` owns every effect boundary: model-facing tools, path confinement,
  command runtimes, repository discovery, policy, approvals, and workspaces.
- `evidence/` owns append-only events and their projections: ledgers, memory
  programs, traces, and metrics. These are records and views, not execution policy.
- `ptc/` contains the persistent Python process and deterministic notebook
  projection used by the one-tool mode.
- `verification/` independently evaluates completion against the workspace and
  reconciled evidence.
- `adapters/` translates external frameworks into those contracts. `adk/` contains
  framework runtime and persistence integration, `providers/` contains model APIs,
  and `pier.py` maps Pier task environments into Skein.

## Outside the harness

- `app/agent/` wires one concrete ADK coding worker from the packages above.
- `evals/` owns frozen Harbor manifests, campaign execution, and result analysis.
  Removing `evals/` must not change the core agent's behavior.
- `scripts/` contains thin developer entry points; business logic belongs in the
  package it invokes.

This is an ownership map, not a claim that every Python import is acyclic: shared
typed contracts connect core, execution, and evidence. New code should join the
package that owns its authority. Do not add a top-level
`harness` package for a feature variant, benchmark, storage projection, or provider.
