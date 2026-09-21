# Pi-compatible loop implementation

Status: implemented behind `workflow.mode: pi_compatible`; ready for a fresh pinned
comparison, 2026-09-21. `thin` retains the lightweight-loop ablation without forcing
the v4.1 runtime limits.

## Decision

Keep Skein on ADK and keep Pi + Skein PTC v4.1 unchanged as the lightweight
reference. Skein's canonical trace, effect receipts, recovery, steering, and managed
verification remain authoritative. The model-facing loop becomes optional:

- `structured` is the existing phased work-packet loop and control arm.
- `pi_compatible` lets the coding model own the continuous inspect/edit/test/repair
  loop using Pi v4.1's direct helper contract.

The modes change model context and continuation policy, never trace capture, workspace
authority, or completion verification.

## Thin-loop contract

```text
task
  -> compact Markdown projection
  -> one continuous ADK model/tool loop over persistent PTC
  -> model requests verification
  -> Skein managed verification
  -> success, or concise failure returned to the next continuous loop
```

The harness still owns confinement, approvals, effect receipts, unknown-effect
reconciliation, budgets, worker recovery, steering, and terminal verification. The
model owns planning, phase changes, tool batching, failure interpretation, repair, and
the decision to request verification.

## Trace and projection boundary

The canonical event ledger retains complete typed events and artifact references. It
is not sent directly to the model. Each model request receives a disposable projection
derived from the ledger.

The thin projection is bounded Markdown containing:

- goal and acceptance criteria;
- constraints;
- changed paths;
- latest independent verification result;
- next action;
- selected skill instructions;
- prior compaction summary, recent public conversation, and user steering.

The projection has no authority and is not persisted as task truth. Context checkpoints
must record the exact projection or its content hash and source-event watermark so live
and resumed runs see the same continuation. Full tool outputs stay in trace artifacts;
the projection carries only relevant excerpts or retrieval handles.

## Compaction and handoff

Compaction is context maintenance, not a workflow transition. It must resume the same
model loop without synthesizing `blocked`, changing phase, or forcing a decision-only
turn. The target projection is Pi-like hybrid context: a compact Markdown summary plus
the exact recent model/tool tail. A cut may not separate a tool call from its result.

Branch handoff and user steering remain distinct operations. A branch handoff names a
new continuation owner; a work-batch yield returns control to the coordinator; context
compaction only reduces model-visible history.

## Comparison

Use three arms, with identical model, PTC runtime, budgets, observation bounds, trace,
and verifier:

| Arm | Purpose |
|---|---|
| Skein `structured` | Existing control |
| Skein `pi_compatible` | Treatment |
| Pi + Skein PTC v4.1 | External lightweight reference |

Report exact pass, partial/F2P/P2P, terminal cause, model calls, PTC cells, failed-test
to repair transitions, repeated reads, live-binding reuse, input/cache/output tokens,
latency, and cost. Do not use patch size as a quality proxy.

## Delivery phases

1. **Loop:** make host work-batch yields optional as part of `workflow.mode`, preserve
   safety limits, and never represent a host yield as a human blocker.
2. **Projection:** add bounded Markdown projection while leaving canonical events
   unchanged.
3. **Verification:** skip Skein's structured counterexample-review detour in compatibility
   mode; retain managed verification and return every failure for repair until the ordinary
   task/model/time budget is exhausted. Pi v4.1's one-shot pre-final evidence reminder
   remains a documented host-loop difference.
4. **Continuity:** retain the exact recent tool tail across genuine context compaction,
   persist projection checkpoints, and keep stable instructions outside summaries.
5. **Parity:** match Pi v4.1's compact direct-helper contract and observation limits
   only where the preceding comparison shows a remaining gap.
6. **Evaluation:** run the three-arm pinned comparison before changing defaults.

The two Skein arms are directly selectable in the evaluation path:

```bash
scripts/run_e13_ptc_isolation.sh skein-structured
scripts/run_e13_muse_20.sh pi-compatible
scripts/run_e13_ptc_isolation.sh pi-skein-v4
```

The implementation reuses ADK's exact recent session history rather than introducing
another memory subsystem. Each thin projection is also recorded with its content hash
and source-event watermark. `structured` remains the default until the three-arm
comparison establishes quality and cost.

## Acceptance criteria

- `workflow.mode` selects `structured` or `pi_compatible` with no topology change.
- Thin mode emits no PTC work-batch yield and no pre-verification review batch.
- Both modes append the same classes of canonical execution and verification events.
- Thin model input is readable Markdown rather than serialized ledger/event envelopes.
- Failed managed verification can re-enter implementation; verified completion remains
  host-owned.
- Thin terminal output may be ordinary prose; the adapter derives the host terminal state,
  and host-owned verification supplies criterion-level evidence.
- Focused tests cover mode parsing, projection shape, minimal terminal output, absence
  of batch yields, and failed-verification repair followed by verified completion.
