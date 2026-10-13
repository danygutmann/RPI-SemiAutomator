"""Bounded, non-blocking fan-out for TCU events."""

from __future__ import annotations

import asyncio
from collections import deque

from .models import TCUEvent


class EventDispatcher:
    def __init__(self, queue_size: int = 100, history_size: int = 1000) -> None:
        self._queue_size = queue_size
        self._history: deque[TCUEvent] = deque(maxlen=history_size)
        self._clients: set[asyncio.Queue[TCUEvent]] = set()
        self._dropped: dict[asyncio.Queue[TCUEvent], int] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue[TCUEvent]:
        queue: asyncio.Queue[TCUEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._clients.add(queue)
        self._dropped[queue] = 0
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TCUEvent]) -> None:
        self._clients.discard(queue)
        self._dropped.pop(queue, None)

    async def publish(self, event: TCUEvent) -> None:
        """Publish without awaiting client capacity or network I/O."""
        self._retain(event)
        for queue in tuple(self._clients):
            if queue.full():
                try:
                    queue.get_nowait()
                    self._dropped[queue] = self._dropped.get(queue, 0) + 1
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped[queue] = self._dropped.get(queue, 0) + 1

    def _retain(self, event: TCUEvent) -> None:
        self._history.append(event)

    def history(self, limit: int = 100) -> list[TCUEvent]:
        items = list(self._history)
        return items[-limit:]

    def dropped_for(self, queue: asyncio.Queue[TCUEvent]) -> int:
        return self._dropped.get(queue, 0)

    def shutdown(self) -> None:
        self._clients.clear()
        self._dropped.clear()
