import sys
from pathlib import Path

import pytest
import yaml

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

from config import (  # noqa: E402
    backup_settings,
    load_config,
    load_favorites,
    load_macros,
    remove_favorite,
    remove_macro,
    save_favorite,
    save_macro,
)
import serial_manager as serial_manager_module  # noqa: E402
from serial_manager import SerialConnection, SerialManager  # noqa: E402


def test_load_config_reads_default_file():
    config = load_config()
    assert len(config.ports) >= 1
    assert all(preset.device.startswith("/dev/") for preset in config.ports)
    # The default configuration no longer ships predefined ("standard")
    # macros; only individually created macros (stored separately) exist.
    assert config.macros == []


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


def test_remove_favorite_deletes_entry(tmp_path):
    favorites_path = tmp_path / "favorites.yaml"
    port_preset = __import__("config").PortPreset
    save_favorite(
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=9600),
        favorites_path,
    )
    save_favorite(
        port_preset(name="Other", device="/dev/ttyTEST1", baudrate=9600),
        favorites_path,
    )

    remove_favorite("/dev/ttyTEST0", favorites_path)

    assert load_favorites(favorites_path) == [
        port_preset(name="Other", device="/dev/ttyTEST1", baudrate=9600)
    ]


def test_remove_favorite_missing_device_is_noop(tmp_path):
    favorites_path = tmp_path / "favorites.yaml"
    port_preset = __import__("config").PortPreset
    save_favorite(
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=9600),
        favorites_path,
    )

    remove_favorite("/dev/ttyDOES-NOT-EXIST", favorites_path)

    assert load_favorites(favorites_path) == [
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=9600)
    ]


def test_save_macro_adds_and_updates_macro(tmp_path):
    macros_path = tmp_path / "macros.yaml"
    macro = __import__("config").Macro
    save_macro(macro(label="Ping", command="ping"), macros_path)
    save_macro(macro(label="Ping", command="ping -c1"), macros_path)

    assert load_macros(macros_path) == [macro(label="Ping", command="ping -c1")]


def test_save_macro_device_scoped_is_independent_of_global(tmp_path):
    macros_path = tmp_path / "macros.yaml"
    macro = __import__("config").Macro
    save_macro(macro(label="Ping", command="ping"), macros_path)
    save_macro(
        macro(label="Ping", command="ping -c1", device="/dev/ttyTEST0"), macros_path
    )

    loaded = load_macros(macros_path)
    assert len(loaded) == 2
    assert macro(label="Ping", command="ping") in loaded
    assert (
        macro(label="Ping", command="ping -c1", device="/dev/ttyTEST0") in loaded
    )


def test_remove_macro_deletes_entry(tmp_path):
    macros_path = tmp_path / "macros.yaml"
    macro = __import__("config").Macro
    save_macro(macro(label="Ping", command="ping"), macros_path)
    save_macro(macro(label="Status", command="status"), macros_path)

    remove_macro("Ping", path=macros_path)

    assert load_macros(macros_path) == [macro(label="Status", command="status")]


def test_remove_macro_missing_entry_is_noop(tmp_path):
    macros_path = tmp_path / "macros.yaml"
    macro = __import__("config").Macro
    save_macro(macro(label="Ping", command="ping"), macros_path)

    remove_macro("DoesNotExist", path=macros_path)

    assert load_macros(macros_path) == [macro(label="Ping", command="ping")]


def test_backup_settings_includes_favorites_and_macros(tmp_path):
    favorites_path = tmp_path / "favorites.yaml"
    macros_path = tmp_path / "macros.yaml"
    port_preset = __import__("config").PortPreset
    macro = __import__("config").Macro
    save_favorite(
        port_preset(name="Test", device="/dev/ttyTEST0", baudrate=9600), favorites_path
    )
    save_macro(macro(label="Ping", command="ping"), macros_path)

    content = backup_settings(favorites_path, macros_path)
    data = yaml.safe_load(content)

    assert data["favorites"] == [
        {"name": "Test", "device": "/dev/ttyTEST0", "baudrate": 9600}
    ]
    assert data["macros"] == [
        {"label": "Ping", "command": "ping", "raw": False, "device": None}
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
