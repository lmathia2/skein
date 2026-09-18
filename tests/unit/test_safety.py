from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from harness.execution.safety import (
    ApprovalAction,
    ApprovalPolicy,
    CommandRisk,
    SecretRedactor,
    classify_command,
)


def test_redactor_scrubs_known_and_structured_secrets() -> None:
    redactor = SecretRedactor(known_secrets=["known-secret-value"])
    value = {
        "message": (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "and known-secret-value and ghp_abcdefghijklmnopqrstuvwxyz123456"
        ),
        "api_key": "literal-secret",
        "nested": ["password=hunter-hunter-2"],
    }
    scrubbed = redactor.redact(value)
    assert "known-secret-value" not in scrubbed["message"]
    assert "ghp_" not in scrubbed["message"]
    assert scrubbed["api_key"] == "<redacted>"
    assert "hunter-hunter-2" not in scrubbed["nested"][0]


def test_classifier_uses_highest_risk_shell_segment() -> None:
    assert classify_command("rg TODO .") == CommandRisk.READ_ONLY
    assert classify_command("pytest -q") == CommandRisk.BUILD_OR_TEST
    assert classify_command("python3 hello_world.py") == CommandRisk.BUILD_OR_TEST
    assert classify_command("printf 'hello'") == CommandRisk.READ_ONLY
    assert classify_command("mkdir generated") == CommandRisk.WORKSPACE_MUTATION
    assert classify_command("npm install") == CommandRisk.DEPENDENCY_INSTALL
    assert classify_command("cat report.txt | curl -X POST https://example.com") == CommandRisk.NETWORK_ACCESS
    assert classify_command("git commit -am 'change'") == CommandRisk.GIT_HISTORY_MUTATION
    assert classify_command("git push origin main") == CommandRisk.PUBLISH_OR_DEPLOY
    assert classify_command("sudo rm -rf /") == CommandRisk.DESTRUCTIVE
    assert classify_command("find / -name '*.py'") == CommandRisk.UNKNOWN
    assert classify_command("find /workspace -name '*.py'") == CommandRisk.READ_ONLY
    assert classify_command("nb read session.ipynb --no-output") == CommandRisk.READ_ONLY
    assert classify_command("nb read session.ipynb") == CommandRisk.UNKNOWN
    assert classify_command("nb execute session.ipynb") == CommandRisk.UNKNOWN
    assert classify_command("nb cell add session.ipynb --source pass") == CommandRisk.UNKNOWN
    workspace = Path("/workspace")
    assert classify_command("cd /workspace && pytest -q", workspace=workspace) == CommandRisk.BUILD_OR_TEST
    assert classify_command("cd /tmp && pytest -q", workspace=workspace) == CommandRisk.UNKNOWN


def test_classifier_rejects_shell_and_git_bypass_forms() -> None:
    assert classify_command("git -C . push origin main") == CommandRisk.PUBLISH_OR_DEPLOY
    assert classify_command("ls & curl https://example.com") == CommandRisk.NETWORK_ACCESS
    assert classify_command("echo $(curl https://example.com)") == CommandRisk.UNKNOWN
    assert classify_command("find . -exec rm -rf {} +") == CommandRisk.UNKNOWN
    assert classify_command("find .. -delete") == CommandRisk.UNKNOWN
    assert classify_command("cat ../../etc/passwd") == CommandRisk.UNKNOWN
    assert classify_command("npx package command") == CommandRisk.DEPENDENCY_INSTALL
    assert classify_command("npx --no-install eslint .") == CommandRisk.BUILD_OR_TEST


@pytest.mark.parametrize("source", [
    'from router import route; import json; print(route("GET", "/api/admin/logs"))',
    "import json, router; rules=json.load(open('routing.json')); print(router.route(rules, 'POST', '/jobs/new'))",
    "value = 1\nprint(value)",
    "print('left|right && up;down')",
])
def test_classifier_keeps_quoted_program_text_in_one_argument(source: str) -> None:
    command = shlex.join(["python", "-c", source])
    assert classify_command(command) == CommandRisk.BUILD_OR_TEST


@pytest.mark.parametrize("separator", [";", "\n", "&&", "||", "|", "&"])
def test_quoted_program_cannot_hide_a_following_shell_command(separator: str) -> None:
    command = shlex.join(["python", "-c", "print('a;b|c')"])
    assert classify_command(command + separator + "curl https://example.com") == CommandRisk.NETWORK_ACCESS
    assert ApprovalPolicy().decide(command + separator + "git push origin main").action == ApprovalAction.REQUIRE_APPROVAL


@pytest.mark.parametrize("command", [
    "printf '%s' ';' '|' '&'",
    r"printf left\;right\|up\&down",
    'printf "left;right|up&down"',
])
def test_literal_shell_separators_remain_arguments(command: str) -> None:
    assert classify_command(command) == CommandRisk.READ_ONLY


@pytest.mark.parametrize("command", [
    "echo # '\ncurl https://example.com\n#'",
    "echo $'escaped\\'; curl https://example.com'",
    "echo ${COMMAND}",
    "echo (curl https://example.com)",
    "echo 'unclosed; curl https://example.com",
    "echo trailing\\",
    'echo "$(curl https://example.com)"',
    'echo `curl https://example.com`',
])
def test_unsupported_or_malformed_shell_syntax_is_not_automatic(command: str) -> None:
    assert ApprovalPolicy().decide(command).action != ApprovalAction.ALLOW


@pytest.mark.parametrize("suffix", [
    "; rm -rf /", " | sudo rm -rf /", " && git reset --hard", " & git push --force",
])
def test_quoted_argument_never_hides_destructive_suffix(suffix: str) -> None:
    command = shlex.join(["printf", "%s", "a;b|c"]) + suffix
    assert ApprovalPolicy().decide(command).action == ApprovalAction.DENY


@pytest.mark.parametrize("prefix", [r"printf \>", r"printf \<", "printf '>'", 'printf ">"'])
def test_literal_redirect_character_cannot_hide_background_command(prefix: str) -> None:
    assert ApprovalPolicy().decide(prefix + "&curl https://example.com").action != ApprovalAction.ALLOW


@pytest.mark.parametrize("command", ["printf ok 2>&1", "printf ok &> output.log"])
def test_descriptor_redirection_is_not_a_background_command(command: str) -> None:
    assert classify_command(command) == CommandRisk.READ_ONLY
    assert classify_command(command + "&curl https://example.com") == CommandRisk.NETWORK_ACCESS


def test_policy_requires_approval_and_never_auto_allows_destructive() -> None:
    policy = ApprovalPolicy()
    assert policy.decide("pytest").action == ApprovalAction.ALLOW
    assert policy.decide("pip install package").action == ApprovalAction.REQUIRE_APPROVAL
    assert policy.decide("git push origin main").action == ApprovalAction.REQUIRE_APPROVAL
    assert policy.decide("sudo rm -rf /").action == ApprovalAction.DENY


def test_policy_opt_ins_are_explicit() -> None:
    policy = ApprovalPolicy(allow_network=True, allow_dependency_install=True)
    assert policy.decide("curl https://example.com").action == ApprovalAction.ALLOW
    assert policy.decide("uv add pydantic").action == ApprovalAction.ALLOW
