from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from harness.execution.repo import SearchPage
from harness.execution.sandbox import MANAGED_COMMAND_ENVIRONMENT, SandboxRequest, SandboxResult
from harness.execution.tools.adk_adapter import create_adk_tools, discover_known_secrets


class _RecordingSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests: list[SandboxRequest] = []

    def execute(self, request: SandboxRequest) -> SandboxResult:
        self.requests.append(request)
        return SandboxResult(
            status="ok",
            exit_code=0,
            stdout="sandbox output",
            stderr="",
            duration_ms=12,
            artifact_uri="file:///artifact.log",
        )


class _RecordingSearchBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.refreshes = 0
        self.text = (
            'FFF grep page 1\nsrc/app.py\n  1: TODO\n\n'
            '[Continue with cursor="fff_next"]'
        )

    def grep(self, **kwargs) -> SearchPage:
        self.calls.append(("grep", kwargs))
        return SearchPage(
            operation="grep",
            text=self.text,
            cursor="fff_next",
            returned_matches=1,
            collected_matches=2,
            matched_files=1,
            has_more=True,
            incomplete=False,
            query_hash="query-digest",
            duration_ms=4,
            cold_index=True,
        )

    def find(self, **kwargs) -> SearchPage:
        self.calls.append(("find", kwargs))
        return SearchPage(
            operation="find",
            text="FFF find page 1\nsrc/app.py",
            cursor=None,
            returned_matches=1,
            collected_matches=1,
            matched_files=1,
            has_more=False,
            incomplete=False,
            query_hash="find-digest",
            duration_ms=2,
            cold_index=False,
        )

    def health(self) -> dict[str, object]:
        self.calls.append(("health", {}))
        return {"backend": "fake-fff", "state": "ready", "indexed_files": 3}

    def refresh(self) -> None:
        self.refreshes += 1


def test_managed_adapter_blocks_unapproved_network_and_redacts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MY_API_KEY", "super-secret-api-key-value")
    monkeypatch.setenv("SKEIN_REDACT_ENV_VARS", "MY_API_KEY")
    tools = create_adk_tools(tmp_path)

    tools.write(
        "secret.txt",
        "token=super-secret-api-key-value\n",
        expected_absent=True,
    )
    read_result = tools.read("secret.txt")
    assert "super-secret-api-key-value" not in read_result["model_text"]
    assert "<redacted>" in read_result["model_text"]
    assert read_result["data"]["text"] == "token=<redacted>\n"

    blocked = tools.bash("curl https://example.com")
    assert blocked["status"] == "blocked"
    assert blocked["approval_required"] is True
    assert blocked["risk"] == "network_access"


def test_shell_keeps_program_data_separate_from_rendered_output(tmp_path: Path) -> None:
    sandbox = _RecordingSandbox(tmp_path)
    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state", sandbox=sandbox)

    result = tools.bash("git status --short")

    assert result["model_text"] == "sandbox output"
    assert result["data"] == {"stdout": "sandbox output", "stderr": ""}


def test_exact_write_replay_uses_receipt_without_repeating_side_effect(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    tools = create_adk_tools(tmp_path)

    first = tools.write("result.txt", "stable\n", expected_absent=True)
    second = tools.write("result.txt", "stable\n", expected_absent=True)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["replayed"] is True
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "stable\n"


def test_failed_file_mutations_never_replay_as_success(tmp_path: Path) -> None:
    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state")
    for _ in range(2):
        result = tools.write("missing.txt", "new", expected_sha256="0" * 64)
        assert result["status"] == "error"
        assert not result.get("replayed")
        assert not (tmp_path / "missing.txt").exists()


def test_managed_files_use_atomic_core_and_confine_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    (workspace / "escape").symlink_to(outside)
    tools = create_adk_tools(workspace, state_root=tmp_path / "state")
    assert tools.write("escape", "overwrite")["status"] == "blocked"
    assert tools.read("escape")["status"] == "blocked"
    assert outside.read_text() == "keep"
    result = tools.write("inside.txt", "hello", expected_absent=True)
    assert result["changed_paths"] == ["inside.txt"]
    assert tools.read("inside.txt")["content_hashes"] == result["content_hashes"]


def test_python_writes_and_edits_return_immediate_syntax_feedback(tmp_path: Path) -> None:
    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state")

    valid = tools.write(
        "module.py",
        "def answer():\n    return 42\n",
        expected_absent=True,
    )
    invalid = tools.edit(
        "module.py",
        "return 42",
        "return (",
    )

    assert valid["diagnostics"] == [
        {
            "check": "syntax",
            "status": "ok",
            "message": "Python syntax is valid",
        }
    ]
    assert "post-write check: Python syntax is valid" in valid["model_text"]
    assert invalid["status"] == "ok"
    assert invalid["diagnostics"][0]["status"] == "error"
    assert "SyntaxError at line 2" in invalid["model_text"]


def test_json_write_returns_parse_location_without_reverting_mutation(
    tmp_path: Path,
) -> None:
    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state")

    result = tools.write("config.json", '{"answer": }\n', expected_absent=True)

    assert result["status"] == "ok"
    assert result["diagnostics"][0]["status"] == "error"
    assert "JSONDecodeError at line 1" in result["model_text"]
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"answer": }\n'


