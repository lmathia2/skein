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

### Prebuild and reuse the ADK image

Build once, then use the cached image for subsequent invocations:

```bash
docker build -t skein-adk-code-mode:1 harness/adk/code_mode_sandbox
docker image inspect skein-adk-code-mode:1 --format '{{.Id}}'
```

Set `notebook_ptc.adk_code_mode_image` to the returned `sha256:...` image ID
(immutable on this Docker daemon), or the local tag for development. The Dockerfile
pins its Python base by digest and copies only the vendored sandbox Python sources.
There is no pip install or rebuild during a run. Rebuild after sandbox source updates.
This image implements the backend's TCP transport, not the optional HTTP server.

Normal runs keep one container alive for the code blocks in one invocation and then
remove it. Trusted sequential evals may instead call `run_evaluation_batch`: its
exclusive `ReusableDockerBackend` resolves the configured image to an immutable ID,
starts one warm container, and creates a fresh authenticated interpreter connection
per example. Before and after every lease it kills non-owner processes, verifies that
none remain, and clears `/workspace`, `/tmp`, and the generated tool tree. Reset
failure discards the container. Only the private tool directory (read-only) and
workspace slot (read-write) are mounted; example state and authorization roots must
be distinct and outside the worker root. The worker root must be on a host path shared
with Docker Desktop. Each example still receives a fresh harness assembly, ADK
session, task event store, and model context.

This pool is process-local. Launchers that start a new Skein process per example do
not share it; cross-process Harbor reuse requires launcher-owned container leasing and
is intentionally not implied by this API.

For Colima or another non-default Docker context, the SDK needs the daemon address:

```bash
export DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
SKEIN_TEST_ADK_IMAGE=skein-adk-code-mode:1 uv run --no-sync pytest tests/integration/test_adk_container_image.py
```

The opt-in test checks state reuse across two blocks in one example, then verifies a
fresh namespace, empty workspace and temp directory, terminated background process,
read-only tools, and excluded symlink in the next lease of the same container. It
removes its test container and leaves the image cached. Docker must remain running;
no image or container is published externally.

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
