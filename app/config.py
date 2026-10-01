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
_favorites_lock = threading.Lock()


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
