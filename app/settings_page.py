"""Settings page for saved serial ports and macros."""
from fastapi import HTTPException
from nicegui import ui

from config import (
    Macro,
    PortPreset,
    backup_settings,
    load_favorites,
    load_macros,
    remove_favorite,
    remove_macro,
    save_favorite,
    update_macro,
)


def build_settings_page(
    setup_dark_mode,
    favorites: list[PortPreset],
    user_macros: list[Macro],
    device_options,
    preset_name,
    device_aliases,
    api_device_for_alias,
) -> None:
    ui.add_head_html(
        "<style>html, body, #app, .nicegui-content { height: 100%; }</style>"
    )

    with ui.header().classes("items-center justify-between").props("bordered"):
        with ui.row().classes("items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat color=white round"
            )
            ui.label("Settings").classes("text-lg font-semibold")
        setup_dark_mode()

    with ui.column().classes("w-full max-w-2xl mx-auto gap-4 p-4"):
        ui.label("Add favorite").classes("text-lg font-bold")
        with ui.row().classes("w-full items-end gap-2"):
            device_select = ui.select(
                options=device_options(), label="Serial port", with_input=True
            ).classes("flex-grow")
            new_name_input = ui.input(label="Name").classes("w-40")
            new_baud_input = ui.number(
                label="Baud rate", value=115200, min=110, max=4000000
            ).classes("w-32")

        favorites_list_container = ui.column().classes("w-full gap-1")

        def render_favorites_list() -> None:
            device_select.options = device_options()
            device_select.update()
            favorites_list_container.clear()
            if not favorites:
                with favorites_list_container:
                    ui.label("No favorites.").classes("text-sm text-grey-6")
                return
            with favorites_list_container:
                for favorite in list(favorites):
                    with ui.row().classes("items-center w-full justify-between"):
                        ui.label(
                            f"{favorite.name} — {favorite.device} "
                            f"({favorite.baudrate} baud)"
                        ).classes("text-sm")

                        def make_edit(fav: PortPreset = favorite):
                            def _edit() -> None:
                                with ui.dialog() as dialog, ui.card():
                                    ui.label("Edit favorite").classes("text-lg font-bold")
                                    name_input = ui.input(label="Name", value=fav.name).classes("w-64")
                                    baud_input = ui.number(
                                        label="Baud rate", value=fav.baudrate, min=110, max=4000000
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
                                        ui.notify(f"'{name}' saved", type="positive")

                                    with ui.row().classes("justify-end w-full gap-2"):
                                        ui.button("Cancel", on_click=dialog.close).props("flat")
                                        ui.button("Save", on_click=save, icon="save")
                                dialog.open()

                            return _edit

                        def make_remove(dev: str = favorite.device):
                            def _remove() -> None:
                                remove_favorite(dev)
                                favorites.clear()
                                favorites.extend(load_favorites())
                                render_favorites_list()
                                ui.notify(f"{dev} removed from favorites", type="info")

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
                ui.notify("Select a serial port", type="warning")
                return
            try:
                name = (new_name_input.value or preset_name(device)).strip() or device
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
                ui.notify(f"'{name}' saved as a favorite", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(
                    f"Could not save favorite: {exc}", type="negative"
                )

        ui.button("Add favorite", on_click=add_favorite, icon="star").props("outline")

        ui.separator()
        ui.label("Saved favorites").classes("text-lg font-bold")
        render_favorites_list()

        ui.separator()
        ui.label("Macros").classes("text-lg font-bold")
        macros_list_container = ui.column().classes("w-full gap-1")

        def render_macros_list() -> None:
            macros_list_container.clear()
            with macros_list_container:
                if not user_macros:
                    ui.label("No macros.").classes("text-sm text-grey-6")
                    return
                for macro in list(user_macros):
                    scope = macro.alias or "Unassigned alias"
                    with ui.row().classes("items-center w-full justify-between"):
                        ui.label(f"{macro.label} — {macro.command} ({scope})").classes(
                            "text-sm"
                        )

                        def make_edit_macro(m: Macro = macro):
                            def _edit() -> None:
                                with ui.dialog() as dialog, ui.card():
                                    ui.label("Edit macro").classes("text-lg font-bold")
                                    name_input = ui.input(label="Name", value=m.label).classes("w-64")
                                    command_input = ui.input(
                                        label="Command", value=m.command
                                    ).classes("w-64")
                                    category_input = ui.input(
                                        label="Folder (use / for nesting)",
                                        value=m.category or "",
                                    ).classes("w-64")
                                    alias_options = {
                                        alias: alias for alias in device_aliases().values()
                                    }
                                    if m.alias and m.alias not in alias_options:
                                        alias_options[m.alias] = m.alias
                                    alias_select = ui.select(
                                        options=alias_options,
                                        label="Alias",
                                        value=m.alias,
                                    ).classes("w-64")

                                    def save() -> None:
                                        name = (name_input.value or "").strip()
                                        command = (command_input.value or "").strip()
                                        if not name or not command:
                                            ui.notify(
                                                "Name and command are required",
                                                type="warning",
                                            )
                                            return
                                        alias = alias_select.value
                                        if not alias:
                                            ui.notify("An alias is required", type="warning")
                                            return
                                        try:
                                            api_device_for_alias(alias)
                                        except HTTPException:
                                            ui.notify("The alias is ambiguous", type="warning")
                                            return
                                        update_macro(
                                            m.label,
                                            m.alias,
                                            Macro(
                                                label=name,
                                                command=command,
                                                raw=m.raw,
                                                alias=alias,
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
                                        ui.notify(f"Macro '{name}' saved", type="positive")

                                    with ui.row().classes("justify-end w-full gap-2"):
                                        ui.button("Cancel", on_click=dialog.close).props("flat")
                                        ui.button("Save", on_click=save, icon="save")
                                dialog.open()

                            return _edit

                        def make_delete_macro(m: Macro = macro):
                            def _delete() -> None:
                                remove_macro(m.label, m.alias)
                                user_macros.clear()
                                user_macros.extend(load_macros())
                                render_macros_list()
                                ui.notify(f"Macro '{m.label}' deleted", type="info")

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
        ui.label("Data").classes("text-lg font-bold")
        ui.label(
            "Back up favorites and custom macros, or reload them after external changes."
        ).classes("text-sm text-grey-6")

        def backup() -> None:
            content = backup_settings()
            ui.download(
                content, "rpi-semiautomator-backup.yaml", media_type="application/x-yaml"
            )
            ui.notify("Backup downloaded", type="positive")

        def reload_settings() -> None:
            favorites.clear()
            favorites.extend(load_favorites())
            user_macros.clear()
            user_macros.extend(load_macros())
            render_favorites_list()
            render_macros_list()
            ui.notify("Settings reloaded", type="positive")

        with ui.row().classes("gap-2"):
            ui.button("Download backup", on_click=backup, icon="download").props(
                "outline"
            )
            ui.button(
                "Reload settings", on_click=reload_settings, icon="refresh"
            ).props("outline")
