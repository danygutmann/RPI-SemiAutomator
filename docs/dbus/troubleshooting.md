# D-Bus troubleshooting

## D-Bus is unreachable or transport is not configured

The feature is disabled by default. Set `DBUS_ENABLED=true` and configure
`DBUS_ADDRESS` as a local Unix socket. The
repository does not identify the target system's bus name or supported remote
access path. Verify these with the system owner; no target is guessed.

For Docker, verify the socket is under the mounted `./data` tree and inspect it
from the application container. Check path spelling and process permissions.
The SSH tunnel or proxy must provide a local Unix socket visible to the
application. This application does not start that tunnel.

## SSH or proxy is unavailable

Check the separately managed tunnel/proxy process, destination access, SSH
host-key/authentication configuration, and socket. A tunnel-provided Unix
socket can be mounted in `./data/dbus`. Do not enable D-Bus TCP as a
workaround.

## Missing D-Bus permissions

Check the bus policy and the application user's group/permissions for the
configured socket. The D-Bus policy must grant only the methods/signals needed
by the test setup. Do not make the bus world-writable or expose all signals to
untrusted users.

## Service, object, interface, or method unknown

Connect first, call `GET /api/v1/dbus/services`, and introspect the exact service
and object path. Confirm the name and capitalization against the returned XML.
`/objects` reports configured known paths and is not exhaustive; D-Bus does not
provide universal complete object discovery.

## Argument type invalid

Compare the JSON argument order/count with the method's introspection signature.
Use explicit wrappers for byte arrays, dictionaries, structures, object paths,
signatures, and variants as described in `docs/dbus/api.md`. Booleans are not
accepted as integers.

## Timeout or connection loss

Check network/tunnel latency and configured timeout. The reported duration is
local; a timeout does not establish whether a state-changing method executed
remotely. The app does not retry automatically. Check the result by a safe,
documented status method before deciding what to do next. After restoring the
transport, call `/api/v1/dbus/connect` again.

## WebSocket does not connect or stops receiving

Confirm `/api/v1/dbus/ws`, WebSocket proxy support, `wss://` setup where
required, token policy, and reverse-proxy upgrade headers. A slow client can
lose queued events; reconnecting does not replay signals. Poll the call result
endpoint if a method outcome is needed.
