import sys
import time
from pathlib import Path

import can
import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

from canbus.manager import CANManager
from canbus.models import CANProfile
from canbus.profiles import save_dbc, save_profile


DBC = """VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 291 Example: 1 ECU
 SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU
"""


def wait_for_frames(manager, count=1):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        frames, _, _ = manager.frames()
        if len(frames) >= count:
            return frames
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for CAN frames")


def test_virtual_backend_receives_classic_and_fd_frames():
    for fd in (False, True):
        channel = f"test-{time.monotonic_ns()}"
        profile = CANProfile(
            id="profile",
            name="Virtual",
            interface="virtual",
            channel=channel,
            fd=fd,
            data_bitrate=2_000_000 if fd else None,
        )
        manager = CANManager()
        peer = can.Bus(interface="virtual", channel=channel)
        try:
            manager.connect(profile)
            message = can.Message(
                arbitration_id=0x123,
                data=bytes(range(12 if fd else 8)),
                is_fd=fd,
            )
            peer.send(message)
            frame = wait_for_frames(manager)[0]
            assert frame["arbitration_id"] == 0x123
            assert frame["is_fd"] is fd
            assert frame["dlc"] == len(message.data)
        finally:
            manager.shutdown()
            peer.shutdown()


def test_transmission_is_disabled_until_explicitly_enabled():
    channel = f"test-{time.monotonic_ns()}"
    manager = CANManager()
    profile = CANProfile(
        id="profile",
        name="Virtual",
        interface="virtual",
        channel=channel,
        listen_only=False,
    )
    peer = can.Bus(interface="virtual", channel=channel)
    try:
        manager.connect(profile)
        with pytest.raises(PermissionError):
            manager.send(0x123, b"\x01")
        manager.set_transmit_enabled(True)
        result = manager.send(0x123, b"\x01")
        received = peer.recv(timeout=1)
        assert result["status"] == "sent"
        assert received is not None
        assert received.data == b"\x01"
    finally:
        manager.shutdown()
        peer.shutdown()


def test_transmit_mode_cannot_be_enabled_for_listen_only_profile():
    manager = CANManager()
    manager.connect(
        CANProfile(
            id="profile",
            name="Virtual",
            interface="virtual",
            channel=f"test-{time.monotonic_ns()}",
        )
    )
    try:
        with pytest.raises(ValueError, match="listen_only=false"):
            manager.set_transmit_enabled(True)
    finally:
        manager.shutdown()


def test_receive_buffer_is_bounded_and_counts_drops():
    manager = CANManager(max_frames=2)
    frame = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "arbitration_id": 1,
        "is_extended_id": False,
        "is_fd": False,
        "is_remote_frame": False,
        "is_error_frame": False,
        "dlc": 1,
        "data": "00",
        "channel": None,
    }
    for _ in range(3):
        manager._append(frame)
    frames, sequence, dropped = manager.frames()
    assert len(frames) == 2
    assert sequence == 3
    assert dropped == 1
    assert manager.status().dropped_frames == 1


@pytest.fixture
def can_client(monkeypatch, tmp_path):
    from canbus.api import can_manager
    import main

    can_manager.disconnect()
    monkeypatch.setenv("RPI_SEMIAUTOMATOR_CAN_PROFILES", str(tmp_path / "profiles.yaml"))
    monkeypatch.setenv("RPI_SEMIAUTOMATOR_DBC_DIR", str(tmp_path / "dbc"))
    monkeypatch.delenv("RPI_SEMIAUTOMATOR_API_TOKEN", raising=False)
    yield TestClient(main.app)
    can_manager.shutdown()


def test_can_profile_crud_and_validation(can_client):
    created = can_client.post(
        "/api/can/profiles",
        json={
            "name": "Virtual",
            "interface": "virtual",
            "channel": "profile-api",
            "bitrate": 500000,
        },
    )
    assert created.status_code == 201
    profile = created.json()
    assert profile["listen_only"] is True
    assert can_client.get("/api/can/profiles").json() == [profile]
    assert can_client.post(
        "/api/can/profiles",
        json={"name": "bad", "interface": "invalid", "channel": "can0"},
    ).status_code == 400
    assert can_client.put(f"/api/can/profiles/{profile['id']}", json=profile).status_code == 200
    assert can_client.delete(f"/api/can/profiles/{profile['id']}").json() == {"ok": True}


def test_can_api_transmit_gate_and_virtual_send(can_client):
    created = can_client.post(
        "/api/can/profiles",
        json={
            "name": "Virtual",
            "interface": "virtual",
            "channel": "api-send",
            "listen_only": False,
        },
    )
    profile_id = created.json()["id"]
    assert can_client.post("/api/can/connect", json={"profile_id": profile_id}).status_code == 200
    frame = {"arbitration_id": 291, "data": "01 02"}
    assert can_client.post("/api/can/send", json=frame).status_code == 409
    assert can_client.post("/api/can/transmit-mode", json={"enabled": True}).status_code == 200
    response = can_client.post("/api/can/send", json=frame)
    assert response.status_code == 200
    assert response.json()["frame"]["data"] == "01 02"
    assert can_client.get("/api/can/frames").json()["frames"][-1]["is_fd"] is False


def test_dbc_upload_validation_preview_and_value_ranges(can_client):
    upload = can_client.post(
        "/api/can/dbcs",
        params={"filename": "../../unsafe.dbc"},
        content=DBC.encode(),
    )
    assert upload.status_code == 201
    dbc_id = upload.json()["id"]
    assert upload.json()["name"] == "unsafe.dbc"
    assert can_client.post(
        "/api/can/dbcs", params={"filename": "bad.dbc"}, content=b"not a DBC"
    ).status_code == 400
    profile = can_client.post(
        "/api/can/profiles",
        json={
            "name": "DBC",
            "interface": "virtual",
            "channel": "dbc-preview",
            "dbc_id": dbc_id,
        },
    ).json()
    assert can_client.get(f"/api/can/dbcs/{dbc_id}/messages").json()[0]["name"] == "Example"
    preview = can_client.post(
        "/api/can/dbc/preview",
        json={"profile_id": profile["id"], "message": "Example", "signals": {"Value": 42}},
    )
    assert preview.status_code == 200
    assert preview.json()["data"] == "2A"
    invalid = can_client.post(
        "/api/can/dbc/preview",
        json={"profile_id": profile["id"], "message": "Example", "signals": {"Value": 256}},
    )
    assert invalid.status_code == 400


def test_can_endpoints_use_existing_optional_api_token(can_client, monkeypatch):
    monkeypatch.setenv("RPI_SEMIAUTOMATOR_API_TOKEN", "secret-test-token")
    assert can_client.get("/api/can/status").status_code == 401
    assert can_client.get(
        "/api/can/status", params={"token": "secret-test-token"}
    ).status_code == 200
