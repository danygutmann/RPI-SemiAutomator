"""RPI-SemiAutomator - NiceGUI web UI for serial interfaces.

Provides a dropdown for available serial ports, connecting/disconnecting,
sending arbitrary input and triggering individually created "macro"
commands.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Dict

from fastapi import HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from nicegui import app, ui

from config import (
    Macro,
    PortPreset,
    backup_settings,
    load_config,
    load_favorites,
    load_macros,
    remove_favorite,
    remove_macro,
    save_favorite,
    save_macro,
    update_macro,
)
from serial_manager import SerialConnection, SerialManager

config = load_config()
favorites = load_favorites()
user_macros = load_macros()
manager = SerialManager()


def _setup_dark_mode() -> None:
    """Apply the persisted dark-mode choice and add a toggle to the header."""
    dark = ui.dark_mode(bool(app.storage.user.get("dark", False)))

    def toggle() -> None:
        dark.toggle()
        app.storage.user["dark"] = bool(dark.value)

    ui.button(icon="dark_mode", on_click=toggle).props("flat color=white round").tooltip(
        "Hell/Dunkel umschalten"
    )


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


def _device_aliases() -> dict[str, str]:
    """Return configured aliases by device, with saved favorites taking priority."""
    aliases = {preset.device: preset.name for preset in config.ports}
    aliases.update({favorite.device: favorite.name for favorite in favorites})
    return {device: alias for device, alias in aliases.items() if alias and alias != device}


def _api_device_for_alias(alias: str) -> str:
    matches = [device for device, name in _device_aliases().items() if name == alias]
    if not matches:
        raise HTTPException(status_code=404, detail="Unknown alias")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="Alias is not unique")
    return matches[0]


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


def build_port_card(device: str, baudrate: int):
    """Build the console card for one device. Does not open the connection."""
    connection: SerialConnection = manager.get_or_create(device, baudrate)
    if not connection.is_open:
        connection.baudrate = baudrate

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
            def do_connect() -> None:
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

        log = ui.log(max_lines=500).classes("w-full flex-grow").style(
            "resize: vertical; overflow: auto; min-height: 150px;"
        )
        size_key = json.dumps(f"rpi-semiautomator-logheight:{device}")
        ui.run_javascript(
            f"""
            const el = document.getElementById('c{log.id}');
            if (el) {{
                const saved = localStorage.getItem({size_key});
                if (saved) el.style.height = saved;
                let timer = null;
                new ResizeObserver(() => {{
                    clearTimeout(timer);
                    timer = setTimeout(() => {{
                        if (el.style.height) localStorage.setItem({size_key}, el.style.height);
                    }}, 300);
                }}).observe(el);
            }}
            """
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

        key_map = {
            "Enter": "\n",
            "Tab": "\t",
            "Backspace": "\x7f",
            "Escape": "\x1b",
            "ArrowUp": "\x1b[A",
            "ArrowDown": "\x1b[B",
            "ArrowRight": "\x1b[C",
            "ArrowLeft": "\x1b[D",
            "Delete": "\x1b[3~",
            "Home": "\x1b[H",
            "End": "\x1b[F",
        }

        def on_key(e) -> None:
            args = e.args or {}
            key = args.get("key", "")
            if args.get("ctrlKey") and len(key) == 1 and key.isalpha():
                payload = chr(ord(key.lower()) - 96)
            elif key in key_map:
                payload = key_map[key]
            elif len(key) == 1 and not args.get("ctrlKey") and not args.get("metaKey"):
                payload = key
            else:
                return
            try:
                connection.send(payload, raw=True)
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Senden fehlgeschlagen: {exc}", type="negative")

        log.props("tabindex=0")
        log.on(
            "keydown",
            on_key,
            args=["key", "ctrlKey", "metaKey"],
            js_handler="""(e) => {
                const k = e.key;
                if (e.metaKey || (e.ctrlKey && k.length !== 1)) return;
                if (k.length === 1 || ['Enter','Tab','Backspace','Escape','ArrowUp',
                    'ArrowDown','ArrowLeft','ArrowRight','Delete','Home','End'].includes(k)) {
                    e.preventDefault();
                    emit({key: k, ctrlKey: e.ctrlKey, metaKey: e.metaKey});
                }
            }""",
        )
        ui.label("Zum Tippen in die Konsole klicken – Eingaben werden direkt gesendet.").classes(
            "text-xs text-grey shrink-0"
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
                log.push(line)
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
        with ui.row().classes("items-center"):
            _setup_dark_mode()
            ui.button(
                "Einstellungen", icon="settings", on_click=lambda: ui.navigate.to("/settings")
            ).props("flat color=white")

    with ui.left_drawer(value=False, bordered=True) as drawer:
        ui.label("Makros").classes("text-lg font-semibold")
        ui.label("Rechtsklick auf einen Ordner oder ein Makro, um Makros hinzuzufügen.").classes(
            "text-xs text-grey-6"
        )
        macros_container = ui.column().classes("w-full gap-1")
        with ui.dialog() as macro_dialog, ui.card():
            ui.label("Makro hinzufügen").classes("text-lg font-bold")
            macro_name_input = ui.input(label="Name").classes("w-64")
            macro_command_input = ui.input(label="Kommando").classes("w-64")
            macro_category_input = ui.input(label="Ordner (mit / verschachteln)").classes("w-64")
            macro_device_only = ui.checkbox(
                "Nur für die aktive Schnittstelle", value=False
            )
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Abbrechen", on_click=macro_dialog.close).props("flat")
                confirm_macro_button = ui.button("Speichern", icon="add")

        def open_macro_dialog(category: str = "") -> None:
            macro_category_input.value = category
            macro_dialog.open()

        def confirm_macro() -> None:
            name = (macro_name_input.value or "").strip()
            command = (macro_command_input.value or "").strip()
            if not name or not command:
                ui.notify("Name und Kommando sind erforderlich", type="warning")
                return
            if macro_device_only.value and page_state["active_device"] is None:
                ui.notify("Keine aktive Schnittstelle ausgewählt", type="warning")
                return
            category = "/".join(
                part.strip()
                for part in (macro_category_input.value or "").split("/")
                if part.strip()
            )
            try:
                save_macro(
                    Macro(
                        label=name,
                        command=command,
                        device=(
                            page_state["active_device"]
                            if macro_device_only.value
                            else None
                        ),
                        category=category or None,
                    )
                )
                user_macros.clear()
                user_macros.extend(load_macros())
                macro_dialog.close()
                macro_name_input.value = ""
                macro_command_input.value = ""
                macro_device_only.value = False
                render_macro_tree()
                ui.notify(f"Makro '{name}' gespeichert", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Makro konnte nicht gespeichert werden: {exc}", type="negative")

        confirm_macro_button.on_click(confirm_macro)

        def render_macro_tree() -> None:
            macros_container.clear()
            device = page_state["active_device"]
            macros = _macros_for(device) if device else [
                macro for macro in user_macros if macro.device is None
            ]
            root = {"groups": {}, "macros": []}
            for macro in macros:
                node = root
                for part in (macro.category or "").split("/"):
                    part = part.strip()
                    if part:
                        node = node["groups"].setdefault(
                            part, {"groups": {}, "macros": []}
                        )
                node["macros"].append(macro)

            def draw_group(node: dict, category: str = "") -> None:
                for group, child in node["groups"].items():
                    group_path = f"{category}/{group}".strip("/")
                    with ui.expansion(group, icon="folder").classes("w-full"):
                        with ui.column().classes("w-full gap-1 pl-2"):
                            draw_group(child, group_path)
                        with ui.context_menu():
                            ui.menu_item(
                                "Makro hinzufügen",
                                on_click=lambda path=group_path: open_macro_dialog(path),
                            )
                for macro in node["macros"]:
                    def make_sender(item: Macro):
                        def send() -> None:
                            selected = page_state["active_device"]
                            if selected is None:
                                ui.notify("Keine aktive Schnittstelle ausgewählt", type="warning")
                                return
                            connection = manager.get_or_create(
                                selected, _known_devices().get(selected, 115200)
                            )
                            try:
                                connection.send(item.command, raw=item.raw)
                                ui.notify(f"Makro '{item.label}' gesendet")
                            except Exception as exc:  # noqa: BLE001
                                ui.notify(f"Makro fehlgeschlagen: {exc}", type="negative")

                        return send

                    with ui.button(
                        macro.label, on_click=make_sender(macro)
                    ).props("flat align=left no-caps").classes("w-full") as macro_button:
                        with ui.context_menu():
                            ui.menu_item(
                                "Makro hinzufügen",
                                on_click=lambda path=category: open_macro_dialog(path),
                            )

            with macros_container:
                if not macros:
                    ui.label("Keine Makros. Rechtsklick zum Hinzufügen.").classes(
                        "text-sm text-grey-6"
                    )
                else:
                    draw_group(root)
                with ui.context_menu():
                    ui.menu_item(
                        "Makro hinzufügen", on_click=lambda: open_macro_dialog("")
                    )

    with ui.column().classes("w-full h-full gap-2 p-2"):
        tabs_row = ui.row().classes("w-full shrink-0 justify-center")
        panel_container = ui.column().classes("w-full flex-grow overflow-hidden")

        def select_device(device: str) -> None:
            page_state["active_device"] = device
            if page_state["tabs"] is not None and page_state["tabs"].value != device:
                page_state["tabs"].value = device
            if page_state["active_timer"] is not None:
                page_state["active_timer"].cancel()
                page_state["active_timer"] = None
            panel_container.clear()
            render_macro_tree()
            with panel_container:
                with ui.column().classes("w-full h-full"):
                    baudrate = _known_devices().get(device, 115200)
                    page_state["active_timer"] = build_port_card(device, baudrate)

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

            with tabs_row:
                if not devices:
                    ui.label(
                        "Keine Favoriten. Unter Einstellungen Favoriten hinzufügen."
                    )
                    page_state["active_device"] = None
                    return
                with ui.tabs().classes("w-full").props("align=center") as tabs:
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
        _setup_dark_mode()

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

                        def make_edit(fav: PortPreset = favorite):
                            def _edit() -> None:
                                with ui.dialog() as dialog, ui.card():
                                    ui.label("Favorit bearbeiten").classes("text-lg font-bold")
                                    name_input = ui.input(label="Name", value=fav.name).classes("w-64")
                                    baud_input = ui.number(
                                        label="Baudrate", value=fav.baudrate, min=110, max=4000000
                                    ).classes("w-64")

                                    def save() -> None:
                                        name = (name_input.value or "").strip() or fav.device
                                        save_favorite(
                                            PortPreset(
                                                name=name,
                                                device=fav.device,
                                                baudrate=int(baud_input.value or fav.baudrate),
                                            )
                                        )
                                        favorites.clear()
                                        favorites.extend(load_favorites())
                                        dialog.close()
                                        render_favorites_list()
                                        ui.notify(f"'{name}' gespeichert", type="positive")

                                    with ui.row().classes("justify-end w-full gap-2"):
                                        ui.button("Abbrechen", on_click=dialog.close).props("flat")
                                        ui.button("Speichern", on_click=save, icon="save")
                                dialog.open()

                            return _edit

                        def make_remove(dev: str = favorite.device):
                            def _remove() -> None:
                                remove_favorite(dev)
                                favorites.clear()
                                favorites.extend(load_favorites())
                                render_favorites_list()
                                ui.notify(f"{dev} aus Favoriten entfernt", type="info")

                            return _remove

                        with ui.row().classes("gap-0"):
                            ui.button(icon="edit", on_click=make_edit()).props(
                                "flat dense round"
                            )
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
        ui.label("Makrotasten").classes("text-lg font-bold")
        macros_list_container = ui.column().classes("w-full gap-1")

        def render_macros_list() -> None:
            macros_list_container.clear()
            with macros_list_container:
                if not user_macros:
                    ui.label("Keine Makros.").classes("text-sm text-grey-6")
                    return
                for macro in list(user_macros):
                    scope = macro.device or "alle Schnittstellen"
                    with ui.row().classes("items-center w-full justify-between"):
                        ui.label(f"{macro.label} — {macro.command} ({scope})").classes(
                            "text-sm"
                        )

                        def make_edit_macro(m: Macro = macro):
                            def _edit() -> None:
                                with ui.dialog() as dialog, ui.card():
                                    ui.label("Makro bearbeiten").classes("text-lg font-bold")
                                    name_input = ui.input(label="Name", value=m.label).classes("w-64")
                                    command_input = ui.input(
                                        label="Kommando", value=m.command
                                    ).classes("w-64")
                                    category_input = ui.input(
                                        label="Ordner (mit / verschachteln)",
                                        value=m.category or "",
                                    ).classes("w-64")
                                    device_select = ui.select(
                                        options={"": "Alle Schnittstellen", **_device_options()},
                                        label="Schnittstelle",
                                        value=m.device or "",
                                    ).classes("w-64")

                                    def save() -> None:
                                        name = (name_input.value or "").strip()
                                        command = (command_input.value or "").strip()
                                        if not name or not command:
                                            ui.notify(
                                                "Name und Kommando sind erforderlich",
                                                type="warning",
                                            )
                                            return
                                        update_macro(
                                            m.label,
                                            m.device,
                                            Macro(
                                                label=name,
                                                command=command,
                                                raw=m.raw,
                                                device=device_select.value or None,
                                                category="/".join(
                                                    part.strip()
                                                    for part in (category_input.value or "").split("/")
                                                    if part.strip()
                                                )
                                                or None,
                                            ),
                                        )
                                        user_macros.clear()
                                        user_macros.extend(load_macros())
                                        dialog.close()
                                        render_macros_list()
                                        ui.notify(f"Makro '{name}' gespeichert", type="positive")

                                    with ui.row().classes("justify-end w-full gap-2"):
                                        ui.button("Abbrechen", on_click=dialog.close).props("flat")
                                        ui.button("Speichern", on_click=save, icon="save")
                                dialog.open()

                            return _edit

                        def make_delete_macro(m: Macro = macro):
                            def _delete() -> None:
                                remove_macro(m.label, m.device)
                                user_macros.clear()
                                user_macros.extend(load_macros())
                                render_macros_list()
                                ui.notify(f"Makro '{m.label}' gelöscht", type="info")

                            return _delete

                        with ui.row().classes("gap-0"):
                            ui.button(icon="edit", on_click=make_edit_macro()).props(
                                "flat dense round"
                            )
                            ui.button(icon="delete", on_click=make_delete_macro()).props(
                                "flat dense round"
                            )

        render_macros_list()

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
            render_macros_list()
            ui.notify("Einstellungen wurden neu geladen", type="positive")

        with ui.row().classes("gap-2"):
            ui.button("Backup herunterladen", on_click=backup, icon="download").props(
                "outline"
            )
            ui.button(
                "Einstellungen neu laden", on_click=reload_settings, icon="refresh"
            ).props("outline")


def _check_api_token(token: str | None) -> None:
    """Enforce the optional ``RPI_SEMIAUTOMATOR_API_TOKEN`` for API access."""
    expected = os.environ.get("RPI_SEMIAUTOMATOR_API_TOKEN")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.query_params.get("token")


def _api_connection(device: str) -> SerialConnection:
    connection = manager.all().get(device)
    if connection is None:
        connection = manager.get_or_create(device, _known_devices().get(device, 115200))
    return connection


def _api_macro_payload(macro: Macro) -> dict:
    return {
        "label": macro.label,
        "command": macro.command,
        "raw": macro.raw,
        "device": macro.device,
        "category": macro.category,
    }


def _api_macro_from_body(body: dict) -> Macro:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="A JSON object is required")
    label = body.get("label")
    command = body.get("command")
    raw = body.get("raw", False)
    device = body.get("device")
    category = body.get("category")
    if device == "":
        device = None
    if category == "":
        category = None
    if not isinstance(label, str) or not label.strip():
        raise HTTPException(status_code=400, detail="'label' is required")
    if not isinstance(command, str) or not command.strip():
        raise HTTPException(status_code=400, detail="'command' is required")
    if not isinstance(raw, bool):
        raise HTTPException(status_code=400, detail="'raw' must be a boolean")
    if device is not None and not isinstance(device, str):
        raise HTTPException(status_code=400, detail="'device' must be a string or null")
    if device is not None and device not in _known_devices():
        raise HTTPException(status_code=404, detail="Unknown device")
    if category is not None and not isinstance(category, str):
        raise HTTPException(status_code=400, detail="'category' must be a string or null")
    normalized_category = (
        "/".join(part.strip() for part in category.split("/") if part.strip())
        if category
        else None
    )
    return Macro(
        label=label.strip(),
        command=command.strip(),
        raw=raw,
        device=device,
        category=normalized_category or None,
    )


def _reload_api_macros() -> None:
    user_macros.clear()
    user_macros.extend(load_macros())


@app.get("/api/ports")
def api_list_ports(request: Request) -> list[dict]:
    _check_api_token(_bearer(request))
    result = []
    aliases = _device_aliases()
    for device, baudrate in _known_devices().items():
        connection = manager.all().get(device)
        result.append(
            {
                "device": device,
                "name": _preset_name(device),
                "alias": aliases.get(device),
                "baudrate": connection.baudrate if connection else baudrate,
                "connected": bool(connection and connection.is_open),
            }
        )
    return result


@app.get("/api/macros")
def api_list_macros(request: Request, device: str | None = None) -> list[dict]:
    """List macros, optionally showing those applicable to one interface."""
    _check_api_token(_bearer(request))
    if device is not None and device not in _known_devices():
        raise HTTPException(status_code=404, detail="Unknown device")
    macros = load_macros()
    if device is not None:
        macros = [macro for macro in macros if macro.device is None or macro.device == device]
    return [_api_macro_payload(macro) for macro in macros]


@app.get("/api/aliases/{alias}/macros")
def api_list_alias_macros(alias: str, request: Request) -> list[dict]:
    """List macros applicable to the interface identified by its alias."""
    _check_api_token(_bearer(request))
    device = _api_device_for_alias(alias)
    macros = [
        macro
        for macro in load_macros()
        if macro.device is None or macro.device == device
    ]
    return [_api_macro_payload(macro) for macro in macros]


@app.post("/api/macros", status_code=201)
async def api_create_macro(request: Request) -> dict:
    _check_api_token(_bearer(request))
    macro = _api_macro_from_body(await request.json())
    if any(
        existing.device == macro.device and existing.label == macro.label
        for existing in load_macros()
    ):
        raise HTTPException(status_code=409, detail="Macro already exists")
    save_macro(macro)
    _reload_api_macros()
    return _api_macro_payload(macro)


@app.put("/api/macros")
async def api_update_macro(request: Request) -> dict:
    _check_api_token(_bearer(request))
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="A JSON object is required")
    old_label = body.get("old_label")
    old_device = body.get("old_device")
    if old_device == "":
        old_device = None
    if not isinstance(old_label, str) or not old_label.strip():
        raise HTTPException(status_code=400, detail="'old_label' is required")
    if old_device is not None and not isinstance(old_device, str):
        raise HTTPException(status_code=400, detail="'old_device' must be a string or null")
    macro = _api_macro_from_body(body)
    existing = load_macros()
    if not any(
        item.label == old_label.strip() and item.device == old_device
        for item in existing
    ):
        raise HTTPException(status_code=404, detail="Macro not found")
    if any(
        item.label == macro.label
        and item.device == macro.device
        and (item.label != old_label.strip() or item.device != old_device)
        for item in existing
    ):
        raise HTTPException(status_code=409, detail="A macro with that label already exists")
    update_macro(old_label.strip(), old_device, macro)
    _reload_api_macros()
    return _api_macro_payload(macro)


@app.delete("/api/macros/{label}")
def api_delete_macro(label: str, request: Request, device: str | None = None) -> dict:
    _check_api_token(_bearer(request))
    if not any(
        macro.label == label and macro.device == device for macro in load_macros()
    ):
        raise HTTPException(status_code=404, detail="Macro not found")
    remove_macro(label, device)
    _reload_api_macros()
    return {"ok": True}


@app.get("/api/backup")
def api_backup(request: Request) -> Response:
    _check_api_token(_bearer(request))
    return Response(
        content=backup_settings(),
        media_type="application/x-yaml",
        headers={"Content-Disposition": 'attachment; filename="rpi-semiautomator-backup.yaml"'},
    )


@app.get("/api/aliases")
def api_list_aliases(request: Request) -> list[dict]:
    """List interfaces addressable by their configured alias."""
    _check_api_token(_bearer(request))
    result = []
    devices = _known_devices()
    for device, alias in _device_aliases().items():
        connection = manager.all().get(device)
        result.append(
            {
                "alias": alias,
                "device": device,
                "baudrate": connection.baudrate if connection else devices.get(device, 115200),
                "connected": bool(connection and connection.is_open),
            }
        )
    return result


def _api_send_to_device(device: str, body: dict) -> dict:
    command = body.get("command")
    if not isinstance(command, str):
        raise HTTPException(status_code=400, detail="'command' is required")
    try:
        _api_connection(device).send(command, raw=bool(body.get("raw", False)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/send")
async def api_send(request: Request) -> dict:
    """Send ``{"device": ..., "command": ..., "raw": false}`` to a serial port."""
    _check_api_token(_bearer(request))
    body = await request.json()
    device = body.get("device")
    command = body.get("command")
    if not isinstance(device, str) or not isinstance(command, str):
        raise HTTPException(status_code=400, detail="'device' and 'command' are required")
    if device not in _known_devices():
        raise HTTPException(status_code=404, detail="Unknown device")
    return _api_send_to_device(device, body)


@app.post("/api/aliases/{alias}/send")
async def api_send_to_alias(alias: str, request: Request) -> dict:
    """Send a command using a configured interface alias."""
    _check_api_token(_bearer(request))
    device = _api_device_for_alias(alias)
    body = await request.json()
    return _api_send_to_device(device, body)


async def _api_ws_for_device(
    websocket: WebSocket, device: str, token: str | None = None
) -> None:
    """Stream RX/TX of ``device`` as JSON and accept input.

    Server -> client: ``{"direction": "rx"|"tx", "text": "..."}``; rx text is
    streamed as raw chunks (no line buffering) so prompts appear immediately.
    Client -> server: plain text or ``{"command": "...", "raw": false}``.
    """
    auth = websocket.headers.get("authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else token
    expected = os.environ.get("RPI_SEMIAUTOMATOR_API_TOKEN")
    if expected and supplied != expected:
        await websocket.close(code=1008)
        return
    if device not in _known_devices():
        await websocket.close(code=1008)
        return

    await websocket.accept()
    connection = _api_connection(device)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def listener(direction: str, text: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"direction": direction, "text": text})

    async def pump_out() -> None:
        while True:
            await websocket.send_json(await queue.get())

    connection.subscribe(listener)
    if not connection.is_open:
        try:
            connection.connect()
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json({"error": str(exc)})
    sender = asyncio.create_task(pump_out())
    try:
        while True:
            message = await websocket.receive_text()
            raw = False
            command = message
            try:
                parsed = json.loads(message)
                if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
                    command = parsed["command"]
                    raw = bool(parsed.get("raw", False))
            except ValueError:
                pass
            try:
                connection.send(command, raw=raw)
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json({"error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        connection.unsubscribe(listener)


@app.websocket("/api/ws")
async def api_ws(websocket: WebSocket, device: str, token: str | None = None) -> None:
    await _api_ws_for_device(websocket, device, token)


@app.websocket("/api/aliases/{alias}/ws")
async def api_ws_alias(
    websocket: WebSocket, alias: str, token: str | None = None
) -> None:
    try:
        device = _api_device_for_alias(alias)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await _api_ws_for_device(websocket, device, token)


@app.on_shutdown
def _cleanup() -> None:
    manager.disconnect_all()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="RPI-SemiAutomator",
        port=8080,
        host="0.0.0.0",
        reload=False,
        fastapi_docs=True,
        storage_secret=os.environ.get("RPI_SEMIAUTOMATOR_STORAGE_SECRET", "rpi-semiautomator"),
    )
