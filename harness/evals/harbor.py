"""Host-side Pier adapter for Skein's Harbor-compatible task environments."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import shlex
import tempfile
import time
from collections.abc import Coroutine, Sequence
from contextlib import suppress
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment, ExecResult
    from harbor.models.agent.context import AgentContext
else:
    try:
        from pier.agents.base import BaseAgent
        from pier.environments.base import BaseEnvironment, ExecResult
        from pier.models.agent.context import AgentContext
    except ImportError:  # Harbor remains a test/runtime compatibility fallback.
        from harbor.agents.base import BaseAgent
        from harbor.environments.base import BaseEnvironment, ExecResult
        from harbor.models.agent.context import AgentContext
from typing_extensions import override

from app.agent.factory import default_harness_registry
from harness.config import DEFAULT_COMPOSITION_PATH, SkeinConfig
from harness.environment import (
    ExecutionRuntime,
    FileConflictError,
    FileMutationResult,
    RepositoryRuntime,
    WorkspaceViolationError,
    sha256_bytes,
)
from harness.evals.runner import (
    EvaluationRunRequest,
    EvaluationRunResult,
    ReasoningEffort,
    run_evaluation_sync,
    write_evaluation_result,
)
from harness.models.task import TaskRequest
from harness.repo import RepositoryManifest, repository_manifest_from_snapshot
from harness.sandbox import CommandSandbox, SandboxRequest, SandboxResult
from harness.sandbox.output import bounded_result, environment_secret_values
from harness.server.bootstrap import build_server_assembly


class _AsyncBridge:
    """Let Skein's synchronous tools call Harbor's owning asyncio loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def call(self, operation: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(operation, self.loop).result()


class HarborWorkspaceEnvironment:
    def __init__(
        self,
        environment: BaseEnvironment,
        bridge: _AsyncBridge,
        root: str,
    ) -> None:
        self._environment = environment
        self._bridge = bridge
        self.root = Path(root)

    def resolve(self, path: str | Path, *, must_exist: bool = False) -> Path:
        raw = str(path)
        if not raw or "\x00" in raw or raw.startswith("~"):
            raise WorkspaceViolationError(f"Unsafe workspace path: {path}")
        candidate = raw if raw.startswith("/") else posixpath.join(self.root.as_posix(), raw)
        normalized = posixpath.normpath(candidate)
        try:
            confined = posixpath.commonpath((self.root.as_posix(), normalized)) == self.root.as_posix()
        except ValueError:
            confined = False
        if not confined:
            raise WorkspaceViolationError(f"Path leaves workspace: {path}")
        resolved = Path(normalized)
        if must_exist and not self._bridge.call(
            self._environment.is_file(resolved.as_posix())
        ):
            raise FileNotFoundError(f"Workspace path does not exist: {path}")
        return resolved

    def relative_path(self, path: Path) -> str:
        return PurePosixPath(path.as_posix()).relative_to(
            PurePosixPath(self.root.as_posix())
        ).as_posix()

    def read_bytes(self, path: str | Path) -> bytes:
        source = self.resolve(path, must_exist=True)
        descriptor, temporary = tempfile.mkstemp(prefix="skein-harbor-read-")
        os.close(descriptor)
        target = Path(temporary)
        try:
            self._bridge.call(self._environment.download_file(source.as_posix(), target))
            return target.read_bytes()
        finally:
            target.unlink(missing_ok=True)

    def atomic_write(
        self,
        path: str | Path,
        content: bytes,
        *,
        expected_sha256: str | None = None,
        expected_absent: bool = False,
    ) -> FileMutationResult:
        target = self.resolve(path)
        exists = self._bridge.call(self._environment.is_file(target.as_posix()))
        before = self.read_bytes(target) if exists else b""
        before_hash = sha256_bytes(before) if exists else None
        after_hash = sha256_bytes(content)
        if expected_absent and exists:
            if before == content:
                return self._unchanged(target, before_hash, after_hash, "already contained")
            raise FileConflictError(f"Expected new file but path already exists: {path}")
        if expected_sha256 is not None and before_hash != expected_sha256:
            if before == content:
                return self._unchanged(target, before_hash, after_hash, "already contained")
            raise FileConflictError(
                f"File hash changed for {path}: expected {expected_sha256}, found {before_hash}"
            )
        if before == content:
            return self._unchanged(target, before_hash, after_hash, "no change")

        descriptor, temporary = tempfile.mkstemp(prefix="skein-harbor-write-")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        local = Path(temporary)
        remote = target.with_name(f".{target.name}.skein-{uuid4().hex}")
        quoted_parent = shlex.quote(target.parent.as_posix())
        quoted_target = shlex.quote(target.as_posix())
        quoted_remote = shlex.quote(remote.as_posix())
        try:
            self._require(self._bridge.call(self._environment.exec(f"mkdir -p {quoted_parent}")))
            self._bridge.call(self._environment.upload_file(local, remote.as_posix()))
            command = (
                f"if [ -e {quoted_target} ]; then chmod --reference={quoted_target} {quoted_remote}; fi; "
                f"mv -f -- {quoted_remote} {quoted_target}"
            )
            self._require(self._bridge.call(self._environment.exec(command)))
        finally:
            local.unlink(missing_ok=True)
            with suppress(Exception):
                self._bridge.call(
                    self._environment.exec(f"rm -f -- {quoted_remote}", timeout_sec=10)
                )
        from harness.environment.local import _text_diff

        return FileMutationResult(
            path=self.relative_path(target),
            changed=True,
            before_sha256=before_hash,
            after_sha256=after_hash,
            diff=_text_diff(self.relative_path(target), before, content),
        )

    def replace_text(
        self,
        path: str | Path,
        old_text: str,
        new_text: str,
        *,
        expected_sha256: str | None = None,
    ) -> FileMutationResult:
        before = self.read_bytes(path)
        before_hash = sha256_bytes(before)
        if expected_sha256 is not None and before_hash != expected_sha256:
            raise FileConflictError(
                f"File hash changed for {path}: expected {expected_sha256}, found {before_hash}"
            )
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FileConflictError(f"Cannot apply text edit to binary file: {path}") from error
        occurrences = text.count(old_text)
        if occurrences == 0:
            if new_text and new_text in text:
                return self._unchanged(
                    self.resolve(path), before_hash, before_hash, "requested replacement already present"
                )
            raise FileConflictError(f"Exact edit preimage not found in {path}")
        if occurrences != 1:
            raise FileConflictError(
                f"Exact edit preimage occurs {occurrences} times in {path}; provide more context"
            )
        return self.atomic_write(
            path,
            text.replace(old_text, new_text, 1).encode(),
            expected_sha256=before_hash,
        )

    def _unchanged(
        self,
        target: Path,
        before_hash: str | None,
        after_hash: str,
        detail: str,
    ) -> FileMutationResult:
        return FileMutationResult(
            path=self.relative_path(target),
            changed=False,
            before_sha256=before_hash,
            after_sha256=after_hash,
            diff=f"({detail})",
            already_applied=True,
        )

    @staticmethod
    def _require(result: ExecResult) -> None:
        if result.return_code != 0:
            raise OSError(result.stderr or result.stdout or "Harbor environment operation failed")


class HarborCommandSandbox(CommandSandbox):
    def __init__(
        self,
        environment: BaseEnvironment,
        bridge: _AsyncBridge,
        root: str,
        artifact_root: Path,
        *,
        max_output_bytes: int,
        known_secrets: Sequence[str],
    ) -> None:
        self._environment = environment
        self._bridge = bridge
        self.workspace = Path(root)
        self.artifact_root = artifact_root
        self.max_output_bytes = max_output_bytes
        self.known_secrets = tuple(known_secrets)

    def execute(self, request: SandboxRequest) -> SandboxResult:
        started = time.monotonic()
        try:
            result = self._bridge.call(
                self._environment.exec(
                    request.command,
                    cwd=self.workspace.as_posix(),
                    env=dict(request.environment) or None,
                    timeout_sec=request.timeout_seconds,
                )
            )
            return bounded_result(
                status="ok" if result.return_code == 0 else "error",
                exit_code=result.return_code,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=(
                    *self.known_secrets,
                    *environment_secret_values(dict(request.environment)),
                ),
            )
        except (TimeoutError, RuntimeError) as error:
            if isinstance(error, RuntimeError) and "timed out" not in str(error).lower():
                raise
            return bounded_result(
                status="timeout",
                exit_code=124,
                stdout="",
                stderr="command timed out",
                duration_ms=int((time.monotonic() - started) * 1_000),
                artifact_root=self.artifact_root,
                max_bytes=self.max_output_bytes,
                known_secrets=self.known_secrets,
            )


class HarborRepositoryRuntime(RepositoryRuntime):
    def __init__(
        self,
        environment: BaseEnvironment,
        bridge: _AsyncBridge,
        files: HarborWorkspaceEnvironment,
    ) -> None:
        self._environment = environment
        self._bridge = bridge
        self._files = files
        self.root = files.root
        self._initial = self._full_snapshot()
        self._base_revision = self._exec("git rev-parse HEAD").strip() or None
        self._initial_candidates = self._candidate_paths()

    def _exec(self, command: str) -> str:
        result = self._bridge.call(
            self._environment.exec(command, cwd=self.root.as_posix(), timeout_sec=60)
        )
        if result.return_code != 0:
            return ""
        return result.stdout or ""

    def _full_snapshot(self) -> dict[str, str]:
        output = self._exec(
            "git ls-files --cached --others --exclude-standard -z "
            "| xargs -0 -r sha256sum --"
        )
        snapshot: dict[str, str] = {}
        for line in output.splitlines():
            digest, separator, raw_path = line.partition("  ")
            path = raw_path.removeprefix("./")
            if separator and len(digest) == 64 and path:
                snapshot[path] = digest
        return snapshot

    def _candidate_paths(self, base_revision: str | None = None) -> set[str]:
        revision = base_revision or self._base_revision or "HEAD"
        output = self._exec(
            f"git diff --name-only --no-renames -z {shlex.quote(revision)} --; "
            "git ls-files --others --exclude-standard -z"
        )
        return {path for path in output.split("\0") if path}

    def _snapshot(self, base_revision: str | None = None) -> dict[str, str]:
        candidates = self._initial_candidates | self._candidate_paths(base_revision)
        if not candidates:
            return dict(self._initial)
        snapshot = {
            path: digest for path, digest in self._initial.items() if path not in candidates
        }
        quoted = " ".join(shlex.quote(path) for path in sorted(candidates))
        output = self._exec(
            f"for path in {quoted}; do "
            'if [ -f "$path" ]; then sha256sum -- "$path"; fi; done'
        )
        for line in output.splitlines():
            digest, separator, raw_path = line.partition("  ")
            path = raw_path.removeprefix("./")
            if separator and len(digest) == 64 and path:
                snapshot[path] = digest
        return snapshot

    def manifest(self) -> RepositoryManifest:
        current = self._snapshot()

        def read_text(path: str) -> str:
            return self._files.read_bytes(path).decode("utf-8", errors="replace")

        return repository_manifest_from_snapshot(
            root=self.root,
            files=current,
            read_text=read_text,
            base_revision=self._base_revision,
            branch=self._exec("git branch --show-current").strip() or None,
            dirty=current != self._initial,
        )

    def changed_paths(self, base_revision: str | None) -> list[str]:
        current = self._snapshot(base_revision)
        return sorted(
            path
            for path in self._initial.keys() | current.keys()
            if self._initial.get(path) != current.get(path)
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self._snapshot(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class SkeinPierAgent(BaseAgent):
    """Run Skein on the host while all coding operations stay in Pier."""

    SUPPORTS_WINDOWS = False
    _skein_provider: Literal["openai_codex", "openrouter"]
    _skein_reasoning: ReasoningEffort | None

    def __init__(
        self,
        *args: Any,
        provider: Literal["openai_codex", "openrouter"] = "openai_codex",
        model_name: str | None = "gpt-5.6-luna",
        reasoning: ReasoningEffort | None = None,
        api_key_env: str | None = None,
        auth_state_root: str | Path | None = None,
        config: str | Path = DEFAULT_COMPOSITION_PATH,
        max_iterations: int = 24,
        max_task_input_tokens: int = 2_000_000,
        max_output_tokens: int | None = None,
        wall_time_seconds: float = 1_800,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, model_name=model_name, **kwargs)
        self._skein_provider = provider
        self._skein_reasoning = reasoning
        self.api_key_env = api_key_env
        self.auth_state_root = Path(
            auth_state_root or Path.home() / ".local" / "state" / "skein"
        ).expanduser().resolve()
        self.config = Path(config).expanduser().resolve()
        self.max_iterations = max_iterations
        self.max_task_input_tokens = max_task_input_tokens
        self.max_output_tokens = max_output_tokens
        self.wall_time_seconds = wall_time_seconds
        self._workspace: str | None = None

    @staticmethod
    @override
    def name() -> str:
        return "skein"

    @override
    def version(self) -> str:
        return version("skein")

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        result = await environment.exec("pwd -P", timeout_sec=10)
        if result.return_code != 0 or not (result.stdout or "").strip().startswith("/"):
            raise RuntimeError("Skein could not resolve the Harbor task workspace")
        self._workspace = (result.stdout or "").strip()
        if self._workspace == "/":
            raise RuntimeError("Skein refuses to treat the container root as a task workspace")

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if self._workspace is None:
            await self.setup(environment)
        assert self._workspace is not None
        loop = asyncio.get_running_loop()
        bridge = _AsyncBridge(loop)
        self.logger.info("Initializing Skein Pier runtime for %s", self._workspace)
        files = HarborWorkspaceEnvironment(environment, bridge, self._workspace)
        repository = await asyncio.to_thread(
            HarborRepositoryRuntime,
            environment,
            bridge,
            files,
        )
        state_root = self.logs_dir / "skein-state"
        shadow = self.logs_dir / "workspace"
        shadow.mkdir(parents=True, exist_ok=True)
        task_id = hashlib.sha256(instruction.encode()).hexdigest()[:24]

        def runtime_factory(
            _settings: Any,
            config: SkeinConfig,
            secrets: Sequence[str],
        ) -> ExecutionRuntime:
            commands = HarborCommandSandbox(
                environment,
                bridge,
                self._workspace or "/",
                state_root / "artifacts" / "commands",
                max_output_bytes=config.tools.output.max_bytes,
                known_secrets=secrets,
            )
            return ExecutionRuntime(files=files, commands=commands, repository=repository)

        self.logger.info("Skein Pier repository snapshot initialized")
        registry = default_harness_registry(execution_runtime_factory=runtime_factory)

        def assembly_builder(**kwargs: Any):
            return build_server_assembly(**kwargs, registry=registry)

        request = EvaluationRunRequest(
            workspace=shadow,
            state_root=state_root,
            auth_state_root=self.auth_state_root,
            task_id=task_id,
            prompt=TaskRequest(goal=instruction, mode="coding").model_dump_json(),
            provider=self._skein_provider,
            model=self.model_name or "gpt-5.6-luna",
            reasoning=self._skein_reasoning,
            api_key_env=self.api_key_env,
            config_template=self.config,
            max_iterations=self.max_iterations,
            max_task_input_tokens=self.max_task_input_tokens,
            max_output_tokens=self.max_output_tokens,
            wall_time_seconds=self.wall_time_seconds,
            isolated_environment_authority=True,
        )
        context.metadata = {
            "skein": {
                "status": "running",
                "state_root": state_root.as_posix(),
                "task_id": task_id,
            }
        }
        self.logger.info("Starting Skein evaluation loop for task %s", task_id)
        result = await asyncio.to_thread(
            run_evaluation_sync,
            request,
            assembly_builder=assembly_builder,
            validate_workspace=False,
        )
        self.logger.info("Skein evaluation loop finished with status %s", result.status)
        write_evaluation_result(result)
        self._populate_context(context, result)

    @staticmethod
    def _populate_context(context: AgentContext, result: EvaluationRunResult) -> None:
        metrics = result.metrics
        context.n_input_tokens = int(metrics.get("input_tokens", 0) or 0)
        context.n_cache_tokens = int(metrics.get("cache_read_tokens", 0) or 0)
        context.n_output_tokens = int(metrics.get("output_tokens", 0) or 0)
        context.cost_usd = result.api_equivalent_cost_usd
        if "peak_context_tokens" in type(context).model_fields:
            extended_context: Any = context
            extended_context.peak_context_tokens = int(metrics.get("peak_context_tokens", 0) or 0)
            extended_context.summarization_count = int(metrics.get("outcome_compactions", 0) or 0)
            extended_context.n_agent_steps = int(metrics.get("model_calls", 0) or 0)
        context.metadata = {
            "skein": result.model_dump(mode="json", exclude={"final_answer"}),
            "final_answer": result.final_answer,
        }


SkeinHarborAgent = SkeinPierAgent


__all__ = [
    "HarborCommandSandbox",
    "HarborRepositoryRuntime",
    "HarborWorkspaceEnvironment",
    "SkeinHarborAgent",
    "SkeinPierAgent",
]
