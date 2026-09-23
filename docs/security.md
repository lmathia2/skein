# Skein security model

Skein executes model-selected code and commands. Prompts influence behavior but do not
authorize effects. The enforceable boundary is the host execution layer.

## Trust boundaries

Untrusted inputs include user text, repository content, project scripts, model output,
PTC source, and command output. The trusted control plane consists of workspace
confinement, command policy, approvals, secret redaction, receipts, recovery checks,
and deterministic verification.

Project-local instructions and skills load only when project trust is explicitly
enabled. Trust is a runtime binding so reusing YAML in a different checkout cannot
silently trust that repository.

## Files and commands

File operations resolve paths beneath the configured workspace and reject traversal or
symlink escape. Edits and writes support expected hashes for optimistic concurrency.

Commands are classified at the execution boundary. The default policy allows bounded
inspection, build, test, and workspace-local mutation; it requires approval for
dependency installation, network access, unknown executables, publication, deployment,
and Git history mutation; destructive operations are denied.

The host-local adapter is not an OS sandbox. Production use must select an isolated
runtime. The production gate rejects the local adapter before opening a service.

PTC does not widen authority. Direct filesystem, process, and network imports are
blocked in the worker, and its `read`, `bash`, `edit`, `write`, and `verify` helpers
enter the same execution boundary as direct ADK tools.

## Approvals and receipts

Approval binds the exact normalized operation fingerprint to the current task and
expires. A denial, timeout, or cancellation does not execute the operation. Approval
does not imply success; the subsequent receipt records what happened.

File mutations are content-addressed where possible so an exact replay can return the
prior receipt. External writes, publishing, deployment, and irreversible operations
must provide their own idempotency mechanism and are never blindly replayed.

An interrupted operation with no provable result is an unknown effect. Skein blocks
automatic completion or replay until it is reconciled.

## Secrets and traces

Known secret values, common credential formats, authorization headers, private keys,
and sensitive mapping keys are redacted before model-visible output and trace
persistence. Tool output is bounded and large bodies are retained only through guarded
artifact references.

Pattern redaction cannot guarantee discovery of every secret. Protect the complete
state directory, including the ADK SQLite session store. Do not pass the complete host
environment into a model-selected command.

Local trace content modes are `off`, `metadata`, and `redacted`; there is no raw mode.
Operators still own retention, access control, encryption, and backup policy.

## Recovery and completion

ADK owns invocation resume and cancellation. Skein records recovery boundaries,
workspace fingerprints, receipts, and PTC checkpoints around that lifecycle. Resume
fails closed when current workspace state cannot be explained by recorded evidence.

The model cannot authorize completion. A mutation requires independent host
verification against the actual workspace. `HarnessOutcome(status="complete")` is
invalid without a passing verification report, and an unknown effect prevents it.

## Deployment minimums

- isolated task worktree and artifact directory;
- network disabled by default and allowlisted per operation;
- CPU, memory, process, output, and wall-time limits;
- short-lived, operation-scoped credentials;
- approval for installation, publication, deployment, and Git mutation;
- adversarial tests for traversal, shell parsing, prompt injection, secret leakage,
  duplicate mutation, stale receipts, and workspace mismatch.
