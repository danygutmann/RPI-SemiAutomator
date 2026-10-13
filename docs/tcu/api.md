# TCU REST API

The API is registered on the existing NiceGUI/FastAPI server. OpenAPI and
Swagger UI are available at `/openapi.json` and `/docs`. All routes below use
the `/api/v1/tcu` prefix. If `RPI_SEMIAUTOMATOR_API_TOKEN` is set, provide the
configured token in the Authorization header or `?token=...`.
Prefer the Authorization header because URLs can be logged. The API is not
safe for public exposure merely because the token feature exists; use a trusted
test network or a properly secured reverse proxy.

Errors use `{"error":{"code":"...","message":"..."}}`.

## Endpoints

| Method and path | Purpose | Success |
| --- | --- | --- |
| `GET /api/v1/tcu/status` | Enabled/connected state, transport label, last change, and last error. | `200` status object. |
| `POST /api/v1/tcu/connect` | Connect to the configured local socket/tunnel. | `200` status object. |
| `POST /api/v1/tcu/disconnect` | Close the connection and signal subscription. | `200` disconnected status. |
| `GET /api/v1/tcu/services` | Ask the bus daemon for service names. | `200` string array. |
| `GET /api/v1/tcu/objects?service=...` | List configured, known paths (optionally associated with a service). | `200` object array; not full discovery. |
| `GET /api/v1/tcu/introspect?service=...&object_path=...` | Introspect one selected object. | `200` with service, path, and D-Bus XML. |
| `POST /api/v1/tcu/calls` | Accept a method call for asynchronous execution. | `202` with request ID and submission timestamp. |
| `GET /api/v1/tcu/calls/{request_id}` | Retrieve accepted/running/completed/failed call state. | `200` call result object. |

### Call request

```json
{
  "service": "com.example.TcuService",
  "object_path": "/com/example/Tcu",
  "interface": "com.example.Tcu",
  "method": "GetStatus",
  "arguments": [],
  "timeout_seconds": 10
}
```

Names above are placeholders, not asserted TCU APIs. The actual request is
validated against introspection immediately before sending. `arguments` is a
JSON array (maximum 64 arguments); request bodies are limited to 256 KiB and
timeouts to 60 seconds. On acceptance:

```json
{
  "request_id": "generated-uuid",
  "status": "accepted",
  "submitted_at": "2026-01-01T00:00:00+00:00"
}
```

`GET /calls/{request_id}` returns `request_id`, `status`, `service`,
`object_path`, `interface`, `method`, `submitted_at`, `started_at`,
`completed_at`, `duration_ms`, `result`, `result_signature`, and `error`.
Time fields not yet known are `null`. Result values are returned as an array of
out arguments, with the D-Bus return signature alongside it.

### D-Bus values in JSON

* Basic strings, booleans, numbers, and non-byte arrays use normal JSON values.
* `ay` byte arrays use `{"$type":"bytes","base64":"..."}` in responses. Calls
  accept that representation or an integer byte array.
* Dictionaries use
  `{"$type":"dict","signature":"a{sv}","entries":[[key,value],...]}`.
* Structures use
  `{"$type":"struct","signature":"(is)","value":[3,"text"]}`.
* Object paths, signatures, and variants use explicit wrappers, e.g.
  `{"$type":"object_path","value":"/x"}`,
  `{"$type":"signature","value":"a{sv}"}`, and
  `{"$type":"variant","signature":"s","value":"text"}`.

Typed wrappers preserve information JSON cannot represent by itself. Dictionary
and structure signatures must match introspection. Variants carry their own
single-value signature.

## Errors

* `400`: invalid request or argument type.
* `401`: API token required/invalid.
* `422`: required query parameters missing or malformed.
* `404`: unknown/expired request ID or introspected service, object, interface,
  or method.
* `413`: call request body exceeds 256 KiB.
* `429`: the bounded queue already contains 64 accepted/running calls.
* `502`: remote D-Bus method error.
* `503`: disabled, unconfigured, disconnected, or unreachable D-Bus transport.
* `504`: connection or method timeout.
* `500`: unexpected HTTP/API failure; an internal background-call failure appears
  in the call result as `internal_error`.

Method execution errors occur after an accepted `202`; poll the request or
consume the event WebSocket. A local timeout is not proof the TCU did not
execute the request. The application does not retry calls.

## Example

```sh
curl http://localhost:8080/api/v1/tcu/status

curl -X POST -H 'Content-Type: application/json' \
  --data '{"service":"com.example.TcuService","object_path":"/com/example/Tcu","interface":"com.example.Tcu","method":"GetStatus","arguments":[]}' \
  http://localhost:8080/api/v1/tcu/calls
```

Replace the placeholder names only after obtaining actual TCU introspection.
