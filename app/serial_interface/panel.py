"""UI for an individual serial terminal panel."""
from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

from nicegui import ui

from panels import safe_timer
from serial_manager import SerialConnection


def build_port_card(
    device: str,
    baudrate: int,
    connection: SerialConnection,
    label: str,
):
    """Build the console card for one device. Does not open the connection."""
    if not connection.is_open:
        connection.baudrate = baudrate

    with ui.column().classes("w-full h-full gap-2"):
        with ui.row().classes("items-center w-full justify-between shrink-0"):
            ui.label(label).classes("text-lg font-bold")
            status_badge = ui.badge("Disconnected", color="red")

            def download_log() -> None:
                filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(device).name) or "serial"
                content = "\n".join(connection.lines).encode("utf-8")
                ui.download(content, f"{filename}.log", media_type="text/plain; charset=utf-8")

            ui.button("Log herunterladen", on_click=download_log, icon="download").props(
                "outline"
            )

        with ui.row().classes("items-center w-full shrink-0"):
            def toggle_connection() -> None:
                try:
                    if connection.is_open:
                        connection.disconnect()
                        ui.notify(f"{device} disconnected", type="warning")
                    else:
                        connection.connect()
                        ui.notify(f"{device} connected", type="positive")
                except Exception as exc:  # noqa: BLE001 - surfaced to the user
                    ui.notify(f"Connection failed: {exc}", type="negative")

            connection_button = ui.button(
                "Disconnect" if connection.is_open else "Connect",
                on_click=toggle_connection,
                icon="power",
            )

        terminal = ui.xterm(
            {"cursorBlink": True, "convertEol": False, "scrollback": 5000}
        ).classes("w-full").style(
            "height: 400px; min-height: 250px; resize: vertical; "
            "overflow: auto; flex: 0 0 auto;"
        )
        incoming: deque[str] = deque()

        def restore_terminal_height() -> None:
            key = json.dumps(f"rpi-semiautomator-terminal-height:{device}")
            ui.run_javascript(
                f"""
                const component = getElement({terminal.id});
                const element = component && component.$el;
                if (element) {{
                    const key = {key};
                    const savedHeight = Number(localStorage.getItem(key));
                    if (savedHeight >= 250) element.style.height = `${{savedHeight}}px`;
                    let saveTimeout;
                    const persistHeight = () => {{
                        const height = Math.round(element.getBoundingClientRect().height);
                        if (height >= 250) localStorage.setItem(key, String(height));
                        runMethod({terminal.id}, "fit", []);
                    }};
                    new ResizeObserver(() => {{
                        clearTimeout(saveTimeout);
                        saveTimeout = setTimeout(persistHeight, 150);
                    }}).observe(element);
                    element.addEventListener("pointerup", persistHeight);
                    element.addEventListener("mouseup", persistHeight);
                }}
                """
            )

        safe_timer(0.2, restore_terminal_height, "Serial", once=True)

        def on_serial(direction: str, text: str) -> None:
            if direction == "rx":
                incoming.append(text)

        connection.subscribe(on_serial)
        ui.context.client.on_disconnect(lambda: connection.unsubscribe(on_serial))
        if connection.lines:
            terminal.write("\r\n".join(connection.lines) + "\r\n")

        def on_terminal_data(e) -> None:
            try:
                connection.send(e.data, raw=True)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Send failed: {exc}", type="negative")

        terminal.on_data(on_terminal_data)
        with ui.row().classes("w-full justify-end shrink-0"):

            def clear_display() -> None:
                terminal.run_terminal_method("clear")
                connection.lines.clear()
                ui.notify("Terminal cleared", type="info")

            ui.button("Clear terminal", on_click=clear_display, icon="delete_sweep").props(
                "outline dense"
            )

        def refresh() -> None:
            if connection.error:
                status_badge.set_text("Error")
                status_badge.props("color=orange")
            else:
                status_badge.set_text("Connected" if connection.is_open else "Disconnected")
                status_badge.props(f"color={'green' if connection.is_open else 'red'}")
            connection_button.set_text("Disconnect" if connection.is_open else "Connect")
            if terminal.is_deleted:
                connection.unsubscribe(on_serial)
                return
            chunks = []
            while incoming:
                chunks.append(incoming.popleft())
            if chunks:
                terminal.write(re.sub(r"(?<!\r)\n", "\r\n", "".join(chunks)))

    return safe_timer(0.1, refresh, "Serial")
