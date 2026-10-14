"""Power-supply control panel and API."""

from __future__ import annotations

import threading
from typing import Callable

from fastapi import HTTPException, Request
from nicegui import app, ui

from panels import header

PSU_MAX_CURRENT = 40.0
PSU_MAX_VOLTAGE = 12.0

_lock = threading.Lock()
psu_state: dict = {
    "output": False,
    "current_limit": 0.0,
    "voltage": 0.0,
    "measured_current": 0.0,
}


def _apply_psu() -> None:
    """Hook: drive the real power supply (not implemented yet)."""
    psu_state["measured_current"] = psu_state["current_limit"] if psu_state["output"] else 0.0


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


@ui.page("/psu")
def psu_page() -> None:
    header("psu")
    with ui.column().classes("w-full p-4 gap-3 max-w-xl"):
        output = ui.switch(
            "Ausgang",
            value=psu_state["output"],
            on_change=lambda event: update_psu(output=event.value),
        )
        current = ui.number(
            "Strom-Limit (A)",
            value=psu_state["current_limit"],
            min=0,
            max=PSU_MAX_CURRENT,
            step=0.1,
            on_change=lambda event: event.value is not None
            and 0 <= event.value <= PSU_MAX_CURRENT
            and update_psu(current_limit=event.value),
        )
        voltage = ui.number(
            "Spannung (V)",
            value=psu_state["voltage"],
            min=0,
            max=PSU_MAX_VOLTAGE,
            step=0.1,
            on_change=lambda event: event.value is not None
            and 0 <= event.value <= PSU_MAX_VOLTAGE
            and update_psu(voltage=event.value),
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
