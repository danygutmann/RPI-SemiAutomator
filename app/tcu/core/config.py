"""Environment-backed TCU settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_float(name: str, default: float, maximum: float) -> float:
    raw = os.environ.get(name)
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
        return "loopback D-Bus tunnel"


def load_config() -> TCUConfig:
    """Load settings without exposing credentials or making a connection."""
    raw_enabled = os.environ.get("TCU_ENABLED", "false").strip().lower()
    if raw_enabled not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError("TCU_ENABLED must be a boolean value")
    enabled = raw_enabled in {"1", "true", "yes", "on"}
    address = os.environ.get("TCU_DBUS_ADDRESS", "").strip() or None
    paths = tuple(
        dict.fromkeys(
            path.strip()
            for path in os.environ.get("TCU_OBJECT_PATHS", "/").split(",")
            if path.strip()
        )
    )
    if any(not path.startswith("/") or "\x00" in path for path in paths):
        raise ValueError("TCU_OBJECT_PATHS must contain absolute D-Bus object paths")
    if address is not None:
        if address.startswith("unix:path="):
            socket_path = address.removeprefix("unix:path=")
            if not socket_path or "\x00" in socket_path:
                raise ValueError("TCU_DBUS_ADDRESS has an invalid Unix socket path")
        elif address.startswith("tcp:"):
            parts = dict(
                part.split("=", 1)
                for part in address.removeprefix("tcp:").split(",")
                if "=" in part
            )
            host = parts.get("host", "")
            if host not in {"127.0.0.1", "::1", "localhost"} or "port" not in parts:
                raise ValueError(
                    "TCP D-Bus addresses must target loopback; use an SSH tunnel"
                )
            try:
                port = int(parts["port"])
            except ValueError as exc:
                raise ValueError("TCU_DBUS_ADDRESS has an invalid port") from exc
            if not 1 <= port <= 65535:
                raise ValueError("TCU_DBUS_ADDRESS port must be between 1 and 65535")
        else:
            raise ValueError(
                "TCU_DBUS_ADDRESS must be unix:path=... or a loopback tcp: address"
            )
    return TCUConfig(
        enabled=enabled,
        bus_address=address,
        timeout_seconds=_positive_float("TCU_TIMEOUT_SECONDS", 10.0, 60.0),
        known_object_paths=paths,
    )
