"""Cancellable human waits around the existing durable exact-command approvals."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from .contracts import ApprovalRequest
from .store import ApprovalStore


@dataclass
class _Pending:
    request: ApprovalRequest
    changed: asyncio.Event
    deadline: str


class ApprovalWaiter:
    """One run's control-plane rendezvous; never executes a command itself."""

    def __init__(self, store: ApprovalStore, task_id: str, *, timeout: float = 120) -> None:
        if timeout <= 0:
            raise ValueError("approval timeout must be positive")
        self.store, self.task_id, self.timeout = store, task_id, timeout
        self._pending: dict[str, _Pending] = {}

    def pending(self) -> list[dict[str, object]]:
        return [{**item.request.model_dump(mode="json"), "wait_deadline": item.deadline}
                for item in self._pending.values()]

    async def decide(self, request_id: str, fingerprint: str, decision: Literal["approved", "denied"], *, actor: str) -> ApprovalRequest:
        request = await asyncio.to_thread(self.store.get, request_id)
        if request is None or request.task_id != self.task_id or request.fingerprint != fingerprint:
            raise ValueError("approval does not match this task and exact command")
        if request.status == "pending" and request_id not in self._pending:
            raise ValueError("command is no longer waiting for approval")
        result = await asyncio.to_thread(self.store.decide, request_id, decision=decision, actor=actor)
        if pending := self._pending.get(request_id):
            pending.changed.set()
        return result

    async def wait(self, request_id: str, task_id: str) -> ApprovalRequest:
        if task_id != self.task_id:
            raise ValueError("approval wait belongs to another task")
        request = await asyncio.to_thread(self.store.get, request_id)
        if request is None or request.task_id != task_id:
            raise ValueError("approval request is not in this task")
        if request.status != "pending":
            return request
        if request_id in self._pending or len(self._pending) >= 32:
            raise ValueError("approval wait already active or capacity exceeded")
        pending = _Pending(request, asyncio.Event(), (datetime.now(UTC) + timedelta(seconds=self.timeout)).isoformat())
        self._pending[request_id] = pending
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    # Polling also observes decisions submitted through the existing
                    # approval CLI; socket decisions wake the waiter immediately.
                    pending.changed.clear()
                    current = await asyncio.to_thread(self.store.get, request_id)
                    if current is None:
                        raise ValueError("approval request disappeared")
                    if current.status != "pending":
                        return current
                    with suppress(TimeoutError):
                        await asyncio.wait_for(pending.changed.wait(), timeout=1)
        except TimeoutError:
            await asyncio.to_thread(self.store.expire, request_id, task_id=task_id)
            current = await asyncio.to_thread(self.store.get, request_id)
            assert current is not None
            # A racing decision may be durable, but must not launch after timeout.
            return current.model_copy(update={"status": "expired"})
        finally:
            self._pending.pop(request_id, None)
            await asyncio.shield(asyncio.to_thread(self.store.expire, request_id, task_id=task_id))
