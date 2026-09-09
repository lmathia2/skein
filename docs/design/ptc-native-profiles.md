# Enabling native PTC implementations

The `notebook_ptc` configuration key remains compatible. `serialization: native`
and `state: native` preserve each implementation's native pairing; they do not
select a universal serializer or promise interchangeable state recovery.

| Implementation | Serialization | State | Enabling requirements |
| --- | --- | --- | --- |
| `skein_notebook` | `native` / `notebook` | `native` / `replay_safe` | Existing notebook profile |
| `adk_code_mode` | `native` | `native` / `none` | Explicit `adk_code_mode_image` and running Docker Engine; Python SDK bundled |
| `prime_repl` | `native` / `jsonl` | `native` / `snapshot` | `prime_native_execution: true`, trusted project; dill bundled |

Set `enabled: true` with the selected implementation. Prime's opt-in composition is
[`prime-ptc-jsonl.yaml`](../../harness/config/profiles/prime-ptc-jsonl.yaml).
The default composition and existing notebook/ADK behavior are unchanged. Known
unimplemented combinations raise `NotImplementedError`; malformed values and
missing trust/image requirements are validation errors. No fallback is performed.

No PTC-specific pip extras are required. The old extra names remain empty compatibility
aliases. Pinned dill and Docker SDK sources and licenses live in `harness/_vendor`;
base ADK HTTP dependencies are reused. Docker Engine and the configured image are
not vendorable Python dependencies, and must still be provisioned separately.

## Prime adapter contract

Skein vendors the MIT-licensed Python runtime at revision
`bf8894afa55832f7cfa2094c8a0d041bc680a691`. This is its CPython JSON-lines protocol,
not an IPython kernel or an `.ipynb` document. Skein supplies the host lifecycle and
task event writer; it does not copy Prime's TypeScript session host.

- A single `execute_code` retains Python names, top-level await, native `bash`, and
  `emit` display payloads. Assignments made before a Python exception remain live.
- Each cell has a submitted receipt and a redacted terminal result in the owned
  task event stream. The default JSONL event store supplies the transcript, which
  is distinct from selecting the canonical memory ledger backend.
- After each completed cell, including ordinary Python errors, the adapter writes
  an immutable dill snapshot and commits its hashes in a receipt. Restart checks
  revision, Python version, workspace, path confinement and hashes before restore.
  Only serializable names restore; skipped names are recorded. This is not replay,
  an atomic heap checkpoint, or rollback of filesystem effects. Background work
  after a snapshot is not durably captured. Cadence is Skein's, not Prime's host's.
- One process owns each task's snapshot directory. Duplicate calls with a stable
  tool-call identity reuse the terminal receipt. Interrupted or uncertain attempts
  block continuation instead of re-executing native side effects. Missing latest
  snapshots also block restart and completion. An operator must reconcile these
  cases; no automatic reconciliation command is provided.
- Native Python/bash have the user's OS authority, including network and files;
  Skein's command allowlists do **not** constrain them. Project trust is mandatory.
  Dill can execute code and contains private, unredacted state. Only restore
  snapshots owned by this trusted run; a hash is integrity, not authentication.
- Close the owning run before physical task erasure. Erasure also removes its
  complete snapshot directory. Process-group termination is best effort, not a
  sandbox guarantee against deliberately detached descendants.

Inline output is bounded. Display payloads are retained as structured results;
provider-specific image attachment rendering is not implemented by this adapter.
Conversation continuity, safe-auto recovery, active brokered memory commands,
Prime notebook serialization, and notebook heap snapshots are rejected until their
contracts and tests exist. The existing notebook profile remains the choice for
brokered capabilities and safe replay.

The shared `PtcSession` is deliberately a lifecycle bundle used by all three
adapters, not a speculative plugin framework. Cross-serializer and state-policy
adapters can be added behind it when implemented, without altering native defaults.

## Explicit memory program selection

The existing memory program service accepts optional version pins independently of
the PTC implementation and canonical ledger format:

```yaml
memory:
  enabled: true
  context_programs:
    mode: active
    programs:
      history.page: 1
      event.read: 1
      events.count: 1
      artifact.read: 1
```

Omitting `programs` preserves the existing allowlist. An explicit mapping restricts
model-visible queries to those name/version pairs; an empty mapping disables them.
`failures.by_kind: 1` additionally requires the existing `reuse: true` gate. Unknown
programs, unimplemented versions, and unavailable gated programs fail configuration
with `NotImplementedError`. Shadow mode requires `events.count: 1` for its fixed probe.
Existing view receipts retain source, parameter, program and result hashes. This is
selection of built-in reviewed programs, not loading arbitrary code or replacing all
internal prompt construction. Prime's active memory bridge remains unavailable.
