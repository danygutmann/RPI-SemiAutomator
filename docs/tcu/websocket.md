# TCU WebSocket

Connect to `ws://<host>:8080/api/v1/tcu/ws` (or `wss://` behind a TLS-enabled
reverse proxy). The socket is server-to-client event transport; initiate
actions through REST. Client data frames are not accepted and close the
connection. Authentication follows the
existing optional `RPI_SEMIAUTOMATOR_API_TOKEN` policy: use an Authorization
header with the `Bearer` scheme and configured token where the WebSocket client supports it,
otherwise `?token=...`. Query tokens
can appear in access logs.

## Event envelope

Every message is JSON with this versioned shape:

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "event_type": "method.completed",
  "timestamp": "2026-01-01T00:00:00+00:00",
  "request_id": "call-uuid-or-null",
  "payload": {}
}
```

Event types:

| Type | Payload |
| --- | --- |
| `connection.status` | Current status object. |
| `method.started` | Service, object path, interface, and method. |
| `method.completed` | Status, result array, return signature, duration and call metadata. |
| `method.failed` | Status, structured error and call metadata. |
| `dbus.signal` | Sender/service, path, interface, signal member, signature and typed arguments. |
| `tcu.error` | Error code and safe message. |

Example completion:

```json
{
  "schema_version": 1,
  "event_id": "f31d8859-17b3-4d9c-b529-32cd9a96e447",
  "event_type": "method.completed",
  "timestamp": "2026-01-01T00:00:01+00:00",
  "request_id": "a56430bd-f84b-4be3-aac0-66481a59fedd",
  "payload": {
    "status": "completed",
    "result": [],
    "result_signature": "",
    "service": "com.example.TcuService",
    "object_path": "/com/example/Tcu",
    "interface": "com.example.Tcu",
    "method": "GetStatus",
    "started_at": "2026-01-01T00:00:00+00:00",
    "completed_at": "2026-01-01T00:00:01+00:00",
    "duration_ms": 1.2
  }
}
```

The method names are illustrative only. D-Bus values use the JSON typing rules
in `api.md`.

## Delivery and failures

At most 100 clients can be connected. Each has an independent queue capped at
100 events. Slow
clients do not block method execution or other subscribers; the oldest queued
event is discarded when that client's queue is full. On disconnect, its queue
and subscription are released. Failed sockets do not cancel backend calls.

There is no persistent event log, acknowledgement, replay cursor, or guaranteed
delivery. Events produced before a client connects are not sent to it, and
events may be lost on overflow or disconnect without a replay notification.
Reconnect and query `GET /api/v1/tcu/calls/{request_id}` to recover a method
result while it remains in the bounded in-memory request history. Signal history
is not persisted.

Use `wss://` outside an isolated trusted network and protect the API using the
existing token and/or a secured reverse proxy. Swagger documents the REST
routes; this document defines the WebSocket messages.
