"""TCU REST endpoints and event WebSocket."""

from __future__ import annotations

import asyncio
import json
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..core.runtime import tcu_service as default_service
from ..core.service import TCUService
from ..dbus.exceptions import TCUError
from .schemas import AcceptedCall, APIError, CallResult, MethodCallRequest, TCUStatus

MAX_CALL_BODY_BYTES = 256 * 1024


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def register_api(
    app: FastAPI,
    check_token: Callable,
    bearer: Callable,
    service: TCUService | None = None,
) -> None:
    backend = service or default_service

    def auth(request: Request) -> None:
        check_token(bearer(request))

    def handle_tcu_error(exc: TCUError) -> JSONResponse:
        return _error(exc.code, str(exc), exc.status_code)

    @app.get(
        "/api/v1/tcu/status",
        response_model=TCUStatus,
        responses={401: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_status(request: Request):
        auth(request)
        return backend.status()

    @app.post(
        "/api/v1/tcu/connect",
        response_model=TCUStatus,
        responses={401: {"model": APIError}, 503: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_connect(request: Request):
        auth(request)
        try:
            return await backend.connect()
        except TCUError as exc:
            return handle_tcu_error(exc)

    @app.post(
        "/api/v1/tcu/disconnect",
        response_model=TCUStatus,
        responses={401: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_disconnect(request: Request):
        auth(request)
        return await backend.disconnect()

    @app.get(
        "/api/v1/tcu/services",
        response_model=list[str],
        responses={401: {"model": APIError}, 503: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_services(request: Request):
        auth(request)
        try:
            return await backend.services()
        except TCUError as exc:
            return handle_tcu_error(exc)

    @app.get(
        "/api/v1/tcu/objects",
        responses={401: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_objects(request: Request, service: str | None = None):
        auth(request)
        return await backend.objects(service)

    @app.get(
        "/api/v1/tcu/introspect",
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_introspect(
        request: Request, service: str, object_path: str
    ) -> dict[str, str] | JSONResponse:
        auth(request)
        if not service.strip() or not object_path.startswith("/"):
            return _error("invalid_request", "Valid service and absolute object_path are required", 400)
        try:
            return {
                "service": service,
                "object_path": object_path,
                "xml": await backend.introspect(service, object_path),
            }
        except TCUError as exc:
            return handle_tcu_error(exc)

    @app.post(
        "/api/v1/tcu/calls",
        status_code=202,
        response_model=AcceptedCall,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            413: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": MethodCallRequest.model_json_schema()
                    }
                },
            }
        },
        tags=["TCU"],
    )
    async def tcu_call(request: Request):
        auth(request)
        body = await request.body()
        if len(body) > MAX_CALL_BODY_BYTES:
            return _error("request_too_large", "Request body exceeds 256 KiB", 413)
        try:
            payload = MethodCallRequest.model_validate_json(body)
        except ValidationError as exc:
            details = [
                {"location": error["loc"], "message": error["msg"], "type": error["type"]}
                for error in exc.errors()
            ]
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_request", "message": details}},
            )
        try:
            record = backend.submit_call(
                payload.service,
                payload.object_path,
                payload.interface,
                payload.method,
                payload.arguments,
                payload.timeout_seconds,
            )
        except ValueError as exc:
            return _error("invalid_request", str(exc), 400)
        return AcceptedCall(
            request_id=record.request_id,
            status="accepted",
            submitted_at=record.submitted_at,
        )

    @app.get(
        "/api/v1/tcu/calls/{request_id}",
        response_model=CallResult,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        tags=["TCU"],
    )
    async def tcu_call_result(request: Request, request_id: str):
        auth(request)
        record = backend.get_call(request_id)
        if record is None:
            return _error("request_not_found", "Unknown or expired request_id", 404)
        return record.to_dict()

    @app.websocket("/api/v1/tcu/ws")
    async def tcu_events(websocket: WebSocket, token: str | None = None) -> None:
        authorization = websocket.headers.get("authorization", "")
        supplied = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else token
        )
        try:
            check_token(supplied)
        except HTTPException:
            await websocket.close(code=1008)
            return
        queue = backend.events.subscribe()
        await websocket.accept()

        async def sender() -> None:
            while True:
                await websocket.send_json((await queue.get()).to_dict())

        async def receiver() -> None:
            while True:
                await websocket.receive_text()

        tasks = {
            asyncio.create_task(sender()),
            asyncio.create_task(receiver()),
        }
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except (WebSocketDisconnect, RuntimeError):
                    pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            backend.events.unsubscribe(queue)

