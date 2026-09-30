"""RPI-SemiAutomator - NiceGUI web UI for multiple serial interfaces.

Displays one panel per serial port, allows connecting/disconnecting,
sending arbitrary input and triggering predefined "macro" commands.
"""
from __future__ import annotations

from typing import Dict

from nicegui import app, ui

from config import Macro, PortPreset, load_config
from serial_manager import SerialConnection, SerialManager

config = load_config()
manager = SerialManager()

# Track which port "cards" have already been rendered in the UI so the
# periodic refresh can pick up newly discovered devices.
rendered_devices: Dict[str, ui.element] = {}


def _known_devices() -> Dict[str, int]:
    """Merge configured presets with auto-detected devices.

    Returns a mapping of device path -> default baudrate.
    """
    devices: Dict[str, int] = {}
    for preset in config.ports:
        devices[preset.device] = preset.baudrate
    for device in manager.list_available_devices():
        devices.setdefault(device, 115200)
    return devices


def _preset_name(device: str) -> str:
    for preset in config.ports:
        if preset.device == device:
            return preset.name
    return device


def build_port_card(device: str, baudrate: int) -> None:
    connection: SerialConnection = manager.get_or_create(device, baudrate)

    with ui.card().classes("w-full") as card:
        with ui.row().classes("items-center w-full justify-between"):
            ui.label(f"{_preset_name(device)} ({device})").classes("text-lg font-bold")
            status_badge = ui.badge("getrennt", color="red")

        with ui.row().classes("items-center w-full"):
            baud_input = ui.number(
                label="Baudrate", value=connection.baudrate, min=110, max=4000000
            ).classes("w-32")

            def do_connect() -> None:
                connection.baudrate = int(baud_input.value or connection.baudrate)
                try:
                    connection.connect()
                    ui.notify(f"{device} verbunden", type="positive")
                except Exception as exc:  # noqa: BLE001 - surfaced to the user
                    ui.notify(f"Verbindung fehlgeschlagen: {exc}", type="negative")

            def do_disconnect() -> None:
                connection.disconnect()
                ui.notify(f"{device} getrennt", type="warning")

            ui.button("Verbinden", on_click=do_connect, icon="power")
            ui.button("Trennen", on_click=do_disconnect, icon="power_off").props(
                "outline"
            )

        log = ui.log(max_lines=500).classes("w-full h-48")
        last_rendered_count = {"n": 0}

        with ui.row().classes("w-full items-center"):
            command_input = ui.input(label="Befehl senden").classes("flex-grow")

            def send_command() -> None:
                text = command_input.value
                if not text:
                    return
                try:
                    connection.send(text)
                    command_input.value = ""
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Senden fehlgeschlagen: {exc}", type="negative")

            command_input.on("keydown.enter", lambda: send_command())
            ui.button("Senden", on_click=send_command, icon="send")

        if config.macros:
            ui.label("Makros").classes("text-sm text-grey-6")
            with ui.row().classes("w-full flex-wrap"):
                for macro in config.macros:
                    def make_handler(m: Macro):
                        def handler() -> None:
                            try:
                                connection.send(m.command, raw=m.raw)
                                ui.notify(f"Makro '{m.label}' gesendet")
                            except Exception as exc:  # noqa: BLE001
                                ui.notify(
                                    f"Makro fehlgeschlagen: {exc}", type="negative"
                                )

                        return handler

                    ui.button(macro.label, on_click=make_handler(macro)).props(
                        "outline"
                    )

        def refresh() -> None:
            status_badge.set_text("verbunden" if connection.is_open else "getrennt")
            status_badge.props(
                f"color={'green' if connection.is_open else 'red'}"
            )
            new_lines = connection.lines[last_rendered_count["n"] :]
            for line in new_lines:
                log.push(line)
            last_rendered_count["n"] = len(connection.lines)

        ui.timer(0.5, refresh)

    rendered_devices[device] = card


def refresh_port_list() -> None:
    for device, baudrate in _known_devices().items():
        if device not in rendered_devices:
            build_port_card(device, baudrate)


@ui.page("/")
def index() -> None:
    ui.label("RPI-SemiAutomator - Serielle Schnittstellen").classes(
        "text-2xl font-bold mb-4"
    )
    ports_container = ui.column().classes("w-full gap-4")

    with ports_container:
        refresh_port_list()

    def rescan() -> None:
        with ports_container:
            refresh_port_list()

    ui.timer(5.0, rescan)


@app.on_shutdown
def _cleanup() -> None:
    manager.disconnect_all()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="RPI-SemiAutomator", port=8080, host="0.0.0.0", reload=False)
