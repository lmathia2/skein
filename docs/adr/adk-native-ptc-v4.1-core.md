# ADR: ADK-native PTC v4.1 core

Status: accepted and implemented

## Decision

Skein uses Google ADK as its only model/tool runtime and PTC v4.1 as its default coding
surface.

- ADK owns model calls, native function-call continuation, sessions, streaming,
  cancellation, and resume behavior.
- The model sees one `code` tool in the default profile.
- The PTC worker exposes brokered `read`, `bash`, `edit`, `write`, and `verify` helpers.
- The Skein workflow owns bounded work packets, budgets, steering boundaries, recovery
  markers, and independent verification around the ADK worker.
- Ordinary assistant prose ends a work batch. Structured model output is not required.
- The four direct coding tools remain an evaluation ablation, not a second production
  architecture.

## Why

The previous design accumulated two partial runtimes: ADK for provider integration and
a structured host loop for tool orchestration. That duplicated loop authority, message
semantics, recovery rules, and terminal parsing.

ADK already solves the native event loop. Skein's useful differentiation is elsewhere:
programmable tool use, effect mediation, evidence projections, recovery receipts, and
verified completion. Keeping those boundaries while deleting the second model/tool loop
makes the implementation closer to Pi's legibility without giving up ADK.

## Consequences

Positive:

- native ADK tool messages and tracing remain intact;
- one public tool keeps the model surface small;
- nested capabilities retain Skein's safety and evidence contracts;
- provider adapters do not need a custom structured-output protocol;
- a single verifier owns completion.

Tradeoffs:

- the outer workflow still has more machinery than Pi because it supports benchmark
  task state, steering, receipts, and verification;
- PTC introduces a persistent worker and checkpoint policy;
- direct four-tool parity must be measured through an explicit ablation.

## Implementation

- `app/agent/factory.py` composes the ADK app and plugins.
- `app/agent/config.py` selects `code` and builds the v4.1 instruction.
- `app/agent/builders.py` constructs the `LlmAgent` and model-facing tools.
- `app/agent/workflow.py` owns work-batch and verification transitions.
- `harness/core/models/outcome.py` enforces terminal host outcomes.

The completed migration phases are tracked in the
[implementation plan](../design/adk-ptc-v4.1-simplification-plan.md).
