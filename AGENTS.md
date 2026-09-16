# Coding guidance

- Read the relevant code and its callers before changing behavior. Fix the cause at the shared boundary when possible.
- Prefer existing code and standard library features. Keep the change small and avoid speculative abstractions.
- Validate non-trivial behavior with a focused, runnable check. Verify completion with evidence, not a claim.
- Keep model-facing context bounded and avoid rewriting stable instructions with task or session state.
- Preserve other work in the checkout. Stage only files you changed, and do not discard unrelated changes.
- Follow the user's instructions when they conflict with this guidance.
