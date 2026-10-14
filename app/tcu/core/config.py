"""Environment-backed D-Bus integration settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dbus_next.validators import is_object_path_valid


def _positive_float(name: str, default: float, maximum: float) -> float:
    legacy_name = name.replace("DBUS_", "TCU_", 1)
    raw = os.environ.get(name, os.environ.get(legacy_name))
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0 < value <= maximum:
        raise ValueError(f"{name} must be greater than 0 and at most {maximum}")
    return value


@dataclass(frozen=True)
class TCUConfig:
    enabled: bool
    bus_address: str | None
    timeout_seconds: float
    known_object_paths: tuple[str, ...]

    @property
    def transport_label(self) -> str:
        if self.bus_address is None:
            return "not configured"
        if self.bus_address.startswith("unix:"):
            return "local D-Bus Unix socket"
        return "unsupported D-Bus transport"


def load_config() -> TCUConfig:
    """Load settings without exposing credentials or making a connection."""
    enabled_value = os.environ.get("DBUS_ENABLED", os.environ.get("TCU_ENABLED", "false"))
    raw_enabled = enabled_value.strip().lower()
    if raw_enabled not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError("DBUS_ENABLED must be a boolean value")
    enabled = raw_enabled in {"1", "true", "yes", "on"}
    address = os.environ.get(
        "DBUS_ADDRESS", os.environ.get("TCU_DBUS_ADDRESS", "")
    ).strip() or None
    paths = tuple(
        dict.fromkeys(
            path.strip()
            for path in os.environ.get(
                "DBUS_OBJECT_PATHS", os.environ.get("TCU_OBJECT_PATHS", "/")
            ).split(",")
            if path.strip()
        )
    )
    if any(not is_object_path_valid(path) for path in paths):
        raise ValueError("DBUS_OBJECT_PATHS must contain valid D-Bus object paths")
    if address is not None:
        if address.startswith("unix:path="):
            socket_path = address.removeprefix("unix:path=")
            if not socket_path.startswith("/") or "\x00" in socket_path:
                raise ValueError("DBUS_ADDRESS requires an absolute Unix socket path")
        else:
            raise ValueError(
                "DBUS_ADDRESS must use an absolute local Unix socket in unix:path=... form; "
                "configure any protected remote proxy separately"
            )
    return TCUConfig(
        enabled=enabled,
        bus_address=address,
        timeout_seconds=_positive_float("DBUS_TIMEOUT_SECONDS", 10.0, 60.0),
        known_object_paths=paths,
    )
