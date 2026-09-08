# Trace-native harness notebooks

These notebooks are standalone, deterministic design fixtures. They use only the
Python standard library and mocked events; no provider credentials, Jupyter server,
DuckDB, LanceDB, or running harness is required.

1. [`01_ptc_messages_and_repl_state.ipynb`](01_ptc_messages_and_repl_state.ipynb)
   separates model messages, live CPython variables, durable notebook cells, and the
   append-only ledger.
2. [`02_cache_aware_compaction.ipynb`](02_cache_aware_compaction.ipynb) demonstrates
   trigger policy, structured continuation handoff, cache epochs, and Pi/Codex-inspired
   strategy choices.
3. [`03_trace_memory_programs.ipynb`](03_trace_memory_programs.ipynb) uses an in-memory
   SQLite ledger to demonstrate versioned, cached factual, episodic, semantic, progress,
   and prompt-assembly programs.
4. [`04_pluggable_ptc_tools.ipynb`](04_pluggable_ptc_tools.ipynb) compares the
   `skein_notebook`/`execute_code` and `adk_code_mode`/`execute_code` state contracts while
   showing that both use the same host effect broker.
5. [`05_optional_memory_strategies.ipynb`](05_optional_memory_strategies.ipynb)
   compares disabled memory, trace-native versioned programs, and Pi-derived structured
   compaction with a recent raw tail.

Run them in any notebook UI, or verify every code cell without notebook dependencies:

```bash
.venv/bin/pytest -q tests/unit/test_example_notebooks.py
```

The notebooks explain architecture and proposed tuning points. They do not enable the
still-gated live prompt/view cutover or live Lance retrieval.
