"""Configuration loading for RPI-SemiAutomator.

Reads the YAML configuration file describing preset serial ports and
predefined macro commands. The configuration file location can be
overridden with the ``RPI_SEMIAUTOMATOR_CONFIG`` environment variable,
which is primarily used to make the container deployment configurable
via a bind mount.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
DEFAULT_FAVORITES_PATH = Path(__file__).resolve().parent.parent / "data" / "favorites.yaml"
DEFAULT_MACROS_PATH = Path(__file__).resolve().parent.parent / "data" / "macros.yaml"
_favorites_lock = threading.Lock()
_macros_lock = threading.Lock()


@dataclass
class PortPreset:
    """A preconfigured serial port that is offered in the UI."""

    name: str
    device: str
    baudrate: int = 115200


@dataclass
class Macro:
    """A predefined command that can be sent with a single click."""

    label: str
    command: str
    raw: bool = False
    device: Optional[str] = None
    category: Optional[str] = None


@dataclass
class AppConfig:
    ports: List[PortPreset] = field(default_factory=list)
    macros: List[Macro] = field(default_factory=list)


def _config_path() -> Path:
    override = os.environ.get("RPI_SEMIAUTOMATOR_CONFIG")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _favorites_path() -> Path:
    override = os.environ.get("RPI_SEMIAUTOMATOR_FAVORITES")
    return Path(override) if override else DEFAULT_FAVORITES_PATH


def _macros_path() -> Path:
    override = os.environ.get("RPI_SEMIAUTOMATOR_MACROS")
    return Path(override) if override else DEFAULT_MACROS_PATH


def load_config(path: Path | None = None) -> AppConfig:
    """Load the application configuration from ``path`` (or the default)."""
    config_path = path or _config_path()

    if not config_path.exists():
        return AppConfig()

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    ports = [
        PortPreset(
            name=entry.get("name", entry.get("device", "port")),
            device=entry["device"],
            baudrate=int(entry.get("baudrate", 115200)),
        )
        for entry in raw.get("ports", []) or []
    ]

    macros = [
        Macro(
            label=entry["label"],
            command=entry["command"],
            raw=bool(entry.get("raw", False)),
            device=entry.get("device"),
            category=entry.get("category"),
        )
        for entry in raw.get("macros", []) or []
    ]

    return AppConfig(ports=ports, macros=macros)


def load_favorites(path: Path | None = None) -> List[PortPreset]:
    """Load user-saved serial port favorites."""
    favorites_path = path or _favorites_path()
    if not favorites_path.exists():
        return []

    with favorites_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    return [
        PortPreset(
            name=entry.get("name", entry.get("device", "port")),
            device=entry["device"],
            baudrate=int(entry.get("baudrate", 115200)),
        )
        for entry in raw.get("ports", []) or []
    ]


def _write_favorites(favorites: dict[str, PortPreset], favorites_path: Path) -> None:
    favorites_path.parent.mkdir(parents=True, exist_ok=True)
    with favorites_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "ports": [
                    {
                        "name": favorite.name,
                        "device": favorite.device,
                        "baudrate": favorite.baudrate,
                    }
                    for favorite in favorites.values()
                ]
            },
            handle,
            allow_unicode=True,
            sort_keys=False,
        )


def save_favorite(preset: PortPreset, path: Path | None = None) -> None:
    """Add or update one favorite in the writable user configuration."""
    favorites_path = path or _favorites_path()
    with _favorites_lock:
        favorites = {favorite.device: favorite for favorite in load_favorites(favorites_path)}
        favorites[preset.device] = preset
        _write_favorites(favorites, favorites_path)


def remove_favorite(device: str, path: Path | None = None) -> None:
    """Remove one favorite (by device path) from the writable user configuration."""
    favorites_path = path or _favorites_path()
    with _favorites_lock:
        favorites = {favorite.device: favorite for favorite in load_favorites(favorites_path)}
        favorites.pop(device, None)
        _write_favorites(favorites, favorites_path)


def load_macros(path: Path | None = None) -> List[Macro]:
    """Load user-defined macros created from the UI."""
    macros_path = path or _macros_path()
    if not macros_path.exists():
        return []

    with macros_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    return [
        Macro(
            label=entry["label"],
            command=entry["command"],
            raw=bool(entry.get("raw", False)),
            device=entry.get("device"),
            category=entry.get("category"),
        )
        for entry in raw.get("macros", []) or []
    ]


def _macro_key(macro: Macro) -> tuple[str | None, str]:
    return (macro.device, macro.label)


def _write_macros(macros: dict[tuple[str | None, str], Macro], macros_path: Path) -> None:
    macros_path.parent.mkdir(parents=True, exist_ok=True)
    with macros_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "macros": [
                    {
                        "label": macro.label,
                        "command": macro.command,
                        "raw": macro.raw,
                        "device": macro.device,
                        **({"category": macro.category} if macro.category else {}),
                    }
                    for macro in macros.values()
                ]
            },
            handle,
            allow_unicode=True,
            sort_keys=False,
        )


def save_macro(macro: Macro, path: Path | None = None) -> None:
    """Add or update one user-defined macro in the writable user configuration."""
    macros_path = path or _macros_path()
    with _macros_lock:
        macros = {_macro_key(existing): existing for existing in load_macros(macros_path)}
        macros[_macro_key(macro)] = macro
        _write_macros(macros, macros_path)


def remove_macro(label: str, device: str | None = None, path: Path | None = None) -> None:
    """Remove one user-defined macro (by label and optional device) from storage."""
    macros_path = path or _macros_path()
    with _macros_lock:
        macros = {_macro_key(existing): existing for existing in load_macros(macros_path)}
        macros.pop((device, label), None)
        _write_macros(macros, macros_path)


def update_macro(
    old_label: str,
    old_device: str | None,
    macro: Macro,
    path: Path | None = None,
) -> None:
    """Replace an existing macro in place (keeping its position) with ``macro``."""
    macros_path = path or _macros_path()
    with _macros_lock:
        existing = load_macros(macros_path)
        old_key = (old_device, old_label)
        new_key = _macro_key(macro)
        result: dict[tuple[str | None, str], Macro] = {}
        replaced = False
        for item in existing:
            key = _macro_key(item)
            if key == old_key:
                result[new_key] = macro
                replaced = True
            elif key != new_key:
                result[key] = item
        if not replaced:
            result[new_key] = macro
        _write_macros(result, macros_path)


def backup_settings(
    favorites_path: Path | None = None, macros_path: Path | None = None
) -> bytes:
    """Return a single YAML document bundling the current favorites and macros.

    Used by the Settings page to let the user download a backup of all
    user-editable settings (favorites and individual macros).
    """
    favorites = load_favorites(favorites_path)
    macros = load_macros(macros_path)
    payload = {
        "favorites": [
            {"name": favorite.name, "device": favorite.device, "baudrate": favorite.baudrate}
            for favorite in favorites
        ],
        "macros": [
            {
                "label": macro.label,
                "command": macro.command,
                "raw": macro.raw,
                "device": macro.device,
                **({"category": macro.category} if macro.category else {}),
            }
            for macro in macros
        ],
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).encode("utf-8")
