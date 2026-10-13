"""RPI-SemiAutomator - NiceGUI web UI for serial interfaces.

Provides a dropdown for available serial ports, connecting/disconnecting,
sending arbitrary input and triggering individually created "macro"
commands.
"""
from __future__ import annotations

import asyncio
import json
import os
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
    remove_macro,
    save_macro,
    update_macro,
)
from panels import nav, register_api
from serial_manager import SerialConnection, SerialManager
from serial_panel import build_port_card
from settings_page import build_settings_page
from ui_common import setup_dark_mode

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


def _macros_for(alias: str) -> list[Macro]:
    # Only individually created macros are shown; there are no predefined
    # "standard" macros anymore.
    return [macro for macro in user_macros if macro.alias == alias]


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
        nav("serial")
        with ui.row().classes("items-center"):
            setup_dark_mode()
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
            macro_alias_label = ui.label()
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Abbrechen", on_click=macro_dialog.close).props("flat")
                confirm_macro_button = ui.button("Speichern", icon="add")

        def open_macro_dialog(category: str = "") -> None:
            macro_category_input.value = category
            device = page_state["active_device"]
            alias = _device_aliases().get(device) if device else None
            macro_alias_label.set_text(f"Alias: {alias or 'unbekannt'}")
            macro_dialog.open()

        def confirm_macro() -> None:
            name = (macro_name_input.value or "").strip()
            command = (macro_command_input.value or "").strip()
            if not name or not command:
                ui.notify("Name und Kommando sind erforderlich", type="warning")
                return
            device = page_state["active_device"]
            alias = _device_aliases().get(device) if device else None
            if not alias:
                ui.notify("Keine aktive Schnittstelle ausgewählt", type="warning")
                return
            try:
                _api_device_for_alias(alias)
            except HTTPException:
                ui.notify("Der Alias ist nicht eindeutig", type="warning")
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
                        alias=alias,
                        category=category or None,
                    )
                )
                user_macros.clear()
                user_macros.extend(load_macros())
                macro_dialog.close()
                macro_name_input.value = ""
                macro_command_input.value = ""
                render_macro_tree()
                ui.notify(f"Makro '{name}' gespeichert", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"Makro konnte nicht gespeichert werden: {exc}", type="negative")

        confirm_macro_button.on_click(confirm_macro)

        def render_macro_tree() -> None:
            macros_container.clear()
            device = page_state["active_device"]
            alias = _device_aliases().get(device) if device else None
            macros = _macros_for(alias) if alias else []
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
                            selected_alias = _device_aliases().get(selected) if selected else None
                            if selected is None or selected_alias != item.alias:
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
                    page_state["active_timer"] = build_port_card(
                        device,
                        baudrate,
                        manager.get_or_create(device, baudrate),
                        _device_options()[device],
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
    build_settings_page(
        setup_dark_mode,
        favorites,
        user_macros,
        _device_options,
        _preset_name,
        _device_aliases,
        _api_device_for_alias,
    )


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
        "alias": macro.alias,
        "category": macro.category,
    }


def _api_macro_from_body(body: dict) -> Macro:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="A JSON object is required")
    label = body.get("label")
    command = body.get("command")
    raw = body.get("raw", False)
    alias = body.get("alias")
    category = body.get("category")
    if category == "":
        category = None
    if not isinstance(label, str) or not label.strip():
        raise HTTPException(status_code=400, detail="'label' is required")
    if not isinstance(command, str) or not command.strip():
        raise HTTPException(status_code=400, detail="'command' is required")
    if not isinstance(raw, bool):
        raise HTTPException(status_code=400, detail="'raw' must be a boolean")
    if not isinstance(alias, str) or not alias.strip():
        raise HTTPException(status_code=400, detail="'alias' is required")
    _api_device_for_alias(alias.strip())
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
        alias=alias.strip(),
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
def api_list_macros(request: Request, alias: str | None = None) -> list[dict]:
    """List macros, optionally showing those applicable to one interface."""
    _check_api_token(_bearer(request))
    macros = load_macros()
    if alias is not None:
        _api_device_for_alias(alias)
        macros = [macro for macro in macros if macro.alias == alias]
    else:
        macros = [macro for macro in macros if macro.alias is not None]
    return [_api_macro_payload(macro) for macro in macros]


@app.get("/api/aliases/{alias}/macros")
def api_list_alias_macros(alias: str, request: Request) -> list[dict]:
    """List macros applicable to the interface identified by its alias."""
    _check_api_token(_bearer(request))
    _api_device_for_alias(alias)
    macros = [
        macro
        for macro in load_macros()
        if macro.alias == alias
    ]
    return [_api_macro_payload(macro) for macro in macros]


@app.post("/api/macros", status_code=201)
async def api_create_macro(request: Request) -> dict:
    _check_api_token(_bearer(request))
    macro = _api_macro_from_body(await request.json())
    if any(
        existing.alias == macro.alias and existing.label == macro.label
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
    old_alias = body.get("old_alias")
    if not isinstance(old_label, str) or not old_label.strip():
        raise HTTPException(status_code=400, detail="'old_label' is required")
    if old_alias is not None and not isinstance(old_alias, str):
        raise HTTPException(status_code=400, detail="'old_alias' must be a string or null")
    macro = _api_macro_from_body(body)
    existing = load_macros()
    if not any(
        item.label == old_label.strip() and item.alias == old_alias
        for item in existing
    ):
        raise HTTPException(status_code=404, detail="Macro not found")
    if any(
        item.label == macro.label
        and item.alias == macro.alias
        and (item.label != old_label.strip() or item.alias != old_alias)
        for item in existing
    ):
        raise HTTPException(status_code=409, detail="A macro with that label already exists")
    update_macro(old_label.strip(), old_alias, macro)
    _reload_api_macros()
    return _api_macro_payload(macro)


@app.delete("/api/macros/{label}")
def api_delete_macro(label: str, request: Request, alias: str) -> dict:
    _check_api_token(_bearer(request))
    _api_device_for_alias(alias)
    if not any(
        macro.label == label and macro.alias == alias for macro in load_macros()
    ):
        raise HTTPException(status_code=404, detail="Macro not found")
    remove_macro(label, alias)
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


register_api(_check_api_token, _bearer)


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
