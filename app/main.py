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
    set_device_macros,
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


def build_macro_tree(macros: list[Macro]) -> tuple[list[dict], dict[str, Macro]]:
    """Group macros into a multi-level tree using ``/`` in the label as separator.

    Returns the node list for ``ui.tree`` and a mapping of leaf node id -> macro.
    Folder node ids are ``folder:<path>``; leaf node ids are ``macro:<n>``.
    """
    roots: list[dict] = []
    folders: dict[str, dict] = {}
    leaves: dict[str, Macro] = {}
    for number, macro in enumerate(macros):
        parts = [part.strip() for part in macro.label.split("/") if part.strip()]
        if not parts:
            continue
        siblings = roots
        path = ""
        for part in parts[:-1]:
            path = f"{path}/{part}" if path else part
            folder = folders.get(path)
            if folder is None:
                folder = {"id": f"folder:{path}", "label": part, "children": []}
                folders[path] = folder
                siblings.append(folder)
            siblings = folder["children"]
        leaf_id = f"macro:{number}"
        siblings.append({"id": leaf_id, "label": parts[-1], "children": []})
        leaves[leaf_id] = macro
    return roots, leaves


def build_port_card(device: str, baudrate: int, state: dict | None = None):
    """Build the console card for one device. Does not open the connection."""
    connection: SerialConnection = manager.get_or_create(device, baudrate)
    # The baud rate is configured in the settings (favorite), not in the terminal.
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
        if state is not None:
            state["push_log"] = log.push

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
        "card_state": {},
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

    with ui.left_drawer(value=True, bordered=True) as drawer:
        ui.label("Makros").classes("text-sm font-semibold text-grey-6")
        macro_tree_container = ui.column().classes("w-full gap-1")
        macro_tree_container.on("contextmenu.prevent", lambda: open_macro_dialog(""))

        with ui.dialog() as macro_dialog, ui.card():
            macro_dialog_title = ui.label("Makro hinzufügen").classes("text-lg font-bold")
            macro_name_input = ui.input(
                label="Pfad/Name", placeholder="Gruppe/Untergruppe/Name"
            ).classes("w-72")
            macro_command_input = ui.input(label="Kommando").classes("w-72")
            macro_device_only = ui.checkbox("Nur für diese Schnittstelle", value=False)
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Abbrechen", on_click=macro_dialog.close).props("flat")
                confirm_macro_button = ui.button("Speichern", icon="add")

        def open_macro_dialog(prefix: str) -> None:
            macro_name_input.value = f"{prefix}/" if prefix else ""
            macro_command_input.value = ""
            macro_device_only.value = False
            macro_device_only.set_visibility(page_state["active_device"] is not None)
            macro_dialog.open()

        def confirm_macro() -> None:
            try:
                name = "/".join(
                    part.strip() for part in (macro_name_input.value or "").split("/") if part.strip()
                )
                command = (macro_command_input.value or "").strip()
                if not name or not command:
                    ui.notify("Name und Kommando sind erforderlich", type="warning")
                    return
                device = page_state["active_device"] if macro_device_only.value else None
                save_macro(Macro(label=name, command=command, device=device))
                user_macros.clear()
                user_macros.extend(load_macros())
                macro_dialog.close()
                render_macro_tree()
                ui.notify(f"Makro '{name}' gespeichert", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Makro konnte nicht gespeichert werden: {exc}", type="negative")

        confirm_macro_button.on_click(confirm_macro)

        def send_macro(macro: Macro) -> None:
            device = page_state["active_device"]
            if device is None:
                ui.notify("Keine Schnittstelle ausgewählt", type="warning")
                return
            try:
                manager.get_or_create(device, _known_devices().get(device, 115200)).send(
                    macro.command, raw=macro.raw
                )
                push_log = page_state["card_state"].get("push_log")
                if push_log is not None:
                    push_log(f"TX → {macro.command}")
                ui.notify(f"Makro '{macro.label}' gesendet")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Makro fehlgeschlagen: {exc}", type="negative")

        def render_macro_tree() -> None:
            macro_tree_container.clear()
            device = page_state["active_device"]
            nodes, leaves = (
                build_macro_tree(_macros_for(device)) if device is not None else ([], {})
            )
            with macro_tree_container:
                if not nodes:
                    ui.label(
                        "Keine Makros. Rechtsklick zum Hinzufügen."
                    ).classes("text-xs text-grey-6")
                    return
                tree = ui.tree(nodes, node_key="id", label_key="label").classes("w-full")
                tree.add_slot(
                    "default-header",
                    "<div class='row items-center' style='width:100%' "
                    "@contextmenu.prevent.stop=\"$parent.$emit('node_menu', props.node.id)\">"
                    "{{ props.node.label }}</div>",
                )

                def on_select(event) -> None:
                    macro = leaves.get(event.value)
                    if macro is not None:
                        send_macro(macro)
                        tree.props(remove="selected")
                        tree.update()

                def on_menu(event) -> None:
                    node_id = event.args
                    if isinstance(node_id, str) and node_id.startswith("folder:"):
                        open_macro_dialog(node_id[len("folder:") :])
                    else:
                        open_macro_dialog("")

                tree.on_select(on_select)
                tree.on("node_menu", on_menu)
                tree.expand()

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
            page_state["card_state"] = {}
            render_macro_tree()
            with panel_container:
                with ui.column().classes("w-full h-full"):
                    baudrate = _known_devices().get(device, 115200)
                    page_state["active_timer"] = build_port_card(
                        device, baudrate, page_state["card_state"]
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

            with tabs_row:
                if not devices:
                    ui.label(
                        "Keine Favoriten. Unter Einstellungen Favoriten hinzufügen."
                    )
                    page_state["active_device"] = None
                    render_macro_tree()
                    return
                with ui.tabs().props("align=center") as tabs:
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


@app.get("/api/ports")
def api_list_ports(request: Request) -> list[dict]:
    _check_api_token(_bearer(request))
    result = []
    for device, baudrate in _known_devices().items():
        connection = manager.all().get(device)
        result.append(
            {
                "device": device,
                "name": _preset_name(device),
                "baudrate": connection.baudrate if connection else baudrate,
                "connected": bool(connection and connection.is_open),
            }
        )
    return result


def _api_interface(alias: str) -> PortPreset:
    """Resolve an interface alias (the favorite's name) to its saved favorite."""
    for favorite in favorites:
        if favorite.name == alias:
            return favorite
    raise HTTPException(status_code=404, detail="Unknown interface alias")


def _macro_json(macro: Macro) -> dict:
    return {
        "label": macro.label,
        "command": macro.command,
        "raw": macro.raw,
        "scope": "global" if macro.device is None else "interface",
    }


def _reload_user_macros() -> None:
    user_macros.clear()
    user_macros.extend(load_macros())


def _parse_macro(body: object, label: str | None = None) -> Macro:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")
    label = label if label is not None else body.get("label")
    command = body.get("command")
    if not isinstance(label, str) or not label.strip():
        raise HTTPException(status_code=400, detail="'label' is required")
    if not isinstance(command, str) or not command.strip():
        raise HTTPException(status_code=400, detail="'command' is required")
    return Macro(label=label.strip(), command=command.strip(), raw=bool(body.get("raw", False)))


@app.get("/api/interfaces")
def api_list_interfaces(request: Request) -> list[dict]:
    """List all saved interfaces (favorites) by alias, without exposing port names as keys."""
    _check_api_token(_bearer(request))
    result = []
    for favorite in favorites:
        connection = manager.all().get(favorite.device)
        result.append(
            {
                "alias": favorite.name,
                "device": favorite.device,
                "baudrate": connection.baudrate if connection else favorite.baudrate,
                "connected": bool(connection and connection.is_open),
            }
        )
    return result


@app.post("/api/interfaces/{alias}/send")
async def api_interface_send(alias: str, request: Request) -> dict:
    """Send ``{"command": ..., "raw": false}`` to the interface with this alias."""
    _check_api_token(_bearer(request))
    favorite = _api_interface(alias)
    body = await request.json()
    command = body.get("command") if isinstance(body, dict) else None
    if not isinstance(command, str):
        raise HTTPException(status_code=400, detail="'command' is required")
    try:
        _api_connection(favorite.device).send(command, raw=bool(body.get("raw", False)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/interfaces/{alias}/macros")
def api_get_macros(alias: str, request: Request) -> list[dict]:
    """Macros usable on this interface (interface-specific and global ones)."""
    _check_api_token(_bearer(request))
    favorite = _api_interface(alias)
    return [_macro_json(macro) for macro in _macros_for(favorite.device)]


@app.put("/api/interfaces/{alias}/macros")
async def api_replace_macros(alias: str, request: Request) -> list[dict]:
    """Replace all interface-specific macros with the given JSON list (global ones are kept)."""
    _check_api_token(_bearer(request))
    favorite = _api_interface(alias)
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(status_code=400, detail="JSON list expected")
    macros = [_parse_macro(item) for item in body]
    if len({macro.label for macro in macros}) != len(macros):
        raise HTTPException(status_code=400, detail="Duplicate labels")
    set_device_macros(favorite.device, macros)
    _reload_user_macros()
    return [_macro_json(macro) for macro in _macros_for(favorite.device)]


@app.put("/api/interfaces/{alias}/macros/{label:path}")
async def api_put_macro(alias: str, label: str, request: Request) -> dict:
    """Create or update one interface-specific macro ``label``: ``{"command": ..., "raw": false}``."""
    _check_api_token(_bearer(request))
    favorite = _api_interface(alias)
    macro = _parse_macro(await request.json(), label)
    macro.device = favorite.device
    update_macro(macro.label, favorite.device, macro)
    _reload_user_macros()
    return _macro_json(macro)


@app.delete("/api/interfaces/{alias}/macros/{label:path}")
def api_delete_macro(alias: str, label: str, request: Request) -> dict:
    """Delete one interface-specific macro."""
    _check_api_token(_bearer(request))
    favorite = _api_interface(alias)
    if not any(m.device == favorite.device and m.label == label for m in user_macros):
        raise HTTPException(status_code=404, detail="Unknown macro")
    remove_macro(label, favorite.device)
    _reload_user_macros()
    return {"ok": True}


@app.get("/api/backup")
def api_backup(request: Request) -> Response:
    """Download favorites (interfaces with alias/baudrate) and macros as one YAML document."""
    _check_api_token(_bearer(request))
    return Response(
        content=backup_settings(),
        media_type="application/x-yaml",
        headers={"Content-Disposition": 'attachment; filename="rpi-semiautomator-backup.yaml"'},
    )


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
    try:
        _api_connection(device).send(command, raw=bool(body.get("raw", False)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.websocket("/api/ws")
async def api_ws(websocket: WebSocket, device: str, token: str | None = None) -> None:
    """Stream RX/TX of ``device`` as JSON and accept input.

    Server -> client: ``{"direction": "rx"|"tx", "text": "..."}``.
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


@app.on_shutdown
def _cleanup() -> None:
    manager.disconnect_all()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="RPI-SemiAutomator",
        port=8080,
        host="0.0.0.0",
        reload=False,
        storage_secret=os.environ.get("RPI_SEMIAUTOMATOR_STORAGE_SECRET", "rpi-semiautomator"),
    )