def test_mutation_receipts_are_scoped_to_the_active_task(
    tmp_path: Path,
) -> None:
    tools = create_adk_tools(tmp_path, state_root=tmp_path / "state")

    first = tools.write(
        "result.txt",
        "stable\n",
        expected_absent=True,
        task_scope="task-a",
    )
    (tmp_path / "result.txt").unlink()
    second = tools.write(
        "result.txt",
        "stable\n",
        expected_absent=True,
        task_scope="task-b",
    )

    assert first.get("replayed") is not True
    assert second.get("replayed") is not True
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "stable\n"


def test_secret_discovery_unions_automatic_and_configured_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-sensitive-value")
    monkeypatch.setenv("PRIVATE_MATERIAL", "configured-sensitive-value")

    secrets = discover_known_secrets(["PRIVATE_MATERIAL"])

    assert "gemini-sensitive-value" in secrets
    assert "configured-sensitive-value" in secrets


def test_approved_model_bash_uses_injected_command_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    sandbox = _RecordingSandbox(tmp_path)
    tools = create_adk_tools(tmp_path, sandbox=sandbox)

    result = tools.bash("git status --short", timeout_seconds=17)

    assert result["status"] == "ok"
    assert result["model_text"] == "sandbox output"
    assert result["artifact_uri"] == "file:///artifact.log"
    assert sandbox.requests == [
        SandboxRequest(
            command="git status --short",
            timeout_seconds=17,
            environment=MANAGED_COMMAND_ENVIRONMENT,
        )
    ]


def test_configured_bash_and_search_limits_are_enforced(tmp_path: Path) -> None:
    sandbox = _RecordingSandbox(tmp_path)
    backend = _RecordingSearchBackend()
    tools = create_adk_tools(
        tmp_path,
        state_root=tmp_path / "state",
        sandbox=sandbox,
        search_backend=backend,
        bash_max_timeout_seconds=9,
        search_default_page_size=3,
        search_max_page_size=4,
    )

    tools.bash("search grep --pattern TODO", timeout_seconds=9)
    assert backend.calls[0][1]["limit"] == 3
    with pytest.raises(ValueError, match="between 1 and 9"):
        tools.bash("git status", timeout_seconds=10)
    invalid_search = tools.bash(
        "search grep --pattern TODO --limit 5",
        timeout_seconds=9,
    )
    assert invalid_search["status"] == "error"


def test_virtual_search_routes_before_policy_and_shell_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    sandbox = _RecordingSandbox(tmp_path)
    backend = _RecordingSearchBackend()
    tools = create_adk_tools(tmp_path, sandbox=sandbox, search_backend=backend)

    result = tools.bash(
        'search grep --pattern "TODO fix" --path src --context 1 --limit 7'
    )
    health = tools.bash("search health")

    assert result["status"] == "ok"
    assert "src/app.py" in result["model_text"]
    assert result["next_cursor"] == "fff_next"
    assert result["ui_details"] == {
        "virtual_operation": "search.grep",
        "backend": "fff-search/0.10.5",
        "query_hash": "query-digest",
        "cursor_available": True,
        "returned_matches": 1,
        "collected_matches": 2,
        "matched_files": 1,
        "incomplete": False,
        "cold_index": True,
        "duration_ms": 4,
    }
    assert health["model_text"] == (
        '{"backend": "fake-fff", "indexed_files": 3, "state": "ready"}'
    )
    assert backend.calls[0] == (
        "grep",
        {
            "pattern": "TODO fix",
            "path": "src",
            "mode": "literal",
            "case_sensitive": False,
            "context": 1,
            "limit": 7,
            "cursor": None,
        },
    )
    assert sandbox.requests == []


