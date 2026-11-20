"""Run a turn in its own task so the reader's speed never decides how long the turn holds its lock."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any


async def pump[T](source: AsyncIterator[T]) -> AsyncIterator[T]:
    """Yield what `source` yields. The source runs ahead in a task and buffers in a queue.

    If the reader stalls, the source still finishes (and releases whatever it holds). If the reader
    leaves, the source is cancelled so no more model calls are spent on an answer nobody will read.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for item in source:
                queue.put_nowait(("item", item))
            queue.put_nowait(("end", None))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            queue.put_nowait(("error", exc))
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    task = asyncio.create_task(produce())
    try:
        while True:
            kind, value = await queue.get()
            if kind == "item":
                yield value
            elif kind == "end":
                return
            else:
                raise value
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
