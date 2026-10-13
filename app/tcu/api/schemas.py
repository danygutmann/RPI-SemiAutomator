"""OpenAPI schemas for the TCU API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..events.models import EventType


class MethodCallRequest(BaseModel):
    service: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^(?:[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+|:[0-9]+\.[0-9]+)$",
    )
    object_path: str = Field(
        min_length=1, max_length=1024, pattern=r"^/(?:[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*)?$"
    )
    interface: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$",
    )
    method: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    arguments: list[Any] = Field(default_factory=list, max_length=64)
    timeout_seconds: float | None = Field(default=None, gt=0, le=60)


class AcceptedCall(BaseModel):
    request_id: str
    status: Literal["accepted"]
    submitted_at: str


class CallResult(BaseModel):
    request_id: str
    status: Literal["accepted", "running", "completed", "failed"]
    service: str
    object_path: str
    interface: str
    method: str
    submitted_at: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None
    result: Any = None
    result_signature: str | None = None
    error: dict[str, str] | None = None


class APIError(BaseModel):
    error: dict[str, str]


class TCUStatus(BaseModel):
    enabled: bool
    connected: bool
    transport: str
    last_changed_at: str
    error: str | None = None


class TCUObject(BaseModel):
    object_path: str
    discovery: Literal["configured"]
    service: str | None = None


class IntrospectionResult(BaseModel):
    service: str
    object_path: str
    xml: str


class TCUEventMessage(BaseModel):
    schema_version: Literal[1] = 1
    event_id: str
    event_type: EventType
    timestamp: str
    request_id: str | None = None
    payload: dict[str, Any]
