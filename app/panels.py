"""Shared panel navigation and header components."""

from __future__ import annotations

from nicegui import ui

from ui_common import setup_dark_mode


def nav(active: str) -> None:
    """Navigation tabs, to be placed inside the page header."""
    with ui.row().classes("items-center gap-0"):
        for key, label, target in (
            ("serial", "Seriell", "/"),
            ("can", "CAN", "/can"),
            ("tcu", "TCU", "/tcu"),
            ("relay", "Relais", "/relay"),
            ("psu", "Netzteil", "/psu"),
        ):
            button = ui.button(label, on_click=lambda t=target: ui.navigate.to(t)).props(
                "flat color=white"
            )
            if key == active:
                button.classes("bg-white/20")


def header(active: str) -> None:
    with ui.header().classes("items-center justify-between").props("bordered"):
        ui.label("RPI-SemiAutomator").classes("text-lg font-semibold")
        nav(active)
        with ui.row().classes("items-center"):
            setup_dark_mode()
            ui.button(
                "Einstellungen", icon="settings", on_click=lambda: ui.navigate.to("/settings")
            ).props("flat color=white")
