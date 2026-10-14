import asyncio
import base64
import sys
import time
from pathlib import Path

import pytest
from dbus_next import Message, MessageType, Variant
from dbus_next.signature import SignatureTree
from fastapi import FastAPI
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

from tcu.api.routes import register_api
from tcu.core.config import TCUConfig, load_config
from tcu.core.models import dbus_value_to_json, json_to_dbus_value
from tcu.core.service import TCUService
from tcu.dbus.client import DBusClient
from tcu.dbus.exceptions import (
    InvalidDBusArguments,
    TCUQueueFull,
    TCUNotConnected,
    TransportNotConfigured,
)
from tcu.events.dispatcher import EventDispatcher
from tcu.events.models import TCUEvent


class FakeDBusClient:
    def __init__(self, *, connected=True, delay=0):
        self.connected = connected
        self.delay = delay
        self.status_handler = None
        self.signal_handler = None

    def status(self):
        return {
            "enabled": True,
            "connected": self.connected,
            "transport": "fake",
            "last_changed_at": "2026-01-01T00:00:00+00:00",
            "error": None,
        }

    async def connect(self):
        self.connected = True
        return self.status()

    async def disconnect(self):
        self.connected = False
        return self.status()

    async def list_services(self):
        if not self.connected:
            raise TCUNotConnected("not connected")
        return ["org.example.Test"]

    async def list_objects(self, service=None):
        return [{"service": service, "object_path": "/"}]

    async def introspect(self, service, object_path):
        if not self.connected:
            raise TCUNotConnected("not connected")
        return "<node><interface name='org.example.Test'/></node>"

    async def call_method(
        self, service, object_path, interface, method, arguments, timeout_seconds=None
    ):
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.connected:
            raise TCUNotConnected("not connected")
        if arguments:
            raise InvalidDBusArguments("expected no arguments")
        return [{"state": "ready"}], "a{sv}"

    async def subscribe_signals(self, handler):
        self.signal_handler = handler
        return lambda: setattr(self, "signal_handler", None)

    def subscribe_status(self, handler):
        self.status_handler = handler
        return lambda: setattr(self, "status_handler", None)


def make_service(client=None, *, queue_size=100):
    config = TCUConfig(
        enabled=True,
        bus_address="unix:path=/run/tcu/dbus.sock",
        timeout_seconds=1,
        known_object_paths=("/",),
    )
    events = EventDispatcher(queue_size=queue_size)
    return TCUService(config, client or FakeDBusClient(), events), events


def test_config_defaults_disabled_and_rejects_tcp_transport(monkeypatch):
    monkeypatch.delenv("DBUS_ENABLED", raising=False)
    monkeypatch.delenv("DBUS_ADDRESS", raising=False)
    monkeypatch.delenv("DBUS_OBJECT_PATHS", raising=False)
    monkeypatch.delenv("DBUS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TCU_ENABLED", raising=False)
    monkeypatch.delenv("TCU_DBUS_ADDRESS", raising=False)
    monkeypatch.delenv("TCU_OBJECT_PATHS", raising=False)
    monkeypatch.delenv("TCU_TIMEOUT_SECONDS", raising=False)
    assert load_config().enabled is False
    monkeypatch.setenv("TCU_ENABLED", "true")
    monkeypatch.setenv("TCU_DBUS_ADDRESS", "tcp:host=127.0.0.1,port=5555")
    with pytest.raises(ValueError, match="Unix socket"):
        load_config()


