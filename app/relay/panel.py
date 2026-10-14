"""Relay control panel and API."""

from __future__ import annotations

import threading
from typing import Callable

from fastapi import HTTPException, Request
from nicegui import app, ui

from panels import header, isolate_panel, safe_timer

RELAY_CHANNELS = 8

_lock = threading.Lock()
relay_state: dict = {
    "channels": [
        {"channel": i, "alias": f"K{i}", "on": False} for i in range(1, RELAY_CHANNELS + 1)
    ]
}


def _apply_relay(channel: dict) -> None:
    """Hook: drive the real relay card (not implemented yet)."""


def _find_channel(ref: str) -> dict:
    for channel in relay_state["channels"]:
        if str(channel["channel"]) == ref or channel["alias"].lower() == ref.lower():
            return channel
    raise HTTPException(status_code=404, detail=f"Unknown relay channel/alias: {ref}")


def set_channel(ref: str, on: bool | None = None, alias: str | None = None) -> dict:
    with _lock:
        channel = _find_channel(ref)
        if alias is not None:
            alias = alias.strip()
            if not alias:
                raise HTTPException(status_code=400, detail="alias must not be empty")
            if any(
                other["alias"].lower() == alias.lower() and other is not channel
                for other in relay_state["channels"]
            ):
                raise HTTPException(status_code=409, detail="alias already in use")
            channel["alias"] = alias
        if on is not None:
            channel["on"] = bool(on)
            _apply_relay(channel)
        return dict(channel)


def set_all(on: bool) -> list[dict]:
    with _lock:
        for channel in relay_state["channels"]:
            channel["on"] = on
            _apply_relay(channel)
        return [dict(channel) for channel in relay_state["channels"]]


@ui.page("/relay")
@isolate_panel("Relay")
def relay_page() -> None:
    header("relay")
    with ui.column().classes("w-full p-4 gap-3"):
        with ui.row().classes("items-center gap-2"):
            ui.button("All on", icon="power", on_click=lambda: set_all(True))
            ui.button("All off", icon="power_off", on_click=lambda: set_all(False)).props("outline")
        switches: dict[int, ui.switch] = {}
        with ui.grid(columns=2).classes("gap-2"):
            for channel in relay_state["channels"]:
                with ui.card().classes("p-3"):
                    with ui.row().classes("items-center gap-3"):
                        ui.label(f"Channel {channel['channel']}").classes("font-bold")
                        ui.input(
                            "Alias",
                            value=channel["alias"],
                            on_change=lambda event, number=channel["channel"]: (
                                set_channel(str(number), alias=event.value)
                                if event.value and event.value.strip()
                                else None
                            ),
                        ).props("dense")
                        switches[channel["channel"]] = ui.switch(
                            value=channel["on"],
                            on_change=lambda event, number=channel["channel"]: set_channel(
                                str(number), on=event.value
                            ),
                        )

        def sync() -> None:
            for channel in relay_state["channels"]:
                switch = switches[channel["channel"]]
                if switch.value != channel["on"]:
                    switch.value = channel["on"]

        safe_timer(1.0, sync, "Relay")


def register_api(check_token: Callable, bearer: Callable) -> None:
    def auth(request: Request) -> None:
        check_token(bearer(request))

    @app.get("/api/relay")
    def api_relay(request: Request) -> list[dict]:
        auth(request)
        return [dict(channel) for channel in relay_state["channels"]]

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
