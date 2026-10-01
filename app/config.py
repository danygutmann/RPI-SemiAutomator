"""Configuration loading for RPI-SemiAutomator.

Reads the YAML configuration file describing preset serial ports and
predefined macro commands. The configuration file location can be
overridden with the ``RPI_SEMIAUTOMATOR_CONFIG`` environment variable,
which is primarily used to make the container deployment configurable
via a bind mount.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


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


@dataclass
class AppConfig:
    ports: List[PortPreset] = field(default_factory=list)
    macros: List[Macro] = field(default_factory=list)


def _config_path() -> Path:
    override = os.environ.get("RPI_SEMIAUTOMATOR_CONFIG")
    return Path(override) if override else DEFAULT_CONFIG_PATH


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
        )
        for entry in raw.get("macros", []) or []
    ]

    return AppConfig(ports=ports, macros=macros)