def test_dbus_environment_names_take_precedence_over_legacy_tcu_names(monkeypatch):
    monkeypatch.setenv("DBUS_ENABLED", "true")
    monkeypatch.setenv("DBUS_ADDRESS", "unix:path=/run/dbus.sock")
    monkeypatch.setenv("DBUS_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("DBUS_OBJECT_PATHS", "/,/org/example")
    monkeypatch.setenv("TCU_ENABLED", "false")
    monkeypatch.setenv("TCU_DBUS_ADDRESS", "unix:path=/run/legacy.sock")

    config = load_config()

    assert config.enabled is True
    assert config.bus_address == "unix:path=/run/dbus.sock"
    assert config.timeout_seconds == 5
    assert config.known_object_paths == ("/", "/org/example")


def test_dbus_client_introspects_validates_calls_and_restarts_signal_registration(
    monkeypatch,
):
    import tcu.dbus.client as dbus_client_module

    xml = """<node>
      <interface name="org.example.Test">
        <method name="GetStatus"><arg type="s" direction="out"/></method>
        <method name="SetMode"><arg type="s" direction="in"/></method>
      </interface>
    </node>"""

    class Bus:
        def __init__(self):
            self.handlers = []
            self.calls = []
            self.added = 0
            self.removed = 0

        async def connect(self):
            return self

        def disconnect(self):
            pass

        async def wait_for_disconnect(self):
            await asyncio.Future()

        def add_message_handler(self, handler):
            self.handlers.append(handler)
            self.added += 1

        def remove_message_handler(self, handler):
            self.handlers.remove(handler)
            self.removed += 1

        async def call(self, message):
            self.calls.append(message)
            if message.member == "Introspect":
                return Message(
                    message_type=MessageType.METHOD_RETURN,
                    reply_serial=1,
                    signature="s",
                    body=[xml],
                )
            if message.member == "GetStatus":
                return Message(
                    message_type=MessageType.METHOD_RETURN,
                    reply_serial=1,
                    signature="s",
                    body=["ready"],
                )
            return Message(
                message_type=MessageType.METHOD_RETURN, reply_serial=1
            )

    buses = []

    def bus_factory(bus_address):
        bus = Bus()
        buses.append(bus)
        return bus

    monkeypatch.setattr(dbus_client_module, "MessageBus", bus_factory)
    config = TCUConfig(
        enabled=True,
        bus_address="unix:path=/run/tcu.sock",
        timeout_seconds=1,
        known_object_paths=("/",),
    )

    async def scenario():
        client = DBusClient(config)
        service = TCUService(config, client)
        events = service.events.subscribe()
        await service.connect()
        assert (await events.get()).event_type == "connection.status"
        assert await client.call_method(
            "org.example.Test", "/", "org.example.Test", "GetStatus", []
        ) == (["ready"], "s")
        with pytest.raises(InvalidDBusArguments):
            await client.call_method(
                "org.example.Test", "/", "org.example.Test", "SetMode", [True]
            )
        assert not any(message.member == "SetMode" for message in buses[0].calls)

        signal = Message(
            message_type=MessageType.SIGNAL,
            path="/",
            interface="org.example.Test",
            member="Changed",
            signature="oay",
            body=["/child", b"\x01"],
            sender="org.example.Test",
        )
        client._handle_message(signal)
        signal_event = await asyncio.wait_for(events.get(), 1)
        assert signal_event.event_type == "dbus.signal"
        assert signal_event.payload["member"] == "Changed"
        assert signal_event.payload["arguments"] == [
            {"$type": "object_path", "value": "/child"},
            {"$type": "bytes", "base64": "AQ=="},
        ]
        assert buses[0].added == 1
        await service.disconnect()
        assert buses[0].removed == 1

        await service.connect()
        assert buses[1].added == 1
        assert sum(message.member == "AddMatch" for message in buses[1].calls) == 1
        await service.shutdown()

    asyncio.run(scenario())


def test_dbus_client_reports_disabled_transport_without_connecting():
    config = TCUConfig(
        enabled=False,
        bus_address=None,
        timeout_seconds=1,
        known_object_paths=("/",),
    )

    async def scenario():
        with pytest.raises(TransportNotConfigured, match="disabled"):
            await DBusClient(config).connect()

    asyncio.run(scenario())


def test_dbus_typed_json_conversion_for_byte_arrays_structs_and_variants():
    byte_signature = SignatureTree._get("ay").types[0]
    value = json_to_dbus_value(
        {"$type": "bytes", "base64": base64.b64encode(b"\x00\xff").decode()},
        byte_signature,
    )
    assert value == b"\x00\xff"
    assert dbus_value_to_json(value, byte_signature) == {
        "$type": "bytes",
        "base64": "AP8=",
    }

    struct_signature = SignatureTree._get("(is)").types[0]
    assert json_to_dbus_value(
        {"$type": "struct", "signature": "(is)", "value": [3, "ok"]},
        struct_signature,
    ) == (3, "ok")
    assert dbus_value_to_json((3, "ok"), struct_signature) == {
        "$type": "struct",
        "signature": "(is)",
        "value": [3, "ok"],
    }

    variant = dbus_value_to_json(Variant("s", "ready"), SignatureTree._get("v").types[0])
    assert variant == {"$type": "variant", "signature": "s", "value": "ready"}
    assert json_to_dbus_value(variant, SignatureTree._get("v").types[0]) == Variant(
        "s", "ready"
    )


def test_dbus_conversion_rejects_wrong_types_and_untyped_dicts():
    with pytest.raises(ValueError, match="JSON integer"):
        json_to_dbus_value(True, SignatureTree._get("i").types[0])
    with pytest.raises(ValueError, match="typed dict"):
        json_to_dbus_value({}, SignatureTree._get("a{sv}").types[0])
    with pytest.raises(ValueError, match="signature"):
        json_to_dbus_value(
            {"$type": "struct", "signature": "(ss)", "value": ["a", "b"]},
            SignatureTree._get("(is)").types[0],
        )


def test_event_dispatcher_bounds_slow_client_queues_and_cleans_subscribers():
    async def scenario():
        dispatcher = EventDispatcher(queue_size=1, history_size=2)
        slow = dispatcher.subscribe()
        for index in range(3):
            await dispatcher.publish(TCUEvent.create("tcu.error", {"index": index}))
        assert dispatcher.dropped_for(slow) == 2
        assert (await slow.get()).payload == {"index": 2}
        assert [event.payload["index"] for event in dispatcher.history()] == [1, 2]
        dispatcher.unsubscribe(slow)
        assert dispatcher.client_count == 0
        capped = EventDispatcher(max_clients=1)
        first = capped.subscribe()
        with pytest.raises(RuntimeError, match="limit"):
            capped.subscribe()
        capped.unsubscribe(first)

    asyncio.run(scenario())


def test_service_call_emits_start_and_completion_and_keeps_result():
    async def scenario():
        service, _ = make_service()
        queue = service.events.subscribe()
        call = service.submit_call("org.example.Test", "/", "org.example.Test", "Get", [])
        while call.status != "completed":
            await asyncio.sleep(0)
        assert call.result == [{"state": "ready"}]
        assert call.result_signature == "a{sv}"
        assert (await queue.get()).event_type == "method.started"
        completed = await queue.get()
        assert completed.event_type == "method.completed"
        assert completed.request_id == call.request_id
        await service.shutdown()

    asyncio.run(scenario())


def test_service_timeout_is_reported_as_failed_result():
    async def scenario():
        service, _ = make_service(FakeDBusClient(delay=0.1))
        call = service.submit_call(
            "org.example.Test", "/", "org.example.Test", "Slow", [], 0.01
        )
        while call.status not in {"completed", "failed"}:
            await asyncio.sleep(0)
        assert call.status == "failed"
        assert call.error["code"] == "timeout"
        await service.shutdown()

    asyncio.run(scenario())


def test_service_rejects_calls_when_bounded_queue_is_full():
    async def scenario():
        service, _ = make_service(FakeDBusClient(delay=1))
        for _ in range(64):
            service.submit_call(
                "org.example.Test", "/", "org.example.Test", "Get", [], 2
            )
        with pytest.raises(TCUQueueFull):
            service.submit_call("org.example.Test", "/", "org.example.Test", "Get", [])
        await service.shutdown()

    asyncio.run(scenario())


def test_tcu_api_call_validation_status_result_events_and_openapi():
    service, _ = make_service()
    app = FastAPI()
    register_api(app, lambda token: None, lambda request: None, service)
    with TestClient(app) as client:
        assert client.get("/api/v1/dbus/status").json()["connected"] is True
        assert client.get("/api/v1/tcu/status").json()["connected"] is True
        assert client.get("/api/v1/tcu/services").json() == ["org.example.Test"]
        assert client.get("/api/v1/tcu/introspect").status_code == 422
        missing_query = client.get("/api/v1/tcu/introspect")
        assert missing_query.json()["error"]["code"] == "invalid_request"
        introspection = client.get(
            "/api/v1/tcu/introspect",
            params={"service": "org.example.Test", "object_path": "/"},
        )
        assert introspection.status_code == 200
        assert "<interface" in introspection.json()["xml"]
        invalid = client.post("/api/v1/tcu/calls", json={"service": "missing"})
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_request"
        invalid_name = client.post(
            "/api/v1/tcu/calls",
            json={
                "service": "bad",
                "object_path": "/",
                "interface": "org.example.Test",
                "method": "Get",
                "arguments": [],
            },
        )
        assert invalid_name.status_code == 400

        accepted = client.post(
            "/api/v1/tcu/calls",
            json={
                "service": "org.example.Test",
                "object_path": "/",
                "interface": "org.example.Test",
                "method": "Get",
                "arguments": [],
            },
        )
        assert accepted.status_code == 202
        request_id = accepted.json()["request_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = client.get(f"/api/v1/tcu/calls/{request_id}").json()
            if result["status"] == "completed":
                break
            time.sleep(0.01)
        assert result["result"] == [{"state": "ready"}]
        assert result["duration_ms"] is not None
        assert client.get("/api/v1/tcu/calls/missing").status_code == 404
        assert client.post(
            "/api/v1/tcu/calls",
            content=b"x" * (256 * 1024 + 1),
            headers={"content-type": "application/json"},
        ).status_code == 413
        schema = client.get("/openapi.json").json()
        assert "/api/v1/dbus/calls" in schema["paths"]
        assert "/api/v1/tcu/calls" in schema["paths"]
        assert "/api/v1/tcu/ws" not in schema["paths"]


def test_tcu_api_websocket_receives_connection_and_method_events():
    service, _ = make_service()
    app = FastAPI()
    register_api(app, lambda token: None, lambda request: None, service)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/tcu/ws") as websocket:
            client.post("/api/v1/tcu/connect")
            assert websocket.receive_json()["event_type"] == "connection.status"
            accepted = client.post(
                "/api/v1/tcu/calls",
                json={
                    "service": "org.example.Test",
                    "object_path": "/",
                    "interface": "org.example.Test",
                    "method": "Get",
                    "arguments": [],
                },
            )
            assert accepted.status_code == 202
            event_types = []
            while "method.completed" not in event_types:
                event_types.append(websocket.receive_json()["event_type"])
            assert event_types == ["method.started", "method.completed"]
        assert service.events.client_count == 0


def test_tcu_api_reports_unavailable_dbus_as_service_unavailable():
    service, _ = make_service(FakeDBusClient(connected=False))
    app = FastAPI()
    register_api(app, lambda token: None, lambda request: None, service)
    with TestClient(app) as client:
        response = client.get("/api/v1/tcu/services")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "not_connected"
