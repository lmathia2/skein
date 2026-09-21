"""Strict ADK loop comparison using the Pi v4.1 contract and provider serializer.

The provider adapter projects exact Pi messages from native ADK events. It does not
run a Pi agent loop. ADK owns tool dispatch and continuation; no managed verifier,
work packet, compaction, terminal schema, or repair prompt is injected here.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from google.adk import Agent, Runner
from google.adk.agents.run_config import RunConfig
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from pydantic import PrivateAttr

from harness.evidence.state import JsonlEventStore


class ParityBridge:
    def __init__(self, process):
        self.process = process
        self.lock = asyncio.Lock()

    async def call(self, method: str, **arguments):
        async with self.lock:
            self.process.stdin.write((json.dumps({"method": method, **arguments}) + "\n").encode())
            await self.process.stdin.drain()
            line = await self.process.stdout.readline()
            if not line:
                raise RuntimeError("Pi provider transport exited; see stderr log")
            response = json.loads(line)
            if "error" in response:
                raise RuntimeError(response["error"])
            return response["value"]


class ParityModel(BaseLlm):
    _bridge: Any = PrivateAttr()
    _messages: list = PrivateAttr(default_factory=list)
    _results: dict = PrivateAttr(default_factory=dict)
    _calls: list = PrivateAttr(default_factory=list)

    def __init__(self, bridge, **kwargs):
        super().__init__(**kwargs)
        self._bridge = bridge

    def user(self, text):
        self._messages.append({"role": "user", "content": [{"type": "text", "text": text}],
                               "timestamp": int(time.time() * 1000)})

    def final_text(self):
        return "\n".join(part["text"] for message in self._messages
                         if message["role"] == "assistant" for part in message.get("content", [])
                         if part["type"] == "text")

    async def generate_content_async(self, llm_request, stream=False):
        # Preserve provider-native reasoning signatures and message boundaries.
        # Never reserialize ADK's lossy thought/text projection back to the provider.
        for call in self._calls:
            self._messages.append(self._results.pop(call["id"]))
        self._calls = []
        response = await self._bridge.call("complete", messages=self._messages)
        print(json.dumps({"type": "message_end", "message": response}), flush=True)
        if response.get("stopReason") in {"error", "aborted"}:
            raise RuntimeError(response.get("errorMessage") or response["stopReason"])
        self._messages.append(response)
        parts = []
        for part in response.get("content", []):
            if part["type"] == "toolCall":
                self._calls.append(part)
                parts.append(types.Part(function_call=types.FunctionCall(
                    id=part["id"], name="code", args={})))
            elif part["type"] == "text":
                parts.append(types.Part.from_text(text=part["text"]))
        yield LlmResponse(content=types.Content(role="model", parts=parts),
                          turn_complete=not self._calls)


class ParityTool(BaseTool):
    def __init__(self, definition, model, ledger):
        super().__init__(name=definition["name"], description=definition["description"])
        self.definition, self.model, self.ledger = definition, model, ledger
        self.lock = asyncio.Lock()

    def _get_declaration(self):
        return types.FunctionDeclaration(name=self.name, description=self.description,
                                         parameters_json_schema=self.definition["parameters"])

    async def run_async(self, *, args, tool_context):
        async with self.lock:
            call_id = tool_context.function_call_id
            original = next(call for call in self.model._calls if call["id"] == call_id)
            result = await self.model._bridge.call("tool", call=original)
            self.ledger.append("parity", "parity.tool_completed", {
                "tool_call_id": call_id, "arguments": original["arguments"], "result": result,
            })
            self.model._results[call_id] = {
                "role": "toolResult", "toolCallId": call_id, "toolName": original["name"],
                "content": result["content"], "details": result.get("details", {}),
                "isError": result["isError"], "timestamp": int(time.time() * 1000),
            }
            return "\n".join(part["text"] for part in result["content"] if part["type"] == "text")


async def run(bridge, task, logs):
    contract = await bridge.call("contract")
    ledger = JsonlEventStore(logs / "skein-events")
    model = ParityModel(bridge, model=contract["model"])
    agent = Agent(name="coding_worker", model=model, instruction="",
                  static_instruction=contract["systemPrompt"],
                  tools=[ParityTool(item, model, ledger) for item in contract["tools"]])
    service = InMemorySessionService()
    await service.create_session(app_name="skein", user_id="user", session_id="parity")
    runner = Runner(app_name="skein", agent=agent, session_service=service)
    prompt = task
    for _ in range(2):  # v4.1 permits one evidence-review follow-up per task.
        model.user(prompt)
        async for event in runner.run_async(user_id="user", session_id="parity",
                run_config=RunConfig(max_llm_calls=0),
                new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)])):
            ledger.append("parity", "parity.adk_event", event.model_dump(mode="json", exclude_none=True))
        prompt = await bridge.call("review", text=model.final_text())
        if prompt is None:
            break
    return model._messages


async def main():
    process = await asyncio.create_subprocess_exec(
        "node", str(Path(__file__).with_name("pi_parity_transport.mjs")),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr, limit=16 * 1024 * 1024,
    )
    try:
        await run(ParityBridge(process), os.environ["SKEIN_PARITY_TASK"],
                  Path(os.environ["SKEIN_PARITY_LOGS"]))
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
