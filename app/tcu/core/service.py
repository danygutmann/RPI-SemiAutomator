"""Application-facing TCU orchestration, independent of GUI and HTTP."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

from dbus_next.validators import (
    is_bus_name_valid,
    is_interface_name_valid,
    is_member_name_valid,
    is_object_path_valid,
)

from ..dbus.client import DBusClient
from ..dbus.exceptions import TCUError, TCUQueueFull, TCUTimeout
from ..events.dispatcher import EventDispatcher
from ..events.models import TCUEvent
from .config import TCUConfig
from .models import CallRecord, utc_now

MAX_CALL_HISTORY = 1000
MAX_ACTIVE_CALLS = 16
MAX_PENDING_CALLS = 64


class TCUService:
    def __init__(
        self,
        config: TCUConfig,
        client: DBusClient,
        events: EventDispatcher | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.events = events or EventDispatcher()
        self._calls: OrderedDict[str, CallRecord] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()
        self._active_calls = asyncio.Semaphore(MAX_ACTIVE_CALLS)
        self._lifecycle_lock = asyncio.Lock()
        self._unsubscribe_signals = None
        self._unsubscribe_status = self.client.subscribe_status(self._on_status)

    def status(self) -> dict[str, Any]:
        return self.client.status()

    async def connect(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            try:
                status = await self.client.connect()
                await self.events.publish(
                    TCUEvent.create("connection.status", {"status": status})
                )
                await self._ensure_signal_subscription()
                return status
            except TCUError as exc:
                if self.status().get("connected"):
                    await self.client.disconnect()
                await self.events.publish(
                    TCUEvent.create("connection.status", {"status": self.status()})
                )
                await self.events.publish(
                    TCUEvent.create(
                        "tcu.error", {"code": exc.code, "message": str(exc)}
                    )
                )
                raise

    async def disconnect(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            status = await self.client.disconnect()
            if self._unsubscribe_signals is not None:
                self._unsubscribe_signals()
                self._unsubscribe_signals = None
            await self.events.publish(
                TCUEvent.create("connection.status", {"status": status})
            )
            return status

    async def services(self) -> list[str]:
        return await self.client.list_services()

    async def objects(self, service: str | None = None) -> list[dict[str, str]]:
        return await self.client.list_objects(service)

    async def introspect(self, service: str, object_path: str) -> str:
        return await self.client.introspect(service, object_path)

    def submit_call(
        self,
        service: str,
        object_path: str,
        interface: str,
        method: str,
        arguments: list[Any],
        timeout_seconds: float | None = None,
    ) -> CallRecord:
        pending = sum(
            call.status in {"accepted", "running"} for call in self._calls.values()
        )
        if pending >= MAX_PENDING_CALLS:
            raise TCUQueueFull("D-Bus call queue is full; retry after calls complete")
        timeout = (
            self.config.timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not 0 < timeout <= 60:
            raise ValueError("timeout_seconds must be greater than 0 and at most 60")
        if not is_bus_name_valid(service):
            raise ValueError("service is not a valid D-Bus bus name")
        if not is_object_path_valid(object_path):
            raise ValueError("object_path is not a valid D-Bus object path")
        if not is_interface_name_valid(interface):
            raise ValueError("interface is not a valid D-Bus interface")
        if not is_member_name_valid(method):
            raise ValueError("method is not a valid D-Bus member name")
        if len(arguments) > 64:
            raise ValueError("arguments may contain at most 64 values")
        try:
            argument_bytes = json.dumps(
                arguments, allow_nan=False, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("arguments must be valid JSON values") from exc
        if len(argument_bytes) > 256 * 1024:
            raise ValueError("arguments exceed the 256 KiB size limit")
        record = CallRecord.create(
            service,
            object_path,
            interface,
            method,
            arguments,
            timeout,
        )
        self._calls[record.request_id] = record
        while len(self._calls) > MAX_CALL_HISTORY:
            self._calls.popitem(last=False)
        task = asyncio.create_task(self._execute_call(record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record

    def get_call(self, request_id: str) -> CallRecord | None:
        return self._calls.get(request_id)

    async def _execute_call(self, record: CallRecord) -> None:
        started = time.monotonic()
        record.started_at = utc_now()
        record.status = "running"
        await self.events.publish(
            TCUEvent.create(
                "method.started",
                {
                    "service": record.service,
                    "object_path": record.object_path,
                    "interface": record.interface,
                    "method": record.method,
                },
                request_id=record.request_id,
            )
        )
        try:
            async with self._active_calls:
                result, signature = await asyncio.wait_for(
                    self.client.call_method(
                        record.service,
                        record.object_path,
                        record.interface,
                        record.method,
                        record._arguments,
                        record.timeout_seconds,
                    ),
                    timeout=record.timeout_seconds,
                )
            record.status = "completed"
            record.result = result
            record.result_signature = signature
            event_type = "method.completed"
            payload = {
                "status": record.status,
                "result": result,
                "result_signature": signature,
            }
        except (asyncio.TimeoutError, TCUTimeout) as exc:
            record.status = "failed"
            record.error = {
                "code": "timeout",
                "message": str(exc)
                or "D-Bus call timed out; the TCU may still have executed it",
            }
            event_type = "method.failed"
            payload = {"status": record.status, "error": record.error}
        except TCUError as exc:
            record.status = "failed"
            record.error = {"code": exc.code, "message": str(exc)}
            event_type = "method.failed"
            payload = {"status": record.status, "error": record.error}
        except Exception:
            record.status = "failed"
            record.error = {
                "code": "internal_error",
                "message": "Internal D-Bus call error",
            }
            event_type = "method.failed"
            payload = {"status": record.status, "error": record.error}
        record.completed_at = utc_now()
        record.duration_ms = round((time.monotonic() - started) * 1000, 3)
        payload.update(
            {
                "service": record.service,
                "object_path": record.object_path,
                "interface": record.interface,
                "method": record.method,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "duration_ms": record.duration_ms,
            }
        )
        await self.events.publish(
            TCUEvent.create(event_type, payload, request_id=record.request_id)
        )

    async def _ensure_signal_subscription(self) -> None:
        if self._unsubscribe_signals is not None:
            return

        def on_signal(payload: dict[str, Any]) -> None:
            task = asyncio.create_task(
                self.events.publish(TCUEvent.create("dbus.signal", payload))
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        self._unsubscribe_signals = await self.client.subscribe_signals(on_signal)

    def _on_status(self, status: dict[str, Any]) -> None:
        if not status.get("connected") and self._unsubscribe_signals is not None:
            self._unsubscribe_signals()
            self._unsubscribe_signals = None

        async def publish() -> None:
            await self.events.publish(
                TCUEvent.create("connection.status", {"status": status})
            )
            if status.get("error"):
                await self.events.publish(
                    TCUEvent.create(
                        "tcu.error",
                        {"code": "connection_lost", "message": status["error"]},
                    )
                )

        task = asyncio.create_task(publish())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._unsubscribe_signals is not None:
            self._unsubscribe_signals()
            self._unsubscribe_signals = None
        if self._unsubscribe_status is not None:
            self._unsubscribe_status()
            self._unsubscribe_status = None
        await self.client.disconnect()
