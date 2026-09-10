"""Cancellation-safe calls into bounded synchronous effect adapters."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


async def run_managed_thread(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Drain a bounded effect before its owner observes cancellation."""
    pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not pending.cancelled():
            pending.exception()
        raise asyncio.CancelledError
    return pending.result()
