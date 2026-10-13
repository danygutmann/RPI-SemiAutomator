"""Versioned event envelope shared by REST, WebSocket, and GUI clients."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

from ..core.models import utc_now


EventType = Literal[
    "connection.status",
    "method.started",
    "method.completed",
    "method.failed",
    "dbus.signal",
    "tcu.error",
]


@dataclass(frozen=True)
class TCUEvent:
    event_type: EventType
    payload: dict[str, Any]
    event_id: str
    timestamp: str
    request_id: str | None = None
    schema_version: Literal[1] = 1

    @classmethod
    def create(
        cls,
        event_type: EventType,
        payload: dict[str, Any],
        request_id: str | None = None,
    ) -> "TCUEvent":
        return cls(
            event_type=event_type,
            payload=payload,
            event_id=str(uuid4()),
            timestamp=utc_now(),
            request_id=request_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
