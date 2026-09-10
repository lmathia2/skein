from __future__ import annotations

from pathlib import Path

import harness.execution.safety.approval as approval
import harness.execution.tools.adk_adapter as adk_adapter


def test_tools_and_approval_use_direct_modules_without_shadow_packages() -> None:
    assert Path(approval.__file__).name == "approval.py"
    assert Path(adk_adapter.__file__).name == "adk_adapter.py"
