"""TCU status, manual D-Bus calls, introspection, and live event page."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from nicegui import ui

from ..core.runtime import config, tcu_service
from ..events.models import TCUEvent
from ...panels import nav
from ...ui_common import setup_dark_mode

MAX_VISIBLE_EVENTS = 200


@ui.page("/tcu")
def tcu_page() -> None:
    state: dict[str, object] = {
        "events": [],
        "last_event_id": None,
        "request_id": None,
    }

    with ui.header().classes("items-center justify-between").props("bordered"):
        ui.label("RPI-SemiAutomator").classes("text-lg font-semibold")
        nav("tcu")
        with ui.row().classes("items-center"):
            setup_dark_mode()
            ui.button(
                "Einstellungen", icon="settings", on_click=lambda: ui.navigate.to("/settings")
            ).props("flat color=white")

    with ui.column().classes("w-full p-4 gap-3"):
        with ui.card().classes("w-full"):
            ui.label("Verbindung").classes("text-lg font-bold")
            status_label = ui.label()
            ui.label(f"Transport: {config.transport_label}").classes("text-sm text-grey-7")
            with ui.row().classes("items-center gap-2"):
                ui.button("Verbinden", icon="power", on_click=lambda: connect())
                ui.button("Trennen", icon="power_off", on_click=lambda: disconnect()).props(
                    "outline"
                )
                changed_label = ui.label()

        with ui.card().classes("w-full"):
            ui.label("D-Bus-Methodenaufruf").classes("text-lg font-bold")
            with ui.row().classes("w-full gap-2"):
                service_input = ui.input("Service").classes("flex-1")
                path_input = ui.input("Object Path", value="/").classes("flex-1")
                interface_input = ui.input("Interface").classes("flex-1")
                method_input = ui.input("Methode").classes("flex-1")
            args_input = ui.textarea("Argumente (JSON-Array)", value="[]").classes("w-full")
            with ui.row().classes("items-center gap-2"):
                timeout_input = ui.number(
                    "Timeout (Sekunden)",
                    value=config.timeout_seconds,
                    min=0.1,
                    max=60,
                    step=0.1,
                ).classes("w-48")
                ui.button(
                    "Aufruf starten", icon="play_arrow", on_click=lambda: invoke()
                )
                call_status = ui.label("Kein Aufruf gestartet")
            result_output = ui.textarea("Letztes Ergebnis").props("readonly").classes("w-full")

        with ui.card().classes("w-full"):
            ui.label("Introspection").classes("text-lg font-bold")
            with ui.row().classes("items-center gap-2"):
                introspection_service = ui.input("Service").classes("flex-1")
                introspection_path = ui.input("Object Path", value="/").classes("flex-1")
                ui.button(
                    "Introspect", icon="account_tree", on_click=lambda: introspect()
                )
            introspection_output = ui.textarea("Interfaces, Methoden und Signaturen").props(
                "readonly"
            ).classes("w-full")

        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Live-Ereignisse").classes("text-lg font-bold")
                ui.button("Historie leeren", icon="delete", on_click=lambda: clear_events()).props(
                    "outline"
                )
            event_table = ui.table(
                columns=[
                    {"name": "timestamp", "label": "Zeit", "field": "timestamp"},
                    {"name": "event_type", "label": "Ereignis", "field": "event_type"},
                    {"name": "request_id", "label": "Request-ID", "field": "request_id"},
                    {"name": "payload", "label": "Daten", "field": "payload"},
                ],
                rows=[],
                row_key="event_id",
            ).classes("w-full")

    def show_status() -> None:
        status = tcu_service.status()
        status_label.set_text(
            f"{'Verbunden' if status['connected'] else 'Getrennt'}"
            + (f" · Fehler: {status['error']}" if status.get("error") else "")
        )
        changed_label.set_text(f"Letzte Änderung: {status['last_changed_at']}")

    async def connect() -> None:
        try:
            await tcu_service.connect()
            ui.notify("TCU-D-Bus verbunden", type="positive")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        show_status()

    async def disconnect() -> None:
        try:
            await tcu_service.disconnect()
            ui.notify("TCU-D-Bus getrennt", type="info")
        except Exception as exc:
            ui.notify(str(exc), type="negative")
        show_status()

    async def invoke() -> None:
        try:
            arguments = json.loads(args_input.value or "[]")
            if not isinstance(arguments, list):
                raise ValueError("Argumente müssen ein JSON-Array sein")
            record = tcu_service.submit_call(
                service_input.value or "",
                path_input.value or "",
                interface_input.value or "",
                method_input.value or "",
                arguments,
                timeout_input.value,
            )
            state["request_id"] = record.request_id
            call_status.set_text(f"Angenommen · Request-ID: {record.request_id}")
            result_output.value = json.dumps(record.to_dict(), ensure_ascii=False, indent=2)
        except Exception as exc:
            call_status.set_text("Request abgewiesen")
            ui.notify(str(exc), type="negative")

    async def introspect() -> None:
        try:
            xml = await tcu_service.introspect(
                introspection_service.value or "", introspection_path.value or "/"
            )
            node = ET.fromstring(xml)
            lines = []
            for interface in node.findall("interface"):
                lines.append(f"Interface: {interface.get('name')}")
                for method in interface.findall("method"):
                    args = [
                        f"{arg.get('direction', 'in')}:{arg.get('type', '')}"
                        for arg in method.findall("arg")
                    ]
                    lines.append(f"  {method.get('name')}({', '.join(args)})")
                for signal in interface.findall("signal"):
                    args = [arg.get("type", "") for arg in signal.findall("arg")]
                    lines.append(f"  signal {signal.get('name')}({', '.join(args)})")
            introspection_output.value = "\n".join(lines) or xml
        except Exception as exc:
            introspection_output.value = f"Introspection fehlgeschlagen: {exc}"

    def clear_events() -> None:
        state["events"] = []
        history = tcu_service.events.history()
        state["last_event_id"] = history[-1].event_id if history else None
        event_table.rows = []
        event_table.update()

    def refresh() -> None:
        show_status()
        events = tcu_service.events.history()
        last_event_id = state["last_event_id"]
        if last_event_id is None:
            unseen = events[-20:]
        else:
            positions = [
                index
                for index, event in enumerate(events)
                if event.event_id == last_event_id
            ]
            unseen = events[positions[-1] + 1 :] if positions else events[-20:]
        for event in unseen:
            append_event(event)
            state["last_event_id"] = event.event_id
        request_id = state["request_id"]
        if request_id:
            record = tcu_service.get_call(str(request_id))
            if record is not None and record.status in {"completed", "failed"}:
                call_status.set_text(f"{record.status} · {record.duration_ms} ms")
                result_output.value = json.dumps(
                    record.to_dict(), ensure_ascii=False, indent=2
                )

    def append_event(event: TCUEvent) -> None:
        rows = list(state["events"])
        rows.append(
            {
                "event_id": event.event_id,
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "request_id": event.request_id or "",
                "payload": json.dumps(event.payload, ensure_ascii=False),
            }
        )
        state["events"] = rows[-MAX_VISIBLE_EVENTS:]
        event_table.rows = state["events"]
        event_table.update()

    show_status()
    ui.timer(0.25, refresh)
