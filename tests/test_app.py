import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

from config import load_config, load_favorites, save_favorite  # noqa: E402
import serial_manager as serial_manager_module  # noqa: E402
from serial_manager import SerialConnection, SerialManager  # noqa: E402


def test_load_config_reads_default_file():
    config = load_config()
    assert len(config.ports) >= 1
    assert all(preset.device.startswith("/dev/") for preset in config.ports)
    assert len(config.macros) >= 1
    assert all(macro.label for macro in config.macros)


def test_load_config_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    config = load_config(missing)
    assert config.ports == []
    assert config.macros == []


def test_load_config_custom_file(tmp_path):
    custom = tmp_path / "config.yaml"
    custom.write_text(
        """
ports:
  - name: "Test"
    device: "/dev/ttyTEST0"
    baudrate: 9600
macros:
  - label: "Ping"
    command: "ping"
    raw: true
    device: "/dev/ttyTEST0"
"""
    )
    config = load_config(custom)
    assert config.ports == [
        __import__("config").PortPreset(name="Test", device="/dev/ttyTEST0", baudrate=9600)
    ]
    assert config.macros[0].label == "Ping"
    assert config.macros[0].raw is True
    assert config.macros[0].device == "/dev/ttyTEST0"


def test_save_favorite_adds_and_updates_port(tmp_path):
    favorites_path = tmp_path / "favorites.yaml"
    port_preset = __import__("config").PortPreset
    save_favorite(
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=9600),
        favorites_path,
    )
    save_favorite(
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=19200),
        favorites_path,
    )

    assert load_favorites(favorites_path) == [
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=19200)
    ]


def test_serial_manager_get_or_create_is_idempotent():
    manager = SerialManager()
    conn1 = manager.get_or_create("/dev/ttyFAKE0", 9600)
    conn2 = manager.get_or_create("/dev/ttyFAKE0", 115200)
    assert conn1 is conn2


def test_serial_manager_discovers_all_supported_device_patterns(monkeypatch):
    matches = {
        "/dev/ttyUSB*": ["/dev/ttyUSB1", "/dev/ttyUSB0"],
        "/dev/ttyACM*": ["/dev/ttyACM0"],
        "/dev/ttyAMA*": ["/dev/ttyAMA0"],
        "/dev/serial/by-id/*": ["/dev/serial/by-id/usb-adapter"],
    }
    monkeypatch.setattr(
        serial_manager_module.glob, "glob", lambda pattern: matches[pattern]
    )

    assert SerialManager().list_available_devices() == [
        "/dev/ttyUSB0",
        "/dev/ttyUSB1",
        "/dev/ttyACM0",
        "/dev/ttyAMA0",
        "/dev/serial/by-id/usb-adapter",
    ]


def test_serial_connection_send_requires_open_port():
    conn = SerialConnection(device="/dev/ttyFAKE1")
    with pytest.raises(Exception):
        conn.send("hello")


def test_serial_connection_connect_invalid_device_raises():
    conn = SerialConnection(device="/dev/ttyDOES-NOT-EXIST")
    with pytest.raises(Exception):
        conn.connect()
    assert conn.error is not None
    assert not conn.is_open
