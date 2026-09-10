# Development guidance

This repository is an Agents CLI-compatible Google ADK 2.x project.

## Required workflow

1. Read `docs/design/pi-inspired-adk-coding-harness.md` and `docs/TODO.md`.
2. Use the Google Agents CLI skills under `.agents/skills` when present.
3. Keep the model-facing tool surface limited to `read`, `bash`, `edit`, and `write` unless an ablation justifies another tool.
4. Keep volatile task/session state out of `static_instruction`.
5. Preserve deterministic serialization and stable prefix hashes.
6. Add deterministic tests for code contracts; use Agents CLI evals for non-deterministic model behavior.
7. Commit completed TODO items independently with focused commit messages.

## Safety

- The local environment adapter is not a production sandbox.
- Never enable network, push, deploy, or destructive commands by default.
- Tool mutations must be atomic and idempotent where practical.
- A model completion claim must pass deterministic verification.

## Code conventions

- Python 3.11+ and Pydantic v2.
- Type public interfaces.
- Keep ADK-specific wiring in `app/` or `harness/adapters/adk/`; keep core logic importable without cloud credentials.
- Do not assert on natural-language model output in pytest.
