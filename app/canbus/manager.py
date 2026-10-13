"""Single-connection CAN manager with bounded, UI-independent reception."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import can

from .backend import open_bus
from .models import CANFrame, CANProfile

FRAME_BUFFER_SIZE = 2000


@dataclass(frozen=True)
class CANStatus:
    connected: bool
    profile_id: str | None
    error: str | None
    dropped_frames: int
    transmit_enabled: bool
    mode: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class CANManager:
    def __init__(self, bus_factory: Callable = open_bus, max_frames: int = FRAME_BUFFER_SIZE):
        self._bus_factory = bus_factory
        self._frames: deque[CANFrame] = deque(maxlen=max_frames)
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._bus = None
        self._profile: CANProfile | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._error: str | None = None
        self._dropped = 0
        self._transmit_enabled = False
        self._sequence = 0

    def status(self) -> CANStatus:
        with self._lock:
            return CANStatus(
                connected=self._bus is not None,
                profile_id=self._profile.id if self._profile else None,
                error=self._error,
                dropped_frames=self._dropped,
                transmit_enabled=self._transmit_enabled,
                mode="transmit-enabled" if self._transmit_enabled else "transmit-disabled",
            )

    @property
    def profile(self) -> CANProfile | None:
        with self._lock:
            return self._profile

    @property
    def position(self) -> int:
        with self._lock:
            return self._sequence

    def frames(self, after: int = 0, limit: int = 200) -> tuple[list[dict], int, int]:
        with self._lock:
            total = self._sequence
            retained_start = total - len(self._frames)
            start = max(after, retained_start)
            selected = list(self._frames)[start - retained_start :]
            batch = selected[:limit]
            next_cursor = min(total, start + len(batch))
            return [frame.to_dict() for frame in batch], next_cursor, max(0, retained_start - after)

    def connect(self, profile: CANProfile) -> dict:
        with self._lifecycle_lock:
            self._disconnect()
            try:
                bus = self._bus_factory(profile)
            except Exception as exc:  # python-can uses backend-specific exceptions
                with self._lock:
                    self._error = str(exc)
                raise
            with self._lock:
                self._bus = bus
                self._profile = profile
                self._error = None
                self._transmit_enabled = False
                self._stop.clear()
                self._thread = threading.Thread(target=self._receive_loop, daemon=True)
                self._thread.start()
        return self.status().to_dict()

    def disconnect(self) -> None:
        with self._lifecycle_lock:
            self._disconnect()

    def _disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            bus, thread = self._bus, self._thread
            self._bus = None
            self._profile = None
            self._thread = None
            self._transmit_enabled = False
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
        if bus:
            try:
                bus.shutdown()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._error = str(exc)

    def set_transmit_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled and self._bus is None:
                raise ValueError("Connect a CAN profile before enabling transmission")
            if enabled and self._profile and self._profile.listen_only:
                raise ValueError("Save and reconnect with listen_only=false before transmitting")
            self._transmit_enabled = enabled

    def send(self, arbitration_id: int, data: bytes, is_extended_id: bool = False, is_fd: bool = False) -> dict:
        with self._lock:
            bus = self._bus
            profile = self._profile
            if bus is None:
                raise ValueError("CAN bus is not connected")
            if not self._transmit_enabled:
                raise PermissionError("CAN transmission is disabled")
            if profile is None or is_fd != profile.fd:
                raise ValueError("Frame format does not match the connected profile")
            if not isinstance(arbitration_id, int) or not 0 <= arbitration_id <= (
                0x1FFFFFFF if is_extended_id else 0x7FF
            ):
                raise ValueError("Invalid CAN arbitration ID")
            if len(data) > (64 if is_fd else 8):
                raise ValueError("CAN frame payload exceeds the selected format")
            message = can.Message(
                arbitration_id=arbitration_id,
                data=data,
                is_extended_id=is_extended_id,
                is_fd=is_fd,
            )
            bus.send(message)
            frame = CANFrame.from_message(message).to_dict()
            self._append(frame)
        return {"ok": True, "status": "sent", "frame": frame}

    def _append(self, payload: dict) -> None:
        if len(self._frames) == self._frames.maxlen:
            self._dropped += 1
        self._frames.append(CANFrame(**payload))
        self._sequence += 1

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                bus = self._bus
            if bus is None:
                return
            try:
                message = bus.recv(timeout=0.2)
            except (can.CanError, OSError) as exc:
                with self._lock:
                    self._error = str(exc)
                    self._transmit_enabled = False
                    self._bus = None
                try:
                    bus.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                return
            if message is not None:
                with self._lock:
                    self._append(CANFrame.from_message(message).to_dict())
            else:
                time.sleep(0.01)

    def shutdown(self) -> None:
        self.disconnect()
