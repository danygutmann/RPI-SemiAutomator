"""Shared panel navigation and header components."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable

from nicegui import ui

from ui_common import setup_dark_mode

logger = logging.getLogger(__name__)


def nav(active: str) -> None:
    """Navigation tabs, to be placed inside the page header."""
    with ui.row().classes("items-center gap-0"):
        for key, label, target in (
            ("serial", "Serial", "/"),
            ("can", "CAN", "/can"),
            ("dbus", "D-Bus", "/dbus"),
            ("relay", "Relay", "/relay"),
            ("psu", "Power supply", "/psu"),
        ):
            button = ui.button(label, on_click=lambda t=target: ui.navigate.to(t)).props(
                "flat color=white"
            )
            if key == active:
                button.classes("bg-white/20")


def isolate_panel(name: str) -> Callable:
    """Keep a panel rendering failure local to that page."""

    def decorate(render: Callable) -> Callable:
        @wraps(render)
        def guarded(*args, **kwargs):
            try:
                return render(*args, **kwargs)
            except Exception:
                logger.exception("%s panel failed while rendering", name)
                with ui.column().classes("w-full p-4"):
                    ui.label(f"{name} panel failed to load. See the server log for details.").classes(
                        "text-negative"
                    )

        return guarded

    return decorate


def safe_timer(interval: float, callback: Callable, panel: str, *, once: bool = False):
    """Stop a failing panel timer and report its error without affecting other pages."""
    timer_ref = {"timer": None}
    notified = {"value": False}

    def guarded() -> None:
        try:
            callback()
        except Exception:
            logger.exception("%s panel background update failed", panel)
            timer = timer_ref["timer"]
            if timer is not None:
                timer.cancel()
            if not notified["value"]:
                notified["value"] = True
                try:
                    ui.notify(f"{panel} panel update failed. See the server log for details.", type="negative")
                except Exception:
                    logger.exception("Could not display %s panel error notification", panel)

    timer_ref["timer"] = ui.timer(interval, guarded, once=once)
    return timer_ref["timer"]


def header(active: str) -> None:
    with ui.header().classes("items-center justify-between").props("bordered"):
        ui.label("RPI-SemiAutomator").classes("text-lg font-semibold")
        nav(active)
        with ui.row().classes("items-center"):
            setup_dark_mode()
            ui.button(
                "Settings", icon="settings", on_click=lambda: ui.navigate.to("/settings")
            ).props("flat color=white")
