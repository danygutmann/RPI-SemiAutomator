"""RPI-SemiAutomator - NiceGUI web UI for serial interfaces.

Provides a dropdown for available serial ports, connecting/disconnecting,
sending arbitrary input and triggering individually created "macro"
commands.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

from nicegui import app, ui

from config import (
    Macro,
    PortPreset,
    backup_settings,
    load_config,
    load_favorites,
    load_macros,
    remove_favorite,
    save_favorite,
    save_macro,
)
from serial_manager import SerialConnection, SerialManager

config = load_config()
favorites = load_favorites()
user_macros = load_macros()
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


def _favorite_devices() -> list[str]:
    """Device paths of all saved favorites, in the order they were saved."""
    return [favorite.device for favorite in favorites]


def _macros_for(device: str) -> list[Macro]:
    # Only individually created macros are shown; there are no predefined
    # "standard" macros anymore.
    return [macro for macro in user_macros if macro.device is None or macro.device == device]


def build_port_card(device: str, baudrate: int, on_macro_saved=None, on_favorite_saved=None):
    """Build the console card for one device. Does not open the connection."""
    connection: SerialConnection = manager.get_or_create(device, baudrate)

    with ui.column().classes("w-full h-full gap-2"):
        with ui.row().classes("items-center w-full justify-between shrink-0"):
            ui.label(_device_options()[device]).classes("text-lg font-bold")
            status_badge = ui.badge("getrennt", color="red")

            def download_log() -> None:
                filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(device).name) or "serial"
                content = "\n".join(connection.lines).encode("utf-8")
                ui.download(content, f"{filename}.log", media_type="text/plain; charset=utf-8")

            ui.button("Log herunterladen", on_click=download_log, icon="download").props(
                "outline"
            )

        with ui.row().classes("items-center w-full shrink-0"):
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

            with ui.dialog() as favorite_dialog, ui.card():
                ui.label("Favorit speichern").classes("text-lg font-bold")
                favorite_name_input = ui.input(label="Name").classes("w-64")
                with ui.row().classes("justify-end w-full gap-2"):
                    ui.button("Abbrechen", on_click=favorite_dialog.close).props("flat")
                    confirm_favorite_button = ui.button("Speichern", icon="star")

            def open_favorite_dialog() -> None:
                favorite_name_input.value = _preset_name(device)
                favorite_dialog.open()

            def confirm_favorite() -> None:
                try:
                    name = (favorite_name_input.value or device).strip() or device
                    favorite = PortPreset(
                        name=name,
                        device=device,
                        baudrate=int(baud_input.value or connection.baudrate),
                    )
                    save_favorite(favorite)
                    favorites.clear()
                    favorites.extend(load_favorites())
                    favorite_dialog.close()
                    ui.notify(f"'{name}' als Favorit gespeichert", type="positive")
                    if on_favorite_saved is not None:
                        on_favorite_saved()
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Favorit konnte nicht gespeichert werden: {exc}", type="negative")

            confirm_favorite_button.on_click(confirm_favorite)

            ui.button(
                "Als Favorit speichern", on_click=open_favorite_dialog, icon="star"
            ).props("outline")

        log = ui.log(max_lines=500).classes("w-full flex-grow").style(
            "resize: vertical; overflow: auto; min-height: 150px;"
        )
        last_rendered_count = {"n": 0}

        with ui.row().classes("w-full justify-end shrink-0"):

            def clear_display() -> None:
                log.clear()
                connection.lines.clear()
                last_rendered_count["n"] = 0
                ui.notify("Anzeige geleert", type="info")

            ui.button("Anzeige leeren", on_click=clear_display, icon="delete_sweep").props(
                "outline dense"
            )

        with ui.row().classes("w-full items-center shrink-0"):
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

        with ui.dialog() as macro_dialog, ui.card():
            ui.label("Makro hinzufügen").classes("text-lg font-bold")
            macro_name_input = ui.input(label="Name").classes("w-64")
            macro_command_input = ui.input(label="Kommando").classes("w-64")
            macro_device_only = ui.checkbox(f"Nur für {device}", value=False)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Abbrechen", on_click=macro_dialog.close).props("flat")
                confirm_macro_button = ui.button("Speichern", icon="add")

        def render_macros() -> None:
            macros_row.clear()
            macros = _macros_for(device)
            with macros_row:
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
                ui.button(icon="add", on_click=macro_dialog.open).props(
                    "outline dense round"
                ).tooltip("Makro hinzufügen")

        def confirm_macro() -> None:
            try:
                name = (macro_name_input.value or "").strip()
                command = (macro_command_input.value or "").strip()
                if not name or not command:
                    ui.notify("Name und Kommando sind erforderlich", type="warning")
                    return
                macro = Macro(
                    label=name,
                    command=command,
                    device=device if macro_device_only.value else None,
                )
                save_macro(macro)
                user_macros.clear()
                user_macros.extend(load_macros())
                macro_name_input.value = ""
                macro_command_input.value = ""
                macro_device_only.value = False
                macro_dialog.close()
                render_macros()
                if on_macro_saved is not None:
                    on_macro_saved()
                ui.notify(f"Makro '{name}' gespeichert", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Makro konnte nicht gespeichert werden: {exc}", type="negative")

        confirm_macro_button.on_click(confirm_macro)

        ui.label("Makros").classes("text-sm text-grey-6 shrink-0")
        macros_row = ui.row().classes("w-full flex-wrap shrink-0")
        render_macros()

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
    ui.add_head_html(
        "<style>html, body, #app, .nicegui-content { height: 100%; }</style>"
    )

    page_state = {
        "devices": [],
        "active_device": None,
        "active_timer": None,
        "tabs": None,
    }

    with ui.header().classes("items-center justify-between").props("bordered"):
        with ui.row().classes("items-center"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props(
                "flat color=white round"
            )
            ui.label("RPI-SemiAutomator").classes("text-lg font-semibold")
        ui.button(
            "Einstellungen", icon="settings", on_click=lambda: ui.navigate.to("/settings")
        ).props("flat color=white")

    with ui.left_drawer(value=True, bordered=True) as drawer:
        ui.label("Favoriten").classes("text-sm font-semibold text-grey-6")
        favorites_container = ui.column().classes("w-full gap-1")

        def render_favorites() -> None:
            favorites_container.clear()
            if not favorites:
                with favorites_container:
                    ui.label(
                        "Keine Favoriten. Unter Einstellungen hinzufügen."
                    ).classes("text-xs text-grey-6")
                return
            with favorites_container:
                for favorite in list(favorites):
                    is_active = favorite.device == page_state["active_device"]
                    ui.button(
                        f"★ {favorite.name} ({favorite.device})",
                        on_click=lambda d=favorite.device: select_device(d),
                    ).props(
                        f"{'unelevated' if is_active else 'flat'} align=left no-caps"
                    ).classes("w-full")

    with ui.column().classes("w-full h-full gap-2 p-2"):
        tabs_row = ui.row().classes("w-full shrink-0")
        panel_container = ui.column().classes("w-full flex-grow overflow-hidden")

        def select_device(device: str) -> None:
            page_state["active_device"] = device
            if page_state["tabs"] is not None and page_state["tabs"].value != device:
                page_state["tabs"].value = device
            if page_state["active_timer"] is not None:
                page_state["active_timer"].cancel()
                page_state["active_timer"] = None
            panel_container.clear()
            render_favorites()
            with panel_container:
                with ui.column().classes("w-full h-full"):
                    baudrate = _known_devices().get(device, 115200)

                    def on_macro_saved() -> None:
                        render_terminals(_favorite_devices(), page_state["active_device"])

                    def on_favorite_saved() -> None:
                        render_terminals(_favorite_devices(), page_state["active_device"])

                    page_state["active_timer"] = build_port_card(
                        device, baudrate, on_macro_saved, on_favorite_saved
                    )

        def render_terminals(devices: list[str], active_device: str | None = None) -> None:
            if page_state["active_timer"] is not None:
                page_state["active_timer"].cancel()
                page_state["active_timer"] = None
            tabs_row.clear()
            panel_container.clear()
            page_state["devices"] = devices
            page_state["tabs"] = None

            if active_device not in devices:
                active_device = devices[0] if devices else None

            render_favorites()

            with tabs_row:
                if not devices:
                    ui.label(
                        "Keine Favoriten. Unter Einstellungen Favoriten hinzufügen."
                    )
                    page_state["active_device"] = None
                    return
                with ui.tabs().classes("w-full") as tabs:
                    for device in devices:
                        ui.tab(device, label=f"★ {_preset_name(device)} ({device})")
                tabs.value = active_device
                tabs.on_value_change(lambda event: select_device(event.value))
                page_state["tabs"] = tabs

            page_state["active_device"] = active_device
            if active_device is not None:
                select_device(active_device)

        def rescan() -> None:
            devices = _favorite_devices()
            if devices != page_state["devices"]:
                render_terminals(devices, page_state["active_device"])

        render_terminals(_favorite_devices())
        ui.timer(5.0, rescan)


@ui.page("/settings")
def settings_page() -> None:
    ui.add_head_html(
        "<style>html, body, #app, .nicegui-content { height: 100%; }</style>"
    )

    with ui.header().classes("items-center justify-between").props("bordered"):
        with ui.row().classes("items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat color=white round"
            )
            ui.label("Einstellungen").classes("text-lg font-semibold")

    with ui.column().classes("w-full max-w-2xl mx-auto gap-4 p-4"):
        ui.label("Favorit hinzufügen").classes("text-lg font-bold")
        with ui.row().classes("w-full items-end gap-2"):
            device_select = ui.select(
                options=_device_options(), label="Schnittstelle", with_input=True
            ).classes("flex-grow")
            new_name_input = ui.input(label="Name").classes("w-40")
            new_baud_input = ui.number(
                label="Baudrate", value=115200, min=110, max=4000000
            ).classes("w-32")

        favorites_list_container = ui.column().classes("w-full gap-1")

        def render_favorites_list() -> None:
            device_select.options = _device_options()
            device_select.update()
            favorites_list_container.clear()
            if not favorites:
                with favorites_list_container:
                    ui.label("Keine Favoriten.").classes("text-sm text-grey-6")
                return
            with favorites_list_container:
                for favorite in list(favorites):
                    with ui.row().classes("items-center w-full justify-between"):
                        ui.label(
                            f"{favorite.name} — {favorite.device} "
                            f"({favorite.baudrate} Baud)"
                        ).classes("text-sm")

                        def make_remove(dev: str = favorite.device):
                            def _remove() -> None:
                                remove_favorite(dev)
                                favorites.clear()
                                favorites.extend(load_favorites())
                                render_favorites_list()
                                ui.notify(f"{dev} aus Favoriten entfernt", type="info")

                            return _remove

                        ui.button(icon="delete", on_click=make_remove()).props(
                            "flat dense round"
                        )

        def add_favorite() -> None:
            device = device_select.value
            if not device:
                ui.notify("Bitte eine Schnittstelle auswählen", type="warning")
                return
            try:
                name = (new_name_input.value or _preset_name(device)).strip() or device
                favorite = PortPreset(
                    name=name,
                    device=device,
                    baudrate=int(new_baud_input.value or 115200),
                )
                save_favorite(favorite)
                favorites.clear()
                favorites.extend(load_favorites())
                device_select.value = None
                new_name_input.value = ""
                new_baud_input.value = 115200
                render_favorites_list()
                ui.notify(f"'{name}' als Favorit gespeichert", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    f"Favorit konnte nicht gespeichert werden: {exc}", type="negative"
                )

        ui.button("Favorit hinzufügen", on_click=add_favorite, icon="star").props("outline")

        ui.separator()
        ui.label("Gespeicherte Favoriten").classes("text-lg font-bold")
        render_favorites_list()

        ui.separator()
        ui.label("Daten").classes("text-lg font-bold")
        ui.label(
            "Favoriten und individuelle Makros sichern oder nach einer "
            "externen Änderung neu laden."
        ).classes("text-sm text-grey-6")

        def backup() -> None:
            content = backup_settings()
            ui.download(
                content, "rpi-semiautomator-backup.yaml", media_type="application/x-yaml"
            )
            ui.notify("Backup wurde heruntergeladen", type="positive")

        def reload_settings() -> None:
            favorites.clear()
            favorites.extend(load_favorites())
            user_macros.clear()
            user_macros.extend(load_macros())
            render_favorites_list()
            ui.notify("Einstellungen wurden neu geladen", type="positive")

        with ui.row().classes("gap-2"):
            ui.button("Backup herunterladen", on_click=backup, icon="download").props(
                "outline"
            )
            ui.button(
                "Einstellungen neu laden", on_click=reload_settings, icon="refresh"
            ).props("outline")


@app.on_shutdown
def _cleanup() -> None:
    manager.disconnect_all()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="RPI-SemiAutomator", port=8080, host="0.0.0.0", reload=False)
