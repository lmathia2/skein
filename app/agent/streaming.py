"""ADK callback seam for gated conversational Markdown, not worker control text."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import ToolContext

from harness.core.models.agent_step import AgentStep
from harness.core.orchestration.reply import MAX_HEADER_CHARS, MAX_REPLY_CHARS, parse_reply
from harness.execution.safety import SecretRedactor

# Multi-word/multi-line credential forms cannot be safely redacted one token at
# a time. Conservatively hold from a sensitive label until the final response.
_SENSITIVE = re.compile(
    r"(?i)-----BEGIN|\b(?:authorization|api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|password|passwd|secret)\b"
)


class PublicReplyStream:
    """One invocation's worker stream; the workflow supplies the authorization gate.

    Partial events carry a separate public projection. Their raw content remains
    untouched for ADK output, tracing and resumability. No extra model call or
    synthetic typing is used. Once published, tools cannot run in this work batch.
    """

    def __init__(self, redactor: SecretRedactor) -> None:
        self.redactor = redactor
        self.authorize: Callable[[AgentStep], Awaitable[bool]] | None = None
        self.started = False
        self._raw = ""
        self._public = ""
        self._allowed: bool | None = None
        self._disabled = False

    def prepare(self, authorize: Callable[[AgentStep], Awaitable[bool]]) -> None:
        self.authorize = authorize
        self.started = False
        self._reset_response()

    def _reset_response(self) -> None:
        self._raw = self._public = ""
        self._allowed = None
        self._disabled = False

    def guard_tool(self) -> None:
        if self.started:
            raise ValueError("A tool call cannot follow a streamed terminal reply")

    async def before_model(
        self, callback_context: CallbackContext, llm_request: LlmRequest,
    ) -> None:
        del callback_context, llm_request
        self.guard_tool()
        self._reset_response()

    def _safe_prefix(self, message: str, *, final: bool) -> str:
        if final:
            return self.redactor.redact_text(message)
        # Finish a word before publishing it: token formats may cross model chunks.
        end = max((match.end() for match in re.finditer(r"\s+", message)), default=0)
        if match := _SENSITIVE.search(message):
            end = min(end, match.start())
        # Known secrets may themselves contain whitespace. Keep any incomplete
        # suffix so a later chunk cannot retroactively turn public text into a key.
        for secret in self.redactor.known_secrets:
            if len(secret) < 4:
                continue
            for size in range(min(len(secret) - 1, len(message)), 0, -1):
                if message.endswith(secret[:size]):
                    end = min(end, len(message) - size)
                    break
            start = message.find(secret)
            while start >= 0:
                if start < end < start + len(secret):
                    end = start
                start = message.find(secret, start + 1)
        return self.redactor.redact_text(message[:end])

    async def after_model(
        self, callback_context: CallbackContext, llm_response: LlmResponse,
    ) -> None:
        del callback_context
        parts = llm_response.content.parts if llm_response.content else ()
        if any(part.function_call is not None for part in parts or ()):
            self.guard_tool()
            self._disabled = True
            return
        text = "".join(part.text or "" for part in parts or () if not part.thought)
        if self._disabled or self.authorize is None or not text:
            return
        if llm_response.partial:
            raw = self._raw + text
        else:
            raw = text
            if self.started and not raw.startswith(self._raw):
                raise ValueError("Final reply disagrees with its streamed prefix")
        if len(raw) > MAX_HEADER_CHARS + MAX_REPLY_CHARS + 1:
            self.guard_tool()
            self._disabled = True
            return
        self._raw = raw
        try:
            step = parse_reply(raw)
        except ValueError:
            self.guard_tool()
            self._disabled = True
            return
        if step is None:
            return
        if self._allowed is None and step.message.strip():
            self._allowed = await self.authorize(step)
        if not self._allowed:
            return
        public = self._safe_prefix(step.message, final=not llm_response.partial)
        if not public.startswith(self._public):
            raise ValueError("Public reply changed an already published prefix")
        delta = public[len(self._public):]
        if delta:
            self.started = True
            self._public = public
            llm_response.custom_metadata = {
                **(llm_response.custom_metadata or {}), "coding.public_delta": delta,
            }

    def finish(self, step: AgentStep) -> AgentStep:
        """A streamed reply must finish as the same immutable control/body pair."""
        if not self.started:
            return step
        if parse_reply(self._raw) != step:
            raise ValueError("Final worker result disagrees with its streamed reply")
        return step.model_copy(update={"message": self.redactor.redact_text(step.message)})


class PublicReplies:
    """Keep reused ADK Apps safe across concurrent sessions; discard on run exit."""

    def __init__(self, redactor: SecretRedactor) -> None:
        self.redactor = redactor
        self._streams: dict[str, PublicReplyStream] = {}

    def for_invocation(self, invocation_id: str) -> PublicReplyStream:
        if invocation_id not in self._streams:
            self._streams[invocation_id] = PublicReplyStream(self.redactor)
        return self._streams[invocation_id]

    def release(self, invocation_id: str) -> None:
        self._streams.pop(invocation_id, None)

    def guard_tool(self, tool_context: ToolContext | None) -> None:
        if tool_context is not None and (stream := self._streams.get(tool_context.invocation_id)):
            stream.guard_tool()

    async def before_model(self, callback_context: CallbackContext, llm_request: LlmRequest) -> None:
        if stream := self._streams.get(callback_context.invocation_id):
            await stream.before_model(callback_context, llm_request)

    async def after_model(self, callback_context: CallbackContext, llm_response: LlmResponse) -> None:
        if stream := self._streams.get(callback_context.invocation_id):
            await stream.after_model(callback_context, llm_response)
