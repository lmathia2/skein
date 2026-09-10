"""ADK session-service decorator that emits append-only lifecycle evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from weakref import WeakValueDictionary

from google.adk.sessions.base_session_service import BaseSessionService

SessionSink = Callable[[str, str, dict[str, Any]], object]


class ObservedSessionService(BaseSessionService):
    def __init__(self, delegate: BaseSessionService, sink: SessionSink | None = None) -> None:
        self.delegate = delegate
        self.sink = sink
        # ponytail: SQLite serializes writes too; use per-session locks if another
        # backend makes concurrent session append throughput measurable.
        self._append_lock = asyncio.Lock()
        self._sessions: WeakValueDictionary[tuple[str, str, str, int], Any] = (
            WeakValueDictionary()
        )

    @staticmethod
    def _identity(session: Any) -> tuple[str, str, str, int]:
        return (session.app_name, session.user_id, session.id, id(session.events))

    def _remember(self, session: Any) -> Any:
        identity = self._identity(session)
        peer = self._sessions.get(identity)
        if peer is None or peer.events is not session.events:
            self._sessions[identity] = session
            return session
        return peer

    async def create_session(self, **kwargs: Any) -> Any:
        session = await self.delegate.create_session(**kwargs)
        self._remember(session)
        if self.sink is not None:
            self.sink(
                session.id,
                "session.created",
                {"app_name": session.app_name, "user_id": session.user_id},
            )
        return session

    async def get_session(self, **kwargs: Any) -> Any:
        session = await self.delegate.get_session(**kwargs)
        if session is not None:
            self._remember(session)
        return session

    async def list_sessions(self, **kwargs: Any) -> Any:
        return await self.delegate.list_sessions(**kwargs)

    async def delete_session(self, **kwargs: Any) -> None:
        session_id = str(kwargs["session_id"])
        await self.delegate.delete_session(**kwargs)
        if self.sink is not None:
            self.sink(session_id, "session.deleted", {"app_name": kwargs["app_name"]})

    async def append_event(self, session: Any, event: Any) -> Any:
        async with self._append_lock:
            peer = self._remember(session)
            session.last_update_time = max(
                session.last_update_time, peer.last_update_time
            )
            stored = await self.delegate.append_event(session, event)
            peer.last_update_time = session.last_update_time
        if not stored.partial and self.sink is not None:
            self.sink(
                session.id,
                "session.event",
                {
                    "event": stored.model_dump(mode="json"),
                    "state_keys": sorted(session.state),
                },
            )
        return stored

    async def get_user_state(self, **kwargs: Any) -> dict[str, Any]:
        return await self.delegate.get_user_state(**kwargs)

    async def flush(self) -> None:
        await self.delegate.flush()
