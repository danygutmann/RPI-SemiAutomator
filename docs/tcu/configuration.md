# TCU configuration

TCU settings are read from environment variables at application startup. No
secret or target address is committed to the repository.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TCU_ENABLED` | `false` | Enables the TCU adapter. Accepted values are `true/false`, `1/0`, `yes/no`, and `on/off`. |
| `TCU_DBUS_ADDRESS` | unset | Required local bus address in the form `unix:path=/absolute/socket`. |
| `TCU_TIMEOUT_SECONDS` | `10` | Positive default timeout, maximum 60 seconds. |
| `TCU_OBJECT_PATHS` | `/` | Comma-separated known object paths for limited `/objects` reporting; this is not complete bus discovery. |

Only a local Unix socket is supported. D-Bus TCP addresses are rejected,
including loopback TCP. No SSH process, authentication, or tunnel is configured
by this application.

## Local setup

Before enabling this feature, confirm the TCU's supported remote-access method,
bus name, D-Bus policy, and required object paths with the TCU/system owner.
Provide the application with a local Unix socket from an approved protected
proxy/tunnel. Test the socket and permissions using the same OS user/container
as the application. The TCU service remains disabled by default and never
reports a simulated connection.

For Docker Compose, the existing `./data:/app/data` bind mount can expose a
host-managed socket created at `./data/tcu-dbus/bus.sock` to the container as
`/app/data/tcu-dbus/bus.sock`. Example `.env` settings:

```dotenv
TCU_ENABLED=true
TCU_DBUS_ADDRESS=unix:path=/app/data/tcu-dbus/bus.sock
TCU_TIMEOUT_SECONDS=10
TCU_OBJECT_PATHS=/
```

The socket's host-side proxy/tunnel must be started and secured separately.
Ensure its owner and permissions permit the container process to connect while
denying unintended clients. The normal Compose service and port remain
unchanged; there is no additional container.

For a native run, set `TCU_DBUS_ADDRESS` to the socket path visible to the
application process. The documented bus address may not be portable to other
D-Bus libraries; this application uses `dbus-next` 0.2.3.

## Diagnose missing transport

1. Read `GET /api/v1/tcu/status` or the TCU page's transport/error display.
2. Check that `TCU_ENABLED=true` and the address is valid in the process
   environment.
3. Check the socket exists and is accessible from inside the container. Verify
   it is provided by the separately managed, protected SSH/proxy configuration.
4. Check service ownership and D-Bus policy. Do not work around denied access
   by enabling an unrestricted network bus.
5. Review `docs/tcu/troubleshooting.md`.

The application has no reconnect loop. After restoring the transport, issue
`POST /api/v1/tcu/connect` again.
