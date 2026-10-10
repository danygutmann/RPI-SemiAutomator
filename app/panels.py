"""Dummy control panels (8 channel relay card, lab power supply) with API.

The real hardware control logic is not implemented yet. State is kept in
memory and exposed read/write through the REST API and the NiceGUI pages so
the actual drivers can be plugged in later by replacing the ``_apply_*``
hooks.
"""
from __future__ import annotations

import threading
from typing import Callable

from fastapi import HTTPException, Request
from nicegui import app, ui

RELAY_CHANNELS = 8
PSU_MAX_CURRENT = 40.0
PSU_MAX_VOLTAGE = 12.0

_lock = threading.Lock()
relay_state: dict = {
    "channels": [
        {"channel": i, "alias": f"K{i}", "on": False} for i in range(1, RELAY_CHANNELS + 1)
    ]
}
psu_state: dict = {"output": False, "current_limit": 0.0, "voltage": 0.0, "measured_current": 0.0}


def _apply_relay(channel: dict) -> None:
    """Hook: drive the real relay card (not implemented yet)."""


def _apply_psu() -> None:
    """Hook: drive the real power supply (not implemented yet)."""
    # Dummy: pretend the load draws the limit when the output is enabled.
    psu_state["measured_current"] = psu_state["current_limit"] if psu_state["output"] else 0.0


def _find_channel(ref: str) -> dict:
    for ch in relay_state["channels"]:
        if str(ch["channel"]) == ref or ch["alias"].lower() == ref.lower():
            return ch
    raise HTTPException(status_code=404, detail=f"Unknown relay channel/alias: {ref}")


def set_channel(ref: str, on: bool | None = None, alias: str | None = None) -> dict:
    with _lock:
        ch = _find_channel(ref)
        if alias is not None:
            alias = alias.strip()
            if not alias:
                raise HTTPException(status_code=400, detail="alias must not be empty")
            if any(
                o["alias"].lower() == alias.lower() and o is not ch
                for o in relay_state["channels"]
            ):
                raise HTTPException(status_code=409, detail="alias already in use")
            ch["alias"] = alias
        if on is not None:
            ch["on"] = bool(on)
            _apply_relay(ch)
        return dict(ch)


def set_all(on: bool) -> list[dict]:
    with _lock:
        for ch in relay_state["channels"]:
            ch["on"] = on
            _apply_relay(ch)
        return [dict(c) for c in relay_state["channels"]]


def update_psu(
    output: bool | None = None,
    current_limit: float | None = None,
    voltage: float | None = None,
) -> dict:
    if current_limit is not None and not 0 <= current_limit <= PSU_MAX_CURRENT:
        raise HTTPException(status_code=400, detail=f"current_limit must be 0..{PSU_MAX_CURRENT}")
    if voltage is not None and not 0 <= voltage <= PSU_MAX_VOLTAGE:
        raise HTTPException(status_code=400, detail=f"voltage must be 0..{PSU_MAX_VOLTAGE}")
    with _lock:
        if output is not None:
            psu_state["output"] = bool(output)
        if current_limit is not None:
            psu_state["current_limit"] = float(current_limit)
        if voltage is not None:
            psu_state["voltage"] = float(voltage)
        _apply_psu()
        return dict(psu_state)


def nav(active: str) -> None:
    """Navigation tabs, to be placed inside the page header."""
    with ui.row().classes("items-center gap-0"):
        for key, label, target in (
            ("serial", "Seriell", "/"),
            ("relay", "Relais", "/relay"),
            ("psu", "Netzteil", "/psu"),
        ):
            button = ui.button(label, on_click=lambda t=target: ui.navigate.to(t)).props(
                "flat color=white"
            )
            if key == active:
                button.classes("bg-white/20")


def _header(active: str) -> None:
    with ui.header().classes("items-center justify-between").props("bordered"):
        ui.label("RPI-SemiAutomator").classes("text-lg font-semibold")
        nav(active)
        ui.button(
            "Einstellungen", icon="settings", on_click=lambda: ui.navigate.to("/settings")
        ).props("flat color=white")


