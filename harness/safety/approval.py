"""Static command risk classification and approval policy.

The coding model never gets to redefine these categories. Risky operations are either
blocked or require an approval supplied by the outer control plane.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class CommandRisk(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_MUTATION = "workspace_mutation"
    BUILD_OR_TEST = "build_or_test"
    DEPENDENCY_INSTALL = "dependency_install"
    NETWORK_ACCESS = "network_access"
    GIT_HISTORY_MUTATION = "git_history_mutation"
    PUBLISH_OR_DEPLOY = "publish_or_deploy"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


class ApprovalAction(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    action: ApprovalAction
    risk: CommandRisk
    reason: str


_SAFE_READ_COMMANDS = {
    "cat",
    "cut",
    "diff",
    "du",
    "echo",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "pwd",
    "printf",
    "rg",
    "sed",
    "sort",
    "stat",
    "tail",
    "wc",
    "which",
    "test",
    "true",
    "false",
}
_BUILD_COMMANDS = {
    "cargo",
    "dotnet",
    "go",
    "gradle",
    "gradlew",
    "java",
    "javac",
    "make",
    "mvn",
    "npm",
    "npx",
    "pnpm",
    "pyright",
    "pytest",
    "python",
    "python3",
    "ruff",
    "tox",
    "uv",
    "yarn",
}
_MUTATION_COMMANDS = {
    "chmod",
    "cp",
    "install",
    "ln",
    "mkdir",
    "mv",
    "patch",
    "rm",
    "rmdir",
    "touch",
    "truncate",
}
_NETWORK_COMMANDS = {
    "curl",
    "ftp",
    "gh",
    "git",
    "http",
    "https",
    "nc",
    "netcat",
    "scp",
    "sftp",
    "ssh",
    "telnet",
    "wget",
}
_PUBLISH_WORDS = {
    "deploy",
    "publish",
    "release",
    "upload",
}
_DESTRUCTIVE_PATTERNS = (
    re.compile(r"(?:^|\s)rm\s+(?:-[A-Za-z]*[rf][A-Za-z]*\s+)+/(?:\s|$)"),
    re.compile(r"(?:^|\s)(?:mkfs|fdisk|parted|shutdown|reboot|halt)(?:\s|$)"),
    re.compile(r"(?:^|\s)dd\s+.*\bof=/dev/"),
    re.compile(r"(?:^|\s)git\s+reset\s+--hard(?:\s|$)"),
    re.compile(r"(?:^|\s)git\s+clean\s+-[A-Za-z]*f"),
    re.compile(r"(?:^|\s)git\s+push\s+.*(?:--force|-f)(?:\s|$)"),
)
_SHELL_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n|(?<![>&])&(?![>&]))\s*")
_HOST_ROOT_READ = re.compile(r"(?:^|\s)(?:du|find|ls)\s+/(?:\s|$)")
_SHELL_SUBSTITUTION = re.compile(r"\$\(|`|[<>]\(")


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return []


def _git_risk(tokens: list[str]) -> CommandRisk:
    index = 1
    options_with_values = {
        "-C",
        "-c",
        "--config-env",
        "--git-dir",
        "--namespace",
        "--work-tree",
    }
    while index < len(tokens) and tokens[index].startswith("-"):
        option = tokens[index].split("=", 1)[0]
        index += 2 if option in options_with_values and "=" not in tokens[index] else 1
    if index >= len(tokens):
        return CommandRisk.READ_ONLY
    subcommand = tokens[index]
    if subcommand in {
        "add",
        "am",
        "branch",
        "checkout",
        "cherry-pick",
        "commit",
        "merge",
        "mv",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "switch",
        "tag",
        "worktree",
    }:
        return CommandRisk.GIT_HISTORY_MUTATION
    if subcommand in {"push", "send-email"}:
        return CommandRisk.PUBLISH_OR_DEPLOY
    if subcommand in {"clone", "fetch", "pull", "remote", "submodule"}:
        return CommandRisk.NETWORK_ACCESS
    return CommandRisk.READ_ONLY


def _package_risk(tokens: list[str]) -> CommandRisk:
    if not tokens:
        return CommandRisk.UNKNOWN
    command = tokens[0].rsplit("/", 1)[-1]
    args = set(tokens[1:])
    if command == "npx":
        return (
            CommandRisk.BUILD_OR_TEST
            if "--no-install" in args
            else CommandRisk.DEPENDENCY_INSTALL
        )
    if command == "uv":
        if args & {"add", "remove", "sync", "pip"}:
            return CommandRisk.DEPENDENCY_INSTALL
        return CommandRisk.BUILD_OR_TEST
    if command in {"npm", "pnpm", "yarn"}:
        if args & {"add", "install", "remove", "uninstall", "update", "upgrade"}:
            return CommandRisk.DEPENDENCY_INSTALL
        if args & _PUBLISH_WORDS:
            return CommandRisk.PUBLISH_OR_DEPLOY
        return CommandRisk.BUILD_OR_TEST
    if command == "cargo":
        if args & {"add", "install", "remove", "update"}:
            return CommandRisk.DEPENDENCY_INSTALL
        if args & {"publish", "login", "owner"}:
            return CommandRisk.PUBLISH_OR_DEPLOY
        return CommandRisk.BUILD_OR_TEST
    if command == "go":
        if args & {"get", "install"}:
            return CommandRisk.DEPENDENCY_INSTALL
        return CommandRisk.BUILD_OR_TEST
    if command in {"pip", "pip3"}:
        return CommandRisk.DEPENDENCY_INSTALL
    return CommandRisk.BUILD_OR_TEST


def _notebook_risk(tokens: list[str]) -> CommandRisk:
    """Only local, non-externalizing nb-cli inspection is automatic."""

    if len(tokens) < 2 or {"--server", "--token"} & set(tokens[2:]):
        return CommandRisk.UNKNOWN
    subcommand = tokens[1]
    if subcommand == "status":
        return CommandRisk.READ_ONLY
    if subcommand == "search":
        return CommandRisk.READ_ONLY
    if subcommand == "read" and "--no-output" in tokens[2:]:
        return CommandRisk.READ_ONLY
    return CommandRisk.UNKNOWN


def _cd_risk(tokens: list[str], workspace: Path | None) -> CommandRisk:
    """Allow directory changes only when their literal target stays in the workspace."""

    if workspace is None or len(tokens) != 2 or tokens[1].startswith("-"):
        return CommandRisk.UNKNOWN
    target = Path(tokens[1])
    if not target.is_absolute():
        target = workspace / target
    try:
        target.resolve().relative_to(workspace.resolve())
    except (OSError, ValueError):
        return CommandRisk.UNKNOWN
    return CommandRisk.READ_ONLY


def classify_command(command: str, *, workspace: Path | None = None) -> CommandRisk:
    """Return the highest-risk segment in a shell command."""

    normalized = command.strip()
    if not normalized:
        return CommandRisk.UNKNOWN
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            return CommandRisk.DESTRUCTIVE
    if _HOST_ROOT_READ.search(normalized):
        return CommandRisk.UNKNOWN
    if _SHELL_SUBSTITUTION.search(normalized):
        return CommandRisk.UNKNOWN

    risks: list[CommandRisk] = []
    for segment in _SHELL_SPLIT.split(normalized):
        if not segment:
            continue
        # Pipelines can hide exfiltration; classify each pipe segment separately.
        pipe_segments = [part.strip() for part in segment.split("|") if part.strip()]
        for pipe_segment in pipe_segments:
            tokens = _tokens(pipe_segment)
            if not tokens:
                risks.append(CommandRisk.UNKNOWN)
                continue
            executable = tokens[0].rsplit("/", 1)[-1]
            if executable == "sudo":
                risks.append(CommandRisk.DESTRUCTIVE)
            elif executable == "cd":
                risks.append(_cd_risk(tokens, workspace))
            elif executable == "git":
                risks.append(_git_risk(tokens))
            elif executable == "nb":
                risks.append(_notebook_risk(tokens))
            elif executable in {"pip", "pip3", "uv", "npm", "npx", "pnpm", "yarn", "cargo", "go"}:
                risks.append(_package_risk(tokens))
            elif executable in _NETWORK_COMMANDS:
                risks.append(CommandRisk.NETWORK_ACCESS)
            elif executable in _MUTATION_COMMANDS:
                if executable == "rm" and any("r" in token and token.startswith("-") for token in tokens[1:]):
                    risks.append(CommandRisk.DESTRUCTIVE)
                else:
                    risks.append(CommandRisk.WORKSPACE_MUTATION)
            elif executable in _BUILD_COMMANDS:
                risks.append(
                    CommandRisk.DEPENDENCY_INSTALL
                    if executable == "npx" and "--no-install" not in tokens[1:]
                    else CommandRisk.BUILD_OR_TEST
                )
            elif executable in _SAFE_READ_COMMANDS:
                if (executable == "find" and {
                    "-delete",
                    "-exec",
                    "-execdir",
                    "-ok",
                    "-okdir",
                } & set(tokens[1:])) or any(
                    ".." in Path(token).parts for token in tokens[1:]
                ):
                    risks.append(CommandRisk.UNKNOWN)
                else:
                    risks.append(CommandRisk.READ_ONLY)
            elif executable in {"gcloud", "kubectl", "terraform", "helm", "docker"}:
                if any(word in _PUBLISH_WORDS for word in tokens[1:]):
                    risks.append(CommandRisk.PUBLISH_OR_DEPLOY)
                else:
                    risks.append(CommandRisk.UNKNOWN)
            else:
                risks.append(CommandRisk.UNKNOWN)

    priority = {
        CommandRisk.READ_ONLY: 0,
        CommandRisk.BUILD_OR_TEST: 1,
        CommandRisk.WORKSPACE_MUTATION: 2,
        CommandRisk.DEPENDENCY_INSTALL: 3,
        CommandRisk.NETWORK_ACCESS: 4,
        CommandRisk.GIT_HISTORY_MUTATION: 5,
        CommandRisk.UNKNOWN: 6,
        CommandRisk.PUBLISH_OR_DEPLOY: 7,
        CommandRisk.DESTRUCTIVE: 8,
    }
    return max(risks, key=priority.__getitem__, default=CommandRisk.UNKNOWN)


@dataclass(slots=True)
class ApprovalPolicy:
    """Translate risk into allow/ask/deny with explicit opt-ins."""

    allow_dependency_install: bool = False
    allow_network: bool = False
    allow_git_history_mutation: bool = False
    allow_unknown: bool = False
    approved_fingerprints: set[str] = field(default_factory=set)

    def decide(
        self,
        command: str,
        *,
        fingerprint: str | None = None,
        workspace: Path | None = None,
    ) -> ApprovalDecision:
        risk = classify_command(command, workspace=workspace)
        if fingerprint and fingerprint in self.approved_fingerprints:
            return ApprovalDecision(
                ApprovalAction.ALLOW,
                risk,
                "operation approved by the outer control plane",
            )
        if risk in {CommandRisk.READ_ONLY, CommandRisk.BUILD_OR_TEST, CommandRisk.WORKSPACE_MUTATION}:
            return ApprovalDecision(ApprovalAction.ALLOW, risk, "safe inside the confined workspace")
        if risk == CommandRisk.DESTRUCTIVE:
            return ApprovalDecision(ApprovalAction.DENY, risk, "destructive operations are never automatic")
        if risk == CommandRisk.PUBLISH_OR_DEPLOY:
            return ApprovalDecision(
                ApprovalAction.REQUIRE_APPROVAL,
                risk,
                "publishing and deployment require explicit human approval",
            )
        if risk == CommandRisk.DEPENDENCY_INSTALL and self.allow_dependency_install:
            return ApprovalDecision(ApprovalAction.ALLOW, risk, "dependency installation enabled by policy")
        if risk == CommandRisk.NETWORK_ACCESS and self.allow_network:
            return ApprovalDecision(ApprovalAction.ALLOW, risk, "network access enabled by policy")
        if risk == CommandRisk.GIT_HISTORY_MUTATION and self.allow_git_history_mutation:
            return ApprovalDecision(ApprovalAction.ALLOW, risk, "Git mutation enabled by policy")
        if risk == CommandRisk.UNKNOWN and self.allow_unknown:
            return ApprovalDecision(ApprovalAction.ALLOW, risk, "unknown commands enabled by policy")
        return ApprovalDecision(
            ApprovalAction.REQUIRE_APPROVAL,
            risk,
            f"{risk.value} operation requires explicit approval",
        )
