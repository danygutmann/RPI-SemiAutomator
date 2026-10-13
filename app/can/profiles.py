"""Persistent, validated CAN profiles and safely named DBC uploads."""

from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path

import cantools
import yaml
from cantools.database.errors import Error as DBCError
from fastapi import HTTPException

from .models import CANProfile

SUPPORTED_INTERFACES = {"socketcan", "pcan", "vector", "kvaser", "virtual"}
MAX_DBC_SIZE = 1024 * 1024
_lock = threading.RLock()
_PROFILE_FIELDS = {
    "name",
    "interface",
    "channel",
    "bitrate",
    "fd",
    "data_bitrate",
    "listen_only",
    "dbc_id",
}


def _profiles_path() -> Path:
    default = Path(__file__).resolve().parents[2] / "data" / "can_profiles.yaml"
    return Path(os.environ.get("RPI_SEMIAUTOMATOR_CAN_PROFILES", default))


def _dbc_dir() -> Path:
    default = Path(__file__).resolve().parents[2] / "data" / "dbc"
    return Path(os.environ.get("RPI_SEMIAUTOMATOR_DBC_DIR", default))


def _read_profiles() -> list[CANProfile]:
    path = _profiles_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles", []), list):
        raise ValueError("Invalid CAN profile document")
    profiles = []
    for entry in raw.get("profiles", []):
        profiles.append(CANProfile(**entry))
    return profiles


def _write_profiles(profiles: list[CANProfile]) -> None:
    path = _profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {"profiles": [profile.to_dict() for profile in profiles]},
            handle,
            allow_unicode=True,
            sort_keys=False,
        )
    temporary.replace(path)


def list_profiles() -> list[CANProfile]:
    with _lock:
        return _read_profiles()


def get_profile(profile_id: str) -> CANProfile:
    for profile in list_profiles():
        if profile.id == profile_id:
            return profile
    raise HTTPException(status_code=404, detail="CAN profile not found")


def _validated_profile(payload: dict, profile_id: str | None = None) -> CANProfile:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="A JSON object is required")
    unknown = set(payload) - _PROFILE_FIELDS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown fields: {', '.join(sorted(unknown))}")

    name = payload.get("name")
    interface = payload.get("interface", "socketcan")
    channel = payload.get("channel", "can0")
    bitrate = payload.get("bitrate", 500_000)
    data_bitrate = payload.get("data_bitrate")
    fd = payload.get("fd", False)
    listen_only = payload.get("listen_only", True)
    dbc_id = payload.get("dbc_id")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise HTTPException(status_code=400, detail="'name' must be 1..80 characters")
    if interface not in SUPPORTED_INTERFACES:
        raise HTTPException(status_code=400, detail="Unsupported CAN interface")
    if not isinstance(channel, (str, int)) or not str(channel).strip() or len(str(channel)) > 128:
        raise HTTPException(status_code=400, detail="Invalid CAN channel")
    if isinstance(bitrate, bool) or not isinstance(bitrate, int) or not 10_000 <= bitrate <= 10_000_000:
        raise HTTPException(status_code=400, detail="'bitrate' must be 10000..10000000")
    if data_bitrate is not None and (
        isinstance(data_bitrate, bool)
        or not isinstance(data_bitrate, int)
        or not 10_000 <= data_bitrate <= 20_000_000
    ):
        raise HTTPException(status_code=400, detail="Invalid CAN FD data bitrate")
    if not isinstance(fd, bool) or not isinstance(listen_only, bool):
        raise HTTPException(status_code=400, detail="'fd' and 'listen_only' must be booleans")
    if fd and data_bitrate is None:
        raise HTTPException(status_code=400, detail="CAN FD requires 'data_bitrate'")
    if not fd and data_bitrate is not None:
        raise HTTPException(status_code=400, detail="'data_bitrate' requires CAN FD")
    if dbc_id is not None and (not isinstance(dbc_id, str) or not re.fullmatch(r"[0-9a-f]{32}", dbc_id)):
        raise HTTPException(status_code=400, detail="Invalid DBC identifier")
    if dbc_id is not None and not dbc_path(dbc_id).is_file():
        raise HTTPException(status_code=404, detail="DBC file not found")
    return CANProfile(
        id=profile_id or uuid.uuid4().hex,
        name=name.strip(),
        interface=interface,
        channel=str(channel).strip(),
        bitrate=bitrate,
        fd=fd,
        data_bitrate=data_bitrate,
        listen_only=listen_only,
        dbc_id=dbc_id,
    )


def save_profile(payload: dict, profile_id: str | None = None) -> CANProfile:
    with _lock:
        profiles = _read_profiles()
        profile = _validated_profile(payload, profile_id)
        if any(item.name.casefold() == profile.name.casefold() and item.id != profile.id for item in profiles):
            raise HTTPException(status_code=409, detail="CAN profile name already exists")
        if profile_id and not any(item.id == profile_id for item in profiles):
            raise HTTPException(status_code=404, detail="CAN profile not found")
        if profile_id:
            profiles = [profile if item.id == profile_id else item for item in profiles]
        else:
            profiles.append(profile)
        _write_profiles(profiles)
        return profile


def delete_profile(profile_id: str) -> None:
    with _lock:
        profiles = _read_profiles()
        remaining = [profile for profile in profiles if profile.id != profile_id]
        if len(remaining) == len(profiles):
            raise HTTPException(status_code=404, detail="CAN profile not found")
        _write_profiles(remaining)


def dbc_path(dbc_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", dbc_id):
        raise HTTPException(status_code=404, detail="DBC file not found")
    return _dbc_dir() / f"{dbc_id}.dbc"


def load_dbc(dbc_id: str):
    try:
        return cantools.database.load_file(dbc_path(dbc_id), strict=True)
    except (OSError, DBCError) as exc:
        raise HTTPException(status_code=400, detail=f"Unable to load DBC: {exc}") from exc


def list_dbcs() -> list[dict]:
    directory = _dbc_dir()
    if not directory.exists():
        return []
    return [
        {"id": path.stem, "name": path.stem}
        for path in sorted(directory.glob("*.dbc"))
        if re.fullmatch(r"[0-9a-f]{32}", path.stem)
    ]


def save_dbc(filename: str, content: bytes) -> dict:
    if not content or len(content) > MAX_DBC_SIZE:
        raise HTTPException(status_code=413, detail="DBC must be between 1 byte and 1 MiB")
    try:
        decoded = content.decode("utf-8-sig")
        database = cantools.database.load_string(decoded, database_format="dbc", strict=True)
        if not database.messages:
            raise ValueError("DBC has no messages")
    except (UnicodeDecodeError, DBCError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid DBC file: {exc}") from exc
    dbc_id = uuid.uuid4().hex
    directory = _dbc_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = dbc_path(dbc_id)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(decoded, encoding="utf-8")
    temporary.replace(destination)
    return {"id": dbc_id, "name": Path(filename).name[:128] or f"{dbc_id}.dbc"}
