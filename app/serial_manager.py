"""Serial port management for RPI-SemiAutomator.

Provides a small, thread based abstraction around ``pyserial`` so that the
NiceGUI front end can open, read from, write to and close several serial
interfaces concurrently without blocking the UI event loop.

Each :class:`SerialConnection` owns a background thread that continuously
reads available bytes from the port and appends decoded lines to an
in-memory buffer. The UI polls this buffer periodically (via a NiceGUI
timer) instead of relying on callbacks, which keeps the threading model
simple and avoids cross-thread UI updates.
"""
from __future__ import annotations

import glob
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import serial
from serial import SerialException


@dataclass
class SerialConnection:
    """Represents a single open (or closed) serial port."""

    device: str
    baudrate: int = 115200
    _serial: Optional[serial.Serial] = field(default=None, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    lines: List[str] = field(default_factory=list)
    max_lines: int = 1000
    error: Optional[str] = None
    on_data: Optional[Callable[[str], None]] = field(default=None, repr=False)

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self) -> None:
        """Open the serial port and start the background reader thread."""
        if self.is_open:
            return

        self.error = None
        try:
            self._serial = serial.Serial(
                port=self.device,
                baudrate=self.baudrate,
                timeout=0.2,
            )
        except SerialException as exc:
            self.error = str(exc)
            self._serial = None
            raise

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        """Stop the reader thread and close the serial port."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                finally:
                    self._serial = None

    def send(self, command: str, raw: bool = False) -> None:
        """Write ``command`` to the serial port.

        A trailing newline is appended unless ``raw`` is set, mirroring the
        behaviour of a typical serial terminal.
        """
        if not self.is_open or self._serial is None:
            raise SerialException(f"Port {self.device} is not open")

        payload = command if raw else f"{command}\n"
        with self._lock:
            self._serial.write(payload.encode("utf-8", errors="replace"))

    def _read_loop(self) -> None:
        buffer = ""
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    ser = self._serial
                if ser is None:
                    break
                waiting = ser.in_waiting
                chunk = ser.read(waiting if waiting else 1)
            except SerialException as exc:
                self.error = str(exc)
                break
            except OSError as exc:
                self.error = str(exc)
                break

            if not chunk:
                continue

            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                self._append_line(line.rstrip("\r"))

        if buffer:
            self._append_line(buffer.rstrip("\r"))

    def _append_line(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > self.max_lines:
            del self.lines[: len(self.lines) - self.max_lines]
        if self.on_data is not None:
            self.on_data(line)


class SerialManager:
    """Keeps track of all known serial connections."""

    def __init__(self) -> None:
        self._connections: Dict[str, SerialConnection] = {}

    def list_available_devices(self) -> List[str]:
        """Return serial devices currently present on the system."""
        patterns = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/serial/by-id/*")
        devices: List[str] = []
        for pattern in patterns:
            devices.extend(sorted(glob.glob(pattern)))
        return devices

    def get_or_create(self, device: str, baudrate: int = 115200) -> SerialConnection:
        conn = self._connections.get(device)
        if conn is None:
            conn = SerialConnection(device=device, baudrate=baudrate)
            self._connections[device] = conn
        return conn

    def all(self) -> Dict[str, SerialConnection]:
        return self._connections

    def remove(self, device: str) -> None:
        conn = self._connections.pop(device, None)
        if conn is not None:
            conn.disconnect()

    def disconnect_all(self) -> None:
        for conn in self._connections.values():
            conn.disconnect()
