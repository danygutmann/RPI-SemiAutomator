"""DBC encode/decode helpers."""

from __future__ import annotations

import cantools
from fastapi import HTTPException

from .models import CANProfile
from .profiles import load_dbc


def message_preview(profile: CANProfile, message_name: str, signals: dict) -> dict:
    if not profile.dbc_id:
        raise HTTPException(status_code=400, detail="The selected profile has no DBC file")
    if not isinstance(signals, dict):
        raise HTTPException(status_code=400, detail="'signals' must be an object")
    database = load_dbc(profile.dbc_id)
    try:
        definition = database.get_message_by_name(message_name)
        payload = definition.encode(signals, strict=True)
    except (KeyError, ValueError, TypeError, cantools.database.errors.EncodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid DBC message or signal value: {exc}") from exc
    if len(payload) > (64 if profile.fd else 8):
        raise HTTPException(status_code=400, detail="DBC message does not fit the CAN profile")
    return {
        "message": definition.name,
        "arbitration_id": definition.frame_id,
        "is_extended_id": definition.is_extended_frame,
        "is_fd": profile.fd,
        "dlc": len(payload),
        "data": payload.hex(" ").upper(),
        "signals": signals,
    }


def list_messages(dbc_id: str) -> list[dict]:
    database = load_dbc(dbc_id)
    return [
        {
            "name": message.name,
            "arbitration_id": message.frame_id,
            "is_extended_id": message.is_extended_frame,
            "length": message.length,
            "signals": [
                {
                    "name": signal.name,
                    "minimum": signal.minimum,
                    "maximum": signal.maximum,
                    "unit": signal.unit,
                }
                for signal in message.signals
            ],
        }
        for message in database.messages
    ]
