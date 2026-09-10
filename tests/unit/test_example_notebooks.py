from __future__ import annotations

import json
from pathlib import Path

NOTEBOOKS = Path(__file__).resolve().parents[2] / "examples" / "notebooks"


def test_standalone_design_notebooks_execute_with_stdlib() -> None:
    paths = sorted(NOTEBOOKS.glob("*.ipynb"))
    assert [path.name for path in paths] == [
        "01_ptc_messages_and_repl_state.ipynb",
        "02_cache_aware_compaction.ipynb",
        "03_trace_memory_programs.ipynb",
    ]

    for path in paths:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        assert notebook["nbformat"] == 4
        namespace = {"__name__": "__main__"}
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                exec(compile(source, f"{path.name}:{cell['id']}", "exec"), namespace)
