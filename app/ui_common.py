"""Shared UI helpers."""
from nicegui import app, ui


def setup_dark_mode() -> None:
    """Apply the persisted dark-mode choice and add a toggle to the header."""
    dark = ui.dark_mode(bool(app.storage.user.get("dark", False)))

    def toggle() -> None:
        dark.toggle()
        app.storage.user["dark"] = bool(dark.value)

    ui.button(icon="dark_mode", on_click=toggle).props("flat color=white round").tooltip(
        "Hell/Dunkel umschalten"
    )
