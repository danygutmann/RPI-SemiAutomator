# TCU architecture

## Repository integration

The application entry point is `app/main.py`. It imports NiceGUI's FastAPI
application, registers the existing serial and CAN APIs on that same server,
and starts NiceGUI on port 8080. Navigation is built in `app/panels.py`.
Serial and CAN I/O already use backend managers independent of individual
browser pages; serial and CAN reception run outside the UI event loop. Relay
and PSU controls currently expose in-memory state and placeholder hardware
hooks in `app/panels.py`.

TCU-only implementation is under `app/tcu/`:

| Module | Responsibility |
| --- | --- |
| `core/config.py` | Validate environment settings and expose immutable configuration. |
| `core/models.py` | Call records and loss-aware JSON conversion for D-Bus values. |
| `core/service.py` | Async call orchestration, request history, timeout and event handling. |
| `core/runtime.py` | One process-level service shared by the GUI and API. |
| `dbus/client.py` | `dbus-next` connection, introspection, method messages, error mapping, and signal match. |
| `dbus/exceptions.py` | Transport, validation, timeout, and remote-method errors. |
| `events/models.py` | Versioned event envelope. |
| `events/dispatcher.py` | Bounded fan-out and in-memory event history. |
| `api/routes.py`, `api/schemas.py` | Versioned REST routes, OpenAPI schemas, auth, and WebSocket transport. |
| `gui/tab.py` | NiceGUI TCU page; no D-Bus connection or message construction. |

The dependency direction is GUI/API → TCU service → D-Bus adapter. The service
publishes common event objects; external clients consume them through the TCU
WebSocket and the GUI presents the same event stream from the in-process
dispatcher. The REST API is registered on NiceGUI's existing FastAPI app, so
there is no second HTTP server. Existing APIs and hardware managers are not
replaced.

## Remote transport prerequisite

The repository and current environment contain no TCU address, service/interface
definitions, credentials, SSH configuration, or proxy specification. Therefore
the actual TCU route cannot be verified here, and the application does not
invent a target or start an SSH session. The chosen library is `dbus-next`
0.2.3, an asynchronous D-Bus client. It connects only to an explicitly
configured Unix socket or a loopback TCP bus address.

For remote use, an operator must first provide a D-Bus socket or a loopback
endpoint backed by a verified, protected tunnel/proxy. The application does not
provide SSH, credentials, or an unprotected remote TCP bus. TCU support is
disabled by default and reports a clear transport error until configured.

## Method-call sequence

1. A client submits the service, object path, interface, method, arguments,
   and optional timeout using `POST /api/v1/tcu/calls`.
2. The API authenticates and validates the JSON request, assigns a UUID, keeps
   the call record, and responds with `202 Accepted`.
3. The TCU service emits `method.started` and performs the async call without
   blocking the NiceGUI loop.
4. The adapter introspects the requested object, checks the input signature,
   converts typed JSON values, and sends the D-Bus message.
5. Completion or failure updates the record and emits `method.completed` or
   `method.failed`; clients can observe WebSocket events or poll the request ID.
6. A local timeout does not prove the remote method did not execute. The service
   never retries state-changing calls automatically.

At most 16 method tasks run concurrently. The request result cache retains the
most recent 1,000 calls in memory; older request IDs expire and return 404.

## Signals, queues, and errors

After connection, the adapter installs one generic signal match and one message
handler. Signals are converted to the shared event format and distributed to
subscribers. Disconnect removes the handler with the bus connection; reconnect
registers a fresh handler, avoiding duplicate subscriptions. An unexpected bus
disconnect updates status and emits connection/error events.

Each WebSocket client has a queue limited to 100 events. Publishing never waits
for network I/O; when a queue is full, the oldest queued event is discarded.
The in-process history is also bounded (1,000 events) and is used by the GUI;
it is not a durable event log. A disconnected or slow external client can miss
events without replay on reconnect. WebSocket send failures do not cancel D-Bus
calls.

Errors distinguish invalid arguments, unconfigured/unavailable transport,
missing D-Bus targets, remote method errors, timeouts, and internal failures.
Responses do not include connection credentials. Unknown concrete TCU
interfaces are not hard-coded.

## Future asynchronous Pytest runner

A later runner can be added as another application service that uses the same
TCU service/adapter and hardware managers. It can publish test start/progress/
result events through the existing dispatcher and expose start/status routes
without placing test logic in NiceGUI callbacks. This change does not implement
a test runner, scheduler, macro engine, or test editor.
