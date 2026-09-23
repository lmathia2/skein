# ADK-native PTC v4.1 core

> Status: accepted and implemented; live qualification pending
>
> Updated: 2026-09-22

## Context

Skein currently combines ADK's native model/tool continuation with a larger host
workflow containing model-authored task phases, structured routing, several durable
state representations, and multiple model-facing execution modes. That breadth makes
the main coding path harder to reason about and obscures the boundaries that matter:
effect authority, exact model-visible context, recovery, and independent verification.

Pi demonstrates that a coding loop can remain legible when one runtime owns
continuation. Strands contributes useful lifecycle primitives—typed stop reasons,
safe-boundary checkpoints, capability narrowing, construction-time collision checks,
and bounded cancellation-aware execution—without requiring its broader tool or plugin
topology.

PTC v4.1 is Skein's selected model-facing interface. Google ADK remains the required
runtime and must continue to own model execution, tool continuation, streaming,
sessions, cancellation, and resumability.

## Decision

Implement the direction specified by the
[ADK-native Skein simplification plan](../design/adk-ptc-v4.1-simplification-plan.md):

1. Use one ADK coding agent with exactly one model-facing tool: PTC v4.1 `code`.
   `execute_code` remains an internal Python callable name, not a wire-level tool name.
2. Keep `read`, `write`, `edit`, `bash`, and `verify` as synchronous PTC guest helpers
   behind the existing Skein effect broker.
3. Reduce the outer ADK workflow to coding, deterministic host verification, and a
   bounded retry-or-finish transition.
4. Remove model-authored task phases and routing from the model-facing path.
5. Retain one byte-stable prompt/tool prefix and one deterministic bounded dynamic
   context path.
6. Preserve native ADK/provider tool-call and tool-result message semantics.
7. Treat every model-visible tool result as a versioned bounded projection linked to
   complete typed evidence and retained artifacts.
8. Converge model, PTC, broker, context, and verifier events on one append-only
   canonical Skein trace while keeping the ADK session authoritative for conversation
   continuation.
9. Record recovery boundaries before model dispatch, after model output, after effects,
   and after verification; reconcile uncertain effects before resuming.
10. Keep host verification as the sole completion authority. PTC `verify()` remains
    model-invoked evidence acquisition.
11. Derive learning episodes only from independently verified traces. Learned memory,
    skills, prompt changes, and context policies remain shadowed or opt-in until
    held-out quality and efficiency gates pass.

## Consequences

### Positive

- ADK has one unambiguous role as the agent runtime rather than one loop inside another.
- PTC v4.1 becomes the stable default instead of a coequal experimental tool surface.
- Effects and completion retain Skein's stronger authority model.
- Exact prompts, provider requests, tool messages, model projections, and causal traces
  remain comparable in strict evaluation arms.
- Structured workflow selection, duplicate packet builders, and provider output schemas
  are removed from the application path.
- Learning becomes reproducible and independently gated instead of mutating runtime
  behavior opportunistically.

### Negative

- Some current ledger fields and progress views become derived rather than directly
  authored.
- Recovery tests must cover the boundary between ADK session state and Skein effect
  evidence explicitly.
- Making PTC v4.1 the default requires new qualification; historical comparison runs do
  not prove the simplified ADK workflow is equivalent.

## Rejected alternatives

- **Replace ADK with a custom Python loop.** Rejected because ADK ownership is
  non-negotiable and already provides the required event-loop machinery.
- **Use four direct coding tools as the default.** Rejected; PTC v4.1 is the selected
  default model surface.
- **Adopt Strands' Monty caller.** Rejected because it is stateless and does not provide
  PTC v4.1 continuity or recovery.
- **Adopt a general middleware framework.** Rejected because ADK callbacks/plugins and
  the Skein broker already provide the necessary boundaries.
- **Make the ADK session the effect ledger.** Rejected because conversation replay does
  not establish effect certainty, projection provenance, or verification authority.
- **Promote learned memory immediately.** Rejected because existing qualification
  reduced some rereads but did not pass call, token, or cost gates.

## Implementation tracking

The linked plan is the single source for phases, tests, rollout gates, and definition
of done. Phases 1–8 are implemented. The strict Pi/Skein adapters remain outside the
application workflow for live qualification; they do not reintroduce a mode switch.
