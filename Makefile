.PHONY: install test lint format typecheck eval

install:
	uv sync --all-groups --extra eval

test:
	uv run --extra eval pytest tests/unit/test_harbor_adapter.py tests/unit/test_harbor_eval_runner.py

lint:
	uv run --extra eval ruff check .

format:
	uv run --extra eval ruff format .

typecheck:
	uv run --extra eval pyright

eval:
	.venv/bin/python scripts/run_harbor_eval.py
