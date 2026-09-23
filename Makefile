.PHONY: install test lint format typecheck eval

install:
	uv sync --all-groups --extra eval

test:
	uv run --extra eval pytest \
		tests/unit/test_orchestration.py \
		tests/unit/test_runtime.py \
		tests/unit/test_context_windows.py \
		tests/unit/test_harness_config.py \
		tests/unit/test_harness_factory.py \
		tests/unit/test_notebook_ptc_integration.py \
		tests/unit/test_harbor_eval_runner.py \
		tests/unit/test_outcome.py \
		tests/unit/test_learning_episode.py

lint:
	uv run --extra eval ruff check app harness evals tests

format:
	uv run --extra eval ruff format .

typecheck:
	uv run --extra eval pyright

eval:
	.venv/bin/python scripts/run_harbor_eval.py
