"""RPI-SemiAutomator - NiceGUI web UI for serial interfaces.

Provides a dropdown for available serial ports, connecting/disconnecting,
sending arbitrary input and triggering predefined "macro" commands.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

from nicegui import app, ui

from config import Macro, PortPreset, load_config, load_favorites, save_favorite
from serial_manager import SerialConnection, SerialManager

config = load_config()
favorites = load_favorites()
manager = SerialManager()


def _known_devices() -> Dict[str, int]:
    """Merge configured/favorite ports with currently detected devices.

    Returns a mapping of device path -> default baudrate.
    """
    devices: Dict[str, int] = {}
    for preset in config.ports:
        devices[preset.device] = preset.baudrate
    for favorite in favorites:
        devices[favorite.device] = favorite.baudrate
    for device in manager.list_available_devices():
        devices.setdefault(device, 115200)
    for device, connection in manager.all().items():
        devices.setdefault(device, connection.baudrate)
    return devices


def _preset_name(device: str) -> str:
    for favorite in favorites:
        if favorite.device == device:
            return favorite.name
    for preset in config.ports:
        if preset.device == device:
            return preset.name
    return device


def _device_options() -> Dict[str, str]:
    return {
        device: f"{_preset_name(device)} ({device})"
        for device in _known_devices()
    }


def build_port_card(device: str, baudrate: int):
    connection: SerialConnection = manager.get_or_create(device, baudrate)

    with ui.card().classes("w-full"):
        with ui.row().classes("items-center w-full justify-between"):
            ui.label(_device_options()[device]).classes("text-lg font-bold")
            status_badge = ui.badge("getrennt", color="red")

            def download_log() -> None:
                filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(device).name) or "serial"
                content = "\n".join(connection.lines).encode("utf-8")
                ui.download(content, f"{filename}.log", media_type="text/plain; charset=utf-8")

            ui.button("Log herunterladen", on_click=download_log, icon="download").props(
                "outline"
            )

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

            def save_as_favorite() -> None:
                global favorites
                try:
                    favorite = PortPreset(
                        name=_preset_name(device),
                        device=device,
                        baudrate=int(baud_input.value or connection.baudrate),
                    )
                    save_favorite(favorite)
                    favorites = load_favorites()
                    ui.notify(f"{device} als Favorit gespeichert", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Favorit konnte nicht gespeichert werden: {exc}", type="negative")

            ui.button("Als Favorit speichern", on_click=save_as_favorite, icon="star").props(
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
                    log.push(f"TX → {text}")
                    command_input.value = ""
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Senden fehlgeschlagen: {exc}", type="negative")

            command_input.on("keydown.enter", lambda: send_command())
            ui.button("Senden", on_click=send_command, icon="send")

        macros = [
            macro
            for macro in config.macros
            if macro.device is None or macro.device == device
        ]
        if macros:
            ui.label("Makros").classes("text-sm text-grey-6")
            with ui.row().classes("w-full flex-wrap"):
                for macro in macros:
                    def make_handler(m: Macro):
                        def handler() -> None:
                            try:
                                connection.send(m.command, raw=m.raw)
                                log.push(f"TX → {m.command}")
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
            if connection.error:
                status_badge.set_text("Fehler")
                status_badge.props("color=orange")
            else:
                status_badge.set_text("verbunden" if connection.is_open else "getrennt")
                status_badge.props(f"color={'green' if connection.is_open else 'red'}")
            new_lines = connection.lines[last_rendered_count["n"] :]
            for line in new_lines:
                log.push(f"RX ← {line}")
            last_rendered_count["n"] = len(connection.lines)

    return ui.timer(0.5, refresh)


@ui.page("/")
def index() -> None:
    ui.label("RPI-SemiAutomator - Serielle Schnittstellen").classes(
        "text-2xl font-bold mb-4"
    )
    ui.label(
        "Angezeigt werden serielle Geräte, nicht alle USB-Geräte aus lsusb."
    ).classes("text-sm text-grey-6")
    ports_container = ui.column().classes("w-full gap-4")
    page_state = {"devices": [], "active_device": None, "timers": []}

    def render_terminals(devices: list[str], active_device: str | None = None) -> None:
        for timer in page_state["timers"]:
            timer.cancel()
        page_state["timers"] = []
        ports_container.clear()
        page_state["devices"] = devices
        if active_device not in devices:
            active_device = devices[0] if devices else None
        page_state["active_device"] = active_device

        with ports_container:
            if not devices:
                ui.label("Keine seriellen Schnittstellen gefunden.")
                return

            with ui.tabs().classes("w-full") as tabs:
                for device in devices:
                    ui.tab(device, label=f"{_preset_name(device)} ({device})")

            tabs.value = active_device
            tabs.on_value_change(
                lambda event: page_state.update(active_device=event.value)
            )
            with ui.tab_panels(tabs, value=active_device).classes("w-full"):
                for device in devices:
                    with ui.tab_panel(device):
                        baudrate = _known_devices().get(device, 115200)
                        page_state["timers"].append(build_port_card(device, baudrate))

    def rescan() -> None:
        devices = list(_device_options())
        if devices != page_state["devices"]:
            render_terminals(devices, page_state["active_device"])

    render_terminals(list(_device_options()))
    ui.timer(5.0, rescan)


@app.on_shutdown
def _cleanup() -> None:
    manager.disconnect_all()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="RPI-SemiAutomator", port=8080, host="0.0.0.0", reload=False)