@ui.page("/relay")
def relay_page() -> None:
    _header("relay")
    with ui.column().classes("w-full p-4 gap-3"):
        with ui.row().classes("items-center gap-2"):
            ui.button("Alle an", icon="power", on_click=lambda: set_all(True))
            ui.button("Alle aus", icon="power_off", on_click=lambda: set_all(False)).props("outline")
        switches: dict[int, ui.switch] = {}
        with ui.grid(columns=2).classes("gap-2"):
            for ch in relay_state["channels"]:
                with ui.card().classes("p-3"):
                    with ui.row().classes("items-center gap-3"):
                        ui.label(f"Kanal {ch['channel']}").classes("font-bold")
                        ui.input(
                            "Alias",
                            value=ch["alias"],
                            on_change=lambda e, n=ch["channel"]: (
                                set_channel(str(n), alias=e.value) if e.value and e.value.strip() else None
                            ),
                        ).props("dense")
                        switches[ch["channel"]] = ui.switch(
                            value=ch["on"],
                            on_change=lambda e, n=ch["channel"]: set_channel(str(n), on=e.value),
                        )

        def sync() -> None:
            for ch in relay_state["channels"]:
                sw = switches[ch["channel"]]
                if sw.value != ch["on"]:
                    sw.value = ch["on"]

        ui.timer(1.0, sync)


@ui.page("/psu")
def psu_page() -> None:
    _header("psu")
    with ui.column().classes("w-full p-4 gap-3 max-w-xl"):
        output = ui.switch("Ausgang", value=psu_state["output"], on_change=lambda e: update_psu(output=e.value))
        current = ui.number(
            "Strom-Limit (A)", value=psu_state["current_limit"], min=0, max=PSU_MAX_CURRENT, step=0.1,
            on_change=lambda e: e.value is not None and 0 <= e.value <= PSU_MAX_CURRENT
            and update_psu(current_limit=e.value),
        )
        voltage = ui.number(
            "Spannung (V)", value=psu_state["voltage"], min=0, max=PSU_MAX_VOLTAGE, step=0.1,
            on_change=lambda e: e.value is not None and 0 <= e.value <= PSU_MAX_VOLTAGE
            and update_psu(voltage=e.value),
        )
        measured = ui.label()

        def sync() -> None:
            measured.set_text(f"Aktueller Strom: {psu_state['measured_current']:.2f} A")
            if output.value != psu_state["output"]:
                output.value = psu_state["output"]

        sync()
        ui.timer(1.0, sync)


def register_api(check_token: Callable, bearer: Callable) -> None:
    def auth(request: Request) -> None:
        check_token(bearer(request))

    @app.get("/api/relay")
    def api_relay(request: Request) -> list[dict]:
        auth(request)
        return [dict(c) for c in relay_state["channels"]]

    @app.post("/api/relay/all")
    async def api_relay_all(request: Request) -> list[dict]:
        auth(request)
        body = await request.json()
        if not isinstance(body.get("on"), bool):
            raise HTTPException(status_code=400, detail="'on' (bool) is required")
        return set_all(body["on"])

    @app.get("/api/relay/{ref}")
    def api_relay_get(ref: str, request: Request) -> dict:
        auth(request)
        return dict(_find_channel(ref))

    @app.put("/api/relay/{ref}")
    async def api_relay_put(ref: str, request: Request) -> dict:
        auth(request)
        body = await request.json()
        on, alias = body.get("on"), body.get("alias")
        if on is not None and not isinstance(on, bool):
            raise HTTPException(status_code=400, detail="'on' must be bool")
        if alias is not None and not isinstance(alias, str):
            raise HTTPException(status_code=400, detail="'alias' must be string")
        return set_channel(ref, on=on, alias=alias)

    @app.get("/api/psu")
    def api_psu(request: Request) -> dict:
        auth(request)
        return dict(psu_state)

    @app.put("/api/psu")
    async def api_psu_put(request: Request) -> dict:
        auth(request)
        body = await request.json()
        kwargs = {}
        for key, typ in (("output", bool), ("current_limit", (int, float)), ("voltage", (int, float))):
            if key in body:
                value = body[key]
                if isinstance(value, bool) != (typ is bool) or not isinstance(value, typ):
                    raise HTTPException(status_code=400, detail=f"invalid '{key}'")
                kwargs[key] = value
        return update_psu(**kwargs)
