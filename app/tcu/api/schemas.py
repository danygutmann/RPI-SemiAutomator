"""OpenAPI schemas for the TCU API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MethodCallRequest(BaseModel):
    service: str = Field(min_length=1, max_length=255)
    object_path: str = Field(min_length=1, max_length=1024, pattern=r"^/")
    interface: str = Field(min_length=1, max_length=255)
    method: str = Field(min_length=1, max_length=255)
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


class TCUEventMessage(BaseModel):
    schema_version: int = 1
    event_id: str
    event_type: str
    timestamp: str
    request_id: str | None = None
    payload: dict[str, Any]