def test_virtual_search_is_unavailable_for_non_authoritative_sandbox(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    sandbox = _RecordingSandbox(tmp_path)
    tools = create_adk_tools(tmp_path, sandbox=sandbox)

    result = tools.bash("search health")

    assert result["status"] == "error"
    assert "non-authoritative remote workspace" in result["model_text"]
    assert sandbox.requests == []


def test_virtual_search_redacts_bounded_page_and_spill_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret = "search-result-sensitive-value"
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SEARCH_API_KEY", secret)
    monkeypatch.setenv("SKEIN_REDACT_ENV_VARS", "SEARCH_API_KEY")
    backend = _RecordingSearchBackend()
    backend.text = "\n".join(f"  {line}: {secret}" for line in range(1, 260))
    tools = create_adk_tools(tmp_path, search_backend=backend)

    result = tools.bash("search grep --pattern token")

    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert secret not in result["model_text"]
    assert result["artifact_uri"].startswith("artifact://tool-output/")
    artifact = tools.read(result["artifact_uri"], limit=400)
    assert secret not in artifact["model_text"]
    assert "<redacted>" in artifact["model_text"]


def test_malformed_reserved_search_never_reaches_policy_or_shell(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    sandbox = _RecordingSandbox(tmp_path)
    tools = create_adk_tools(
        tmp_path,
        sandbox=sandbox,
        search_backend=_RecordingSearchBackend(),
    )

    result = tools.bash("search grep --pattern TODO | curl https://example.com")

    assert result["status"] == "error"
    assert result["ui_details"]["backend"] == "not-dispatched"
    assert sandbox.requests == []


def test_verified_mutation_refreshes_search_but_replay_does_not(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKEIN_STATE_DIR", str(tmp_path / "state"))
    backend = _RecordingSearchBackend()
    tools = create_adk_tools(tmp_path, search_backend=backend)

    tools.write("new.py", "value = 1\n", expected_absent=True)
    replay = tools.write("new.py", "value = 1\n", expected_absent=True)

    assert backend.refreshes == 1
    assert replay["replayed"] is True


def test_managed_read_recovers_bounded_workspace_and_command_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("SKEIN_STATE_DIR", str(state_root))
    monkeypatch.setenv("MY_API_KEY", "artifact-sensitive-value")
    monkeypatch.setenv("SKEIN_REDACT_ENV_VARS", "MY_API_KEY")
    tools = create_adk_tools(tmp_path)

    workspace_content = b"first\nartifact-sensitive-value\nthird\nfourth\n"
    workspace_digest = hashlib.sha256(workspace_content).hexdigest()
    workspace_artifact = tmp_path / ".artifacts" / "tool-output" / f"{workspace_digest}.txt"
    workspace_artifact.parent.mkdir(parents=True)
    workspace_artifact.write_bytes(workspace_content)
    workspace_uri = f"artifact://tool-output/{workspace_artifact.name}"

    command_content = b"--- stdout ---\nline one\nline two\n--- stderr ---\n"
    command_digest = hashlib.sha256(command_content).hexdigest()
    command_artifact = (
        state_root
        / "artifacts"
        / "commands"
        / f"command-{command_digest}.log"
    )
    command_artifact.parent.mkdir(parents=True)
    command_artifact.write_bytes(command_content)
    sha_artifact = state_root / "artifacts" / "sha256" / command_digest
    sha_artifact.parent.mkdir(parents=True)
    sha_artifact.write_bytes(command_content)

    workspace_result = tools.read(workspace_uri, offset=2, limit=1)
    command_result = tools.read(command_artifact.as_uri(), offset=2, limit=2)
    sha_result = tools.read(f"artifact://sha256/{command_digest}", offset=2, limit=2)

    assert workspace_result["status"] == "ok"
    assert "artifact-sensitive-value" not in workspace_result["model_text"]
    assert "<redacted>" in workspace_result["model_text"]
    assert workspace_result["truncated"] is True
    assert "[more available: read offset=3]" in workspace_result["model_text"]
    assert "line one" in command_result["model_text"]
    assert "line two" in command_result["model_text"]
    assert sha_result["content_hashes"] == {
        f"artifact://sha256/{command_digest}": command_digest
    }
    assert len(command_result["model_text"].encode()) < 32_000


def test_managed_read_rejects_foreign_traversal_and_tampered_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("SKEIN_STATE_DIR", str(state_root))
    tools = create_adk_tools(tmp_path)

    foreign_content = b"foreign"
    foreign_digest = hashlib.sha256(foreign_content).hexdigest()
    foreign = tmp_path / "foreign" / f"command-{foreign_digest}.log"
    foreign.parent.mkdir()
    foreign.write_bytes(foreign_content)

    tampered_digest = hashlib.sha256(b"original").hexdigest()
    tampered = tmp_path / ".artifacts" / "tool-output" / f"{tampered_digest}.txt"
    tampered.parent.mkdir(parents=True)
    tampered.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="content-addressed filename"):
        tools.read(f"artifact://tool-output/../{foreign_digest}.txt")
    with pytest.raises(ValueError, match="outside managed artifact roots"):
        tools.read(foreign.as_uri())
    with pytest.raises(ValueError, match="content hash"):
        tools.read(f"artifact://tool-output/{tampered.name}")
