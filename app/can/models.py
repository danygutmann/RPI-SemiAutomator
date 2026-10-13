"""Common data models for CAN frames and connection profiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class CANProfile:
    id: str
    name: str
    interface: str = "socketcan"
    channel: str = "can0"
    bitrate: int = 500_000
    fd: bool = False
    data_bitrate: int | None = None
    listen_only: bool = True
    dbc_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CANFrame:
    timestamp: str
    arbitration_id: int
    is_extended_id: bool
    is_fd: bool
    is_remote_frame: bool
    is_error_frame: bool
    dlc: int
    data: str
    channel: str | None = None

    @classmethod
    def from_message(cls, message) -> "CANFrame":
        timestamp = message.timestamp
        if not timestamp:
            timestamp = datetime.now(timezone.utc).timestamp()
        return cls(
            timestamp=datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            arbitration_id=message.arbitration_id,
            is_extended_id=message.is_extended_id,
            is_fd=message.is_fd,
            is_remote_frame=message.is_remote_frame,
            is_error_frame=message.is_error_frame,
            dlc=message.dlc,
            data=bytes(message.data).hex(" ").upper(),
            channel=str(message.channel) if message.channel is not None else None,
        )

    def to_dict(self) -> dict:
        return asdict(self)
