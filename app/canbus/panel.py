"""NiceGUI CAN monitor and explicit DBC message generator."""

from __future__ import annotations

import json

from nicegui import ui

from panels import isolate_panel, nav, safe_timer
from ui_common import setup_dark_mode
from .api import can_manager
from .dbc import list_messages, message_preview
from .profiles import list_dbcs, list_profiles, save_profile


@ui.page("/can")
@isolate_panel("CAN")
def can_page() -> None:
    with ui.header().classes("items-center justify-between").props("bordered"):
        ui.label("RPI-SemiAutomator").classes("text-lg font-semibold")
        nav("can")
        with ui.row().classes("items-center"):
            setup_dark_mode()
            ui.button("Settings", icon="settings", on_click=lambda: ui.navigate.to("/settings")).props(
                "flat color=white"
            )

    selected_profile: dict[str, str | None] = {"id": None}
    sequence = {"value": 0, "dropped": 0}
    profile_options = {profile.id: profile.name for profile in list_profiles()}
    dbcs = list_dbcs()
    dbc_options = {dbc["id"]: dbc["name"] for dbc in dbcs}

    with ui.column().classes("w-full p-4 gap-3"):
        with ui.row().classes("items-center gap-2"):
            profile_select = ui.select(profile_options, label="CAN profile").classes("w-56")
            profile_name = ui.input("Profile name", value="CAN profile").classes("w-40")
            interface = ui.select(
                {"socketcan": "SocketCAN", "pcan": "PEAK PCAN", "vector": "Vector", "kvaser": "Kvaser", "virtual": "Virtual"},
                value="socketcan",
                label="Backend",
            ).classes("w-40")
            channel = ui.input("Channel", value="can0").classes("w-32")
            bitrate = ui.number("Bitrate", value=500000, min=10000, max=10000000).classes("w-32")
            fd = ui.switch("CAN FD")
            data_bitrate = ui.number("FD data rate", value=2000000, min=10000, max=20000000).classes("w-36")
            listen_only = ui.switch("Listen-only", value=True)
        with ui.row().classes("items-center gap-2"):
            dbc_select = ui.select(dbc_options, label="DBC", with_input=True).classes("w-56")

            async def upload_dbc(event) -> None:
                try:
                    content = await event.file.read()
                    from .profiles import save_dbc

                    uploaded = save_dbc(event.file.name, content)
                    dbc_select.options = {item["id"]: item["name"] for item in list_dbcs()}
                    dbc_select.value = uploaded["id"]
                    dbc_select.update()
                    ui.notify("DBC file validated and saved", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"DBC upload failed: {exc}", type="negative")

            ui.upload(
                label="Upload DBC (max 1 MiB)",
                on_upload=upload_dbc,
                auto_upload=True,
                max_file_size=1024 * 1024,
            ).props("accept=.dbc")
            status_label = ui.label()
            dropped_label = ui.label().classes("text-orange")

        with ui.row().classes("items-center gap-2"):
            def refresh_profiles() -> None:
                profiles = list_profiles()
                profile_select.options = {profile.id: profile.name for profile in profiles}
                profile_select.update()
                dbc_select.options = {item["id"]: item["name"] for item in list_dbcs()}
                dbc_select.update()

            def save_current_profile() -> None:
                try:
                    values = {
                        "name": profile_name.value or "CAN profile",
                        "interface": interface.value,
                        "channel": channel.value,
                        "bitrate": int(bitrate.value or 500000),
                        "fd": bool(fd.value),
                        "data_bitrate": int(data_bitrate.value or 2000000) if fd.value else None,
                        "listen_only": bool(listen_only.value),
                        "dbc_id": dbc_select.value or None,
                    }
                    current_id = selected_profile["id"] or profile_select.value
                    profile = save_profile(values, current_id) if current_id else save_profile(values)
                    selected_profile["id"] = profile.id
                    profile_name.value = profile.name
                    profile_options[profile.id] = profile.name
                    profile_select.value = profile.id
                    refresh_profiles()
                    ui.notify("CAN profile saved", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Could not save profile: {exc}", type="negative")

            def load_selected_profile() -> None:
                profile = next((item for item in list_profiles() if item.id == profile_select.value), None)
                if profile is None:
                    return
                selected_profile["id"] = profile.id
                interface.value = profile.interface
                channel.value = profile.channel
                bitrate.value = profile.bitrate
                fd.value = profile.fd
                data_bitrate.value = profile.data_bitrate or 2000000
                listen_only.value = profile.listen_only
                dbc_select.value = profile.dbc_id

            profile_select.on_value_change(lambda _: load_selected_profile())
            ui.button("Save profile", on_click=save_current_profile, icon="save")
            ui.button("Connect", on_click=lambda: connect_profile(), icon="power")
            ui.button("Disconnect", on_click=lambda: disconnect_profile(), icon="power_off").props("outline")
            transmit_switch = ui.switch("Enable transmission", value=False, on_change=lambda event: set_transmit(event.value))

        def connect_profile() -> None:
            try:
                profile = next((item for item in list_profiles() if item.id == profile_select.value), None)
                if profile is None:
                    ui.notify("Save a CAN profile first", type="warning")
                    return
                can_manager.connect(profile)
                sequence["value"] = can_manager.position
                sequence["dropped"] = 0
                frame_table.rows = []
                frame_table.update()
                ui.notify("CAN connected; transmission is disabled by default", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"CAN connection failed: {exc}", type="negative")

        def disconnect_profile() -> None:
            can_manager.disconnect()
            transmit_switch.value = False
            ui.notify("CAN disconnected", type="info")

        def set_transmit(enabled: bool) -> None:
            try:
                can_manager.set_transmit_enabled(enabled)
                ui.notify("CAN transmission enabled" if enabled else "CAN transmission disabled")
            except Exception as exc:  # noqa: BLE001
                transmit_switch.value = False
                ui.notify(str(exc), type="warning")

        with ui.card().classes("w-full"):
            ui.label("Received frames").classes("text-lg font-bold")
            frame_table = ui.table(
                columns=[
                    {"name": "timestamp", "label": "Time", "field": "timestamp"},
                    {"name": "id", "label": "ID", "field": "id"},
                    {"name": "format", "label": "Format", "field": "format"},
                    {"name": "dlc", "label": "DLC", "field": "dlc"},
                    {"name": "data", "label": "Data", "field": "data"},
                ],
                rows=[],
                row_key="timestamp",
            ).classes("w-full")

        with ui.card().classes("w-full gap-2"):
            ui.label("DBC message generator").classes("text-lg font-bold")
            message_select = ui.select({}, label="Message").classes("w-64")
            signals_input = ui.textarea(
                "Signal values as JSON", placeholder='{"Signal": 1}'
            ).classes("w-full")
            preview_label = ui.label("Preview: no message prepared").classes("font-mono")
            preview_state: dict[str, dict | None] = {"message": None}

            def refresh_messages() -> None:
                if not dbc_select.value:
                    message_select.options = {}
                    message_select.update()
                    return
                try:
                    messages = list_messages(dbc_select.value)
                    message_select.options = {item["name"]: item["name"] for item in messages}
                    message_select.update()
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Could not load DBC: {exc}", type="negative")

            dbc_select.on_value_change(lambda _: refresh_messages())

            def prepare_message() -> None:
                try:
                    profile_id = selected_profile["id"] or profile_select.value
                    profile = next((item for item in list_profiles() if item.id == profile_id), None)
                    if profile is None:
                        raise ValueError("Select a CAN profile")
                    if dbc_select.value != profile.dbc_id:
                        raise ValueError("The DBC must be saved in the profile")
                    signals = json.loads(signals_input.value or "{}")
                    preview = message_preview(profile, message_select.value, signals)
                    preview_state["message"] = preview
                    preview_label.set_text(
                        f"Preview: ID 0x{preview['arbitration_id']:X} "
                        f"{'FD' if preview['is_fd'] else 'Classic'} "
                        f"DLC {preview['dlc']} | {preview['data']}"
                    )
                except Exception as exc:  # noqa: BLE001
                    preview_state["message"] = None
                    ui.notify(f"Preview failed: {exc}", type="warning")

            def send_prepared() -> None:
                preview = preview_state["message"]
                if preview is None:
                    ui.notify("Create a valid transmission preview first", type="warning")
                    return
                try:
                    can_manager.send(
                        preview["arbitration_id"],
                        bytes.fromhex(preview["data"]),
                        preview["is_extended_id"],
                        preview["is_fd"],
                    )
                    ui.notify("CAN message sent", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"Send failed: {exc}", type="negative")

            with ui.row().classes("gap-2"):
                ui.button("Create preview", on_click=prepare_message, icon="visibility").props("outline")
                ui.button("Send preview", on_click=send_prepared, icon="send")

        def refresh_frames() -> None:
            status = can_manager.status()
            status_label.set_text(
                f"{status.mode} · {'connected' if status.connected else 'disconnected'}"
                + (f" · Error: {status.error}" if status.error else "")
            )
            dropped_label.set_text(f"Dropped frames: {status.dropped_frames}")
            frames, current, client_dropped = can_manager.frames(sequence["value"], 100)
            sequence["value"] = current
            if client_dropped:
                sequence["dropped"] += client_dropped
            if sequence["dropped"]:
                dropped_label.set_text(
                    f"Dropped frames: {status.dropped_frames} · skipped by UI: {sequence['dropped']}"
                )
            if frames:
                rows = list(frame_table.rows)
                rows.extend(
                    {
                        "timestamp": frame["timestamp"],
                        "id": f"0x{frame['arbitration_id']:X}",
                        "format": "ERROR" if frame["is_error_frame"] else (
                            "FD" if frame["is_fd"] else "Classic"
                        ),
                        "dlc": frame["dlc"],
                        "data": frame["data"],
                    }
                    for frame in frames
                )
                frame_table.rows = rows[-200:]
                frame_table.update()

        refresh_messages()
        safe_timer(0.2, refresh_frames, "CAN")
