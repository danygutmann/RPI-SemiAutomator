"""Application-facing TCU orchestration, independent of GUI and HTTP."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

from ..dbus.client import DBusClient
from ..dbus.exceptions import TCUError, TCUTimeout
from ..events.dispatcher import EventDispatcher
from ..events.models import TCUEvent
from .config import TCUConfig
from .models import CallRecord, utc_now

MAX_CALL_HISTORY = 1000
MAX_ACTIVE_CALLS = 16


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
        self._unsubscribe_signals = None

    def status(self) -> dict[str, Any]:
        return self.client.status()

    async def connect(self) -> dict[str, Any]:
        try:
            status = await self.client.connect()
            await self.events.publish(
                TCUEvent.create("connection.status", {"status": status})
            )
            await self._ensure_signal_subscription()
            return status
        except TCUError as exc:
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
        timeout = timeout_seconds or self.config.timeout_seconds
        if not 0 < timeout <= 60:
            raise ValueError("timeout_seconds must be greater than 0 and at most 60")
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
                "message": "Internal TCU call error",
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

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._unsubscribe_signals is not None:
            self._unsubscribe_signals()
            self._unsubscribe_signals = None
        await self.client.disconnect()
