"""Asynchronous dbus-next client over an explicitly configured local tunnel."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.signature import SignatureTree
from dbus_next.validators import is_bus_name_valid, is_object_path_valid

from ..core.config import TCUConfig
from ..core.models import dbus_value_to_json, json_to_dbus_value
from .exceptions import (
    DbusRemoteError,
    DbusTargetNotFound,
    InvalidDBusArguments,
    TCUError,
    TCUNotConnected,
    TCUTimeout,
    TransportNotConfigured,
)

DBUS_SERVICE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_INTERFACE = "org.freedesktop.DBus"


class DBusClient:
    def __init__(self, config: TCUConfig) -> None:
        self.config = config
        self._bus: MessageBus | None = None
        self._last_error: str | None = None
        self._last_change = datetime.now(timezone.utc).isoformat()
        self._introspection: dict[tuple[str, str], ET.Element] = {}
        self._signal_handlers: set[Callable[[dict[str, Any]], None]] = set()
        self._status_handlers: set[Callable[[dict[str, Any]], None]] = set()
        self._signal_match_installed = False
        self._message_handler_added = False
        self._monitor_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "connected": self._bus is not None,
            "transport": self.config.transport_label,
            "last_changed_at": self._last_change,
            "error": self._last_error,
        }

    async def connect(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            return await self._connect_unlocked()

    async def _connect_unlocked(self) -> dict[str, Any]:
        if self._bus is not None:
            return self.status()
        if not self.config.enabled:
            raise TransportNotConfigured("TCU support is disabled; set TCU_ENABLED=true")
        address = self.config.bus_address
        if not address:
            raise TransportNotConfigured(
                "No D-Bus tunnel is configured; set TCU_DBUS_ADDRESS to a local Unix "
                "socket provided by a separately secured proxy"
            )
        if not address.startswith("unix:path="):
            raise TransportNotConfigured(
                "Only a local Unix socket from a separately secured proxy is supported"
            )
        bus = None
        try:
            bus = MessageBus(bus_address=address)
            await asyncio.wait_for(bus.connect(), self.config.timeout_seconds)
        except asyncio.TimeoutError as exc:
            if bus is not None:
                bus.disconnect()
            self._last_error = "Timed out connecting to the configured D-Bus transport"
            self._last_change = datetime.now(timezone.utc).isoformat()
            raise TCUTimeout(self._last_error) from exc
        except Exception as exc:
            if bus is not None:
                bus.disconnect()
            self._last_error = str(exc)[:500]
            self._last_change = datetime.now(timezone.utc).isoformat()
            raise TransportNotConfigured(
                "Could not connect to the configured local D-Bus transport"
            ) from exc
        self._bus = bus
        self._last_error = None
        self._introspection.clear()
        self._last_change = datetime.now(timezone.utc).isoformat()
        self._monitor_task = asyncio.create_task(self._watch_disconnect(bus))
        await self._install_signal_handler()
        return self.status()

    async def disconnect(self) -> dict[str, Any]:
        async with self._lifecycle_lock:
            return await self._disconnect_unlocked()

    async def _disconnect_unlocked(self) -> dict[str, Any]:
        bus, self._bus = self._bus, None
        self._introspection.clear()
        message_handler_added = self._message_handler_added
        self._signal_match_installed = False
        self._message_handler_added = False
        monitor, self._monitor_task = self._monitor_task, None
        if bus is not None:
            if message_handler_added:
                bus.remove_message_handler(self._handle_message)
            bus.disconnect()
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        self._last_change = datetime.now(timezone.utc).isoformat()
        return self.status()

    async def list_services(self) -> list[str]:
        reply = await self._daemon_call("ListNames", "", [])
        return sorted(reply.body[0])

    async def list_objects(self, service: str | None = None) -> list[dict[str, str]]:
        return [
            {"service": service, "object_path": path, "discovery": "configured"}
            for path in self.config.known_object_paths
            for service in ([service] if service else [])
        ] if service else [
            {"object_path": path, "discovery": "configured"}
            for path in self.config.known_object_paths
        ]

    async def introspect(self, service: str, object_path: str) -> str:
        node = await self._get_introspection(service, object_path)
        return ET.tostring(node, encoding="unicode")

    async def call_method(
        self,
        service: str,
        object_path: str,
        interface: str,
        method: str,
        arguments: list[Any],
        timeout_seconds: float | None = None,
    ) -> tuple[list[Any], str]:
        node = await self._get_introspection(service, object_path, timeout_seconds)
        interface_node = next(
            (item for item in node.findall("interface") if item.get("name") == interface),
            None,
        )
        if interface_node is None:
            raise DbusTargetNotFound(f"Unknown D-Bus interface: {interface}")
        method_node = next(
            (
                item
                for item in interface_node.findall("method")
                if item.get("name") == method
            ),
            None,
        )
        if method_node is None:
            raise DbusTargetNotFound(f"Unknown D-Bus method: {method}")
        in_signature = "".join(
            item.get("type", "")
            for item in method_node.findall("arg")
            if item.get("direction", "in") == "in"
        )
        input_types = SignatureTree._get(in_signature).types
        if len(arguments) != len(input_types):
            raise InvalidDBusArguments(
                f"Expected {len(input_types)} arguments for signature '{in_signature}'"
            )
        try:
            body = [
                json_to_dbus_value(value, signature)
                for value, signature in zip(arguments, input_types, strict=True)
            ]
        except (TypeError, ValueError) as exc:
            raise InvalidDBusArguments(str(exc)) from exc
        reply = await self._call(
            Message(
                destination=service,
                path=object_path,
                interface=interface,
                member=method,
                signature=in_signature,
                body=body,
            ),
            timeout_seconds,
        )
        result_signature = reply.signature or ""
        output_types = SignatureTree._get(result_signature).types
        result = [
            dbus_value_to_json(value, signature)
            for value, signature in zip(reply.body or [], output_types, strict=True)
        ]
        return result, result_signature

    async def subscribe_signals(
        self, handler: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._signal_handlers.add(handler)
        try:
            await self._install_signal_handler()
        except Exception:
            self._signal_handlers.discard(handler)
            bus = self._bus
            if bus is not None and self._message_handler_added:
                bus.remove_message_handler(self._handle_message)
                self._message_handler_added = False
            raise

        def unsubscribe() -> None:
            self._signal_handlers.discard(handler)

        return unsubscribe

    def subscribe_status(
        self, handler: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._status_handlers.add(handler)

        def unsubscribe() -> None:
            self._status_handlers.discard(handler)

        return unsubscribe

    async def _watch_disconnect(self, bus: MessageBus) -> None:
        try:
            await bus.wait_for_disconnect()
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        if self._bus is not bus:
            return
        self._bus = None
        self._signal_match_installed = False
        self._message_handler_added = False
        self._introspection.clear()
        self._last_error = "D-Bus connection was lost"
        self._last_change = datetime.now(timezone.utc).isoformat()
        status = self.status()
        for handler in tuple(self._status_handlers):
            try:
                handler(status)
            except Exception:
                continue

    async def _install_signal_handler(self) -> None:
        if self._bus is None or not self._signal_handlers:
            return
        if not self._message_handler_added:
            self._bus.add_message_handler(self._handle_message)
            self._message_handler_added = True
        if not self._signal_match_installed:
            await self._daemon_call("AddMatch", "s", ["type='signal'"])
            self._signal_match_installed = True

    def _handle_message(self, message: Message) -> bool:
        if message.message_type != MessageType.SIGNAL:
            return False
        signature_types = SignatureTree._get(message.signature or "").types
        payload = {
            "service": message.sender,
            "object_path": message.path,
            "interface": message.interface,
            "member": message.member,
            "signature": message.signature or "",
            "arguments": [
                dbus_value_to_json(value, signature)
                for value, signature in zip(message.body or [], signature_types, strict=True)
            ],
        }
        for handler in tuple(self._signal_handlers):
            try:
                handler(payload)
            except Exception:
                continue
        return False

    async def _get_introspection(
        self, service: str, object_path: str, timeout_seconds: float | None = None
    ) -> ET.Element:
        if not is_bus_name_valid(service):
            raise InvalidDBusArguments("service is not a valid D-Bus bus name")
        if not is_object_path_valid(object_path):
            raise InvalidDBusArguments("object_path is not a valid D-Bus object path")
        self._require_bus()
        key = (service, object_path)
        if key in self._introspection:
            return self._introspection[key]
        reply = await self._call(
            Message(
                destination=service,
                path=object_path,
                interface="org.freedesktop.DBus.Introspectable",
                member="Introspect",
            ),
            timeout_seconds,
        )
        try:
            node = ET.fromstring(reply.body[0])
        except (ET.ParseError, IndexError, TypeError) as exc:
            raise TCUError("D-Bus Introspection returned invalid XML") from exc
        self._introspection[key] = node
        return node

    async def _daemon_call(self, member: str, signature: str, body: list[Any]) -> Message:
        return await self._call(
            Message(
                destination=DBUS_SERVICE,
                path=DBUS_PATH,
                interface=DBUS_INTERFACE,
                member=member,
                signature=signature,
                body=body,
            )
        )

    async def _call(
        self, message: Message, timeout_seconds: float | None = None
    ) -> Message:
        bus = self._require_bus()
        timeout = timeout_seconds or self.config.timeout_seconds
        try:
            reply = await asyncio.wait_for(bus.call(message), timeout)
        except asyncio.TimeoutError as exc:
            self._last_error = "D-Bus call timed out; remote execution may be uncertain"
            raise TCUTimeout(self._last_error) from exc
        except Exception as exc:
            self._last_error = str(exc)[:500]
            raise TCUNotConnected("D-Bus connection failed during the request") from exc
        if reply is None:
            raise TCUNotConnected("D-Bus connection closed before a reply was received")
        if reply.message_type == MessageType.ERROR:
            name = reply.error_name or "org.freedesktop.DBus.Error.Failed"
            detail = str(reply.body[0])[:500] if reply.body else "Remote D-Bus error"
            if name.endswith(
                (
                    "ServiceUnknown",
                    "UnknownService",
                    "NameHasNoOwner",
                    "UnknownObject",
                    "UnknownInterface",
                    "UnknownMethod",
                )
            ):
                raise DbusTargetNotFound(f"{name}: {detail}")
            raise DbusRemoteError(name, detail)
        if reply.message_type != MessageType.METHOD_RETURN:
            raise TCUError("D-Bus returned an unexpected message type")
        return reply

    def _require_bus(self) -> MessageBus:
        if self._bus is None:
            raise TCUNotConnected("TCU D-Bus is not connected")
        return self._bus
