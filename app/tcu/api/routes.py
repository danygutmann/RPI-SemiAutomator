"""D-Bus REST endpoints and event WebSocket."""

from __future__ import annotations

import asyncio
import logging

from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from ..core.runtime import tcu_service as default_service
from ..core.service import TCUService
from ..dbus.exceptions import TCUError
from .schemas import (
    AcceptedCall,
    APIError,
    CallResult,
    IntrospectionResult,
    MethodCallRequest,
    TCUObject,
    TCUStatus,
)

MAX_CALL_BODY_BYTES = 256 * 1024
logger = logging.getLogger(__name__)


async def _read_call_body(request: Request) -> bytes:
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_CALL_BODY_BYTES:
            raise OverflowError
        chunks.append(chunk)
    return b"".join(chunks)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


class TCUAPIRoute(APIRoute):
    def __init__(self, *args, **kwargs) -> None:
        responses = kwargs.setdefault("responses", {})
        responses.setdefault(422, {"model": APIError})
        responses.setdefault(500, {"model": APIError})
        super().__init__(*args, **kwargs)

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                message = "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
                return _error("invalid_request", message, 422)
            except HTTPException as exc:
                code = "authentication_failed" if exc.status_code == 401 else "http_error"
                return _error(code, str(exc.detail), exc.status_code)
            except Exception as exc:
                logger.error("Unhandled D-Bus API failure (%s)", type(exc).__name__)
                return _error("internal_error", "Internal D-Bus API error", 500)

        return handler


def register_api(
    app: FastAPI,
    check_token: Callable,
    bearer: Callable,
    service: TCUService | None = None,
) -> None:
    router = APIRouter(route_class=TCUAPIRoute)
    backend = service or default_service

    def auth(request: Request) -> None:
        check_token(bearer(request))

    def handle_tcu_error(exc: TCUError) -> JSONResponse:
        return _error(exc.code, str(exc), exc.status_code)

    @router.get(
        "/status",
        response_model=TCUStatus,
        responses={401: {"model": APIError}},
        tags=["D-Bus"],
    )
    async def tcu_status(request: Request):
        auth(request)
        return backend.status()

    @router.post(
        "/connect",
        response_model=TCUStatus,
        responses={
            401: {"model": APIError},
            502: {"model": APIError},
            503: {"model": APIError},
            504: {"model": APIError},
        },
        tags=["D-Bus"],
    )
    async def tcu_connect(request: Request):
        auth(request)
        try:
            return await backend.connect()
        except TCUError as exc:
            return handle_tcu_error(exc)

    @router.post(
        "/disconnect",
        response_model=TCUStatus,
        responses={401: {"model": APIError}},
        tags=["D-Bus"],
    )
    async def tcu_disconnect(request: Request):
        auth(request)
        return await backend.disconnect()

    @router.get(
        "/services",
        response_model=list[str],
        responses={
            401: {"model": APIError},
            502: {"model": APIError},
            503: {"model": APIError},
            504: {"model": APIError},
        },
        tags=["D-Bus"],
    )
    async def tcu_services(request: Request):
        auth(request)
        try:
            return await backend.services()
        except TCUError as exc:
            return handle_tcu_error(exc)

    @router.get(
        "/objects",
        response_model=list[TCUObject],
        responses={401: {"model": APIError}},
        tags=["D-Bus"],
    )
    async def tcu_objects(request: Request, service: str | None = None):
        auth(request)
        return await backend.objects(service)

    @router.get(
        "/introspect",
        response_model=IntrospectionResult,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            503: {"model": APIError},
            504: {"model": APIError},
        },
        tags=["D-Bus"],
    )
    async def tcu_introspect(
        request: Request, service: str, object_path: str
    ):
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

    @router.post(
        "/calls",
        status_code=202,
        response_model=AcceptedCall,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            413: {"model": APIError},
            429: {"model": APIError},
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
        tags=["D-Bus"],
    )
    async def tcu_call(request: Request):
        auth(request)
        try:
            body = await _read_call_body(request)
        except OverflowError:
            return _error("request_too_large", "Request body exceeds 256 KiB", 413)
        try:
            payload = MethodCallRequest.model_validate_json(body)
        except ValidationError as exc:
            details = [
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            ]
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_request",
                        "message": "; ".join(details),
                    }
                },
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
        except TCUError as exc:
            return handle_tcu_error(exc)
        return AcceptedCall(
            request_id=record.request_id,
            status="accepted",
            submitted_at=record.submitted_at,
        )

    @router.get(
        "/calls/{request_id}",
        response_model=CallResult,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        tags=["D-Bus"],
    )
    async def tcu_call_result(request: Request, request_id: str):
        auth(request)
        record = backend.get_call(request_id)
        if record is None:
            return _error("request_not_found", "Unknown or expired request_id", 404)
        return record.to_dict()

    @router.websocket("/ws")
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
        await websocket.accept()
        try:
            queue = backend.events.subscribe()
        except RuntimeError:
            await websocket.close(code=1013, reason="D-Bus event client limit reached")
            return

        async def sender() -> None:
            while True:
                await websocket.send_json((await queue.get()).to_dict())

        async def receiver() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("text") is not None or message.get("bytes") is not None:
                    await websocket.close(code=1003, reason="Use REST to submit actions")
                    return

        tasks = set()
        try:
            tasks = {
                asyncio.create_task(sender()),
                asyncio.create_task(receiver()),
            }
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

    app.include_router(router, prefix="/api/v1/dbus")
    app.include_router(router, prefix="/api/v1/tcu")
