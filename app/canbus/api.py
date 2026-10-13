"""REST and WebSocket endpoints for CAN control and monitoring."""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .dbc import list_messages, message_preview
from .manager import CANManager
from .profiles import (
    delete_profile,
    get_profile,
    list_dbcs,
    list_profiles,
    save_dbc,
    save_profile,
)

can_manager = CANManager()


async def _json_body(request: Request):
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON request body") from exc


def register_api(app: FastAPI, check_token, bearer) -> None:
    def auth(request: Request) -> None:
        check_token(bearer(request))

    @app.get("/api/can/profiles")
    def api_can_profiles(request: Request) -> list[dict]:
        auth(request)
        return [profile.to_dict() for profile in list_profiles()]

    @app.post("/api/can/profiles", status_code=201)
    async def api_can_save_profile(request: Request) -> dict:
        auth(request)
        return save_profile(await _json_body(request)).to_dict()

    @app.put("/api/can/profiles/{profile_id}")
    async def api_can_update_profile(profile_id: str, request: Request) -> dict:
        auth(request)
        body = await _json_body(request)
        if isinstance(body, dict) and "id" in body:
            if body["id"] != profile_id:
                raise HTTPException(status_code=400, detail="Profile ID does not match URL")
            body = {key: value for key, value in body.items() if key != "id"}
        return save_profile(body, profile_id).to_dict()

    @app.delete("/api/can/profiles/{profile_id}")
    def api_can_delete_profile(profile_id: str, request: Request) -> dict:
        auth(request)
        if can_manager.profile and can_manager.profile.id == profile_id:
            raise HTTPException(status_code=409, detail="Disconnect the active CAN profile first")
        delete_profile(profile_id)
        return {"ok": True}

    @app.get("/api/can/status")
    def api_can_status(request: Request) -> dict:
        auth(request)
        return can_manager.status().to_dict()

    @app.post("/api/can/connect")
    async def api_can_connect(request: Request) -> dict:
        auth(request)
        body = await _json_body(request)
        profile_id = body.get("profile_id") if isinstance(body, dict) else None
        if not isinstance(profile_id, str):
            raise HTTPException(status_code=400, detail="'profile_id' is required")
        profile = get_profile(profile_id)
        try:
            return can_manager.connect(profile)
        except Exception as exc:  # noqa: BLE001 - driver errors are backend-specific
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/can/disconnect")
    def api_can_disconnect(request: Request) -> dict:
        auth(request)
        can_manager.disconnect()
        return can_manager.status().to_dict()

    @app.post("/api/can/transmit-mode")
    async def api_can_transmit_mode(request: Request) -> dict:
        auth(request)
        body = await _json_body(request)
        enabled = body.get("enabled") if isinstance(body, dict) else None
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="'enabled' must be a boolean")
        try:
            can_manager.set_transmit_enabled(enabled)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return can_manager.status().to_dict()

    @app.get("/api/can/frames")
    def api_can_frames(request: Request, after: int = 0, limit: int = 200) -> dict:
        auth(request)
        if after < 0 or not 1 <= limit <= 500:
            raise HTTPException(status_code=400, detail="Invalid frame cursor or limit")
        frames, sequence, dropped = can_manager.frames(after, limit)
        return {
            "frames": frames,
            "sequence": sequence,
            "dropped": dropped,
            "total_dropped": can_manager.status().dropped_frames,
        }

    @app.post("/api/can/send")
    async def api_can_send(request: Request) -> dict:
        auth(request)
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="A JSON object is required")
        arbitration_id = body.get("arbitration_id")
        data = body.get("data")
        extended = body.get("is_extended_id", False)
        fd = body.get("is_fd", False)
        if isinstance(arbitration_id, bool) or not isinstance(arbitration_id, int):
            raise HTTPException(status_code=400, detail="'arbitration_id' must be an integer")
        if not isinstance(data, str) or not isinstance(extended, bool) or not isinstance(fd, bool):
            raise HTTPException(status_code=400, detail="Invalid frame fields")
        try:
            payload = bytes.fromhex(data)
            return can_manager.send(arbitration_id, payload, extended, fd)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=f"CAN send failed: {exc}") from exc

    @app.get("/api/can/dbcs")
    def api_can_dbcs(request: Request) -> list[dict]:
        auth(request)
        return list_dbcs()

    @app.post("/api/can/dbcs", status_code=201)
    async def api_can_upload_dbc(request: Request, filename: str) -> dict:
        auth(request)
        content_length = request.headers.get("content-length")
        if content_length and (not content_length.isdigit() or int(content_length) > 1024 * 1024):
            raise HTTPException(status_code=413, detail="DBC upload exceeds 1 MiB")
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1024 * 1024:
                raise HTTPException(status_code=413, detail="DBC upload exceeds 1 MiB")
            chunks.append(chunk)
        return save_dbc(filename, b"".join(chunks))

    @app.get("/api/can/dbcs/{dbc_id}/messages")
    def api_can_dbc_messages(dbc_id: str, request: Request) -> list[dict]:
        auth(request)
        return list_messages(dbc_id)

    @app.post("/api/can/dbc/preview")
    async def api_can_dbc_preview(request: Request) -> dict:
        auth(request)
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="A JSON object is required")
        profile_id = body.get("profile_id")
        message_name = body.get("message")
        if not isinstance(profile_id, str) or not isinstance(message_name, str):
            raise HTTPException(status_code=400, detail="'profile_id' and 'message' are required")
        profile = get_profile(profile_id)
        return message_preview(profile, message_name, body.get("signals", {}))

    @app.post("/api/can/dbc/send")
    async def api_can_dbc_send(request: Request) -> dict:
        auth(request)
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="A JSON object is required")
        profile_id = body.get("profile_id")
        message_name = body.get("message")
        if not isinstance(profile_id, str) or not isinstance(message_name, str):
            raise HTTPException(status_code=400, detail="'profile_id' and 'message' are required")
        profile = get_profile(profile_id)
        if can_manager.profile is None or can_manager.profile.id != profile.id:
            raise HTTPException(status_code=409, detail="Connect the selected CAN profile first")
        preview = message_preview(profile, message_name, body.get("signals", {}))
        try:
            result = can_manager.send(
                preview["arbitration_id"],
                bytes.fromhex(preview["data"]),
                preview["is_extended_id"],
                preview["is_fd"],
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=f"CAN send failed: {exc}") from exc
        return {"preview": preview, **result}

    @app.websocket("/api/can/ws")
    async def api_can_ws(websocket: WebSocket, token: str | None = None) -> None:
        authorization = websocket.headers.get("authorization", "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else token
        try:
            check_token(supplied)
        except HTTPException:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cursor = 0
        try:
            while True:
                frames, cursor, dropped = can_manager.frames(cursor, 100)
                if dropped:
                    await websocket.send_json({"event": "overflow", "dropped": dropped})
                for frame in frames:
                    await websocket.send_json({"event": "frame", **frame})
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            return
