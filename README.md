# RPI-SemiAutomator

A Docker-based tool for Raspberry Pi that displays multiple serial interfaces
(for example, `/dev/ttyUSB0` and `/dev/ttyACM0`) in a Python web UI powered by
[NiceGUI](https://nicegui.io/). Read live data, send commands, and trigger
custom macros.

## Features

- Automatically detect serial ports; show only saved favorites on the home page.
- Connect and disconnect each port; configure baud rates per favorite.
- Live terminal for each port, with a clear-terminal button and downloadable
  logs. Resize the terminal vertically; its height is saved per port in the
  browser.
- Send arbitrary text commands.
- Create alias-bound macros in the menu and organize them in nested folders
  using `/`.
- Manage favorites, macros, backups, and saved settings on the Settings page.
- Run as a Docker container on Raspberry Pi.
- CAN monitor and DBC message generator using python-can and cantools.
- D-Bus control panel with REST and WebSocket APIs. A protected transport must
  be configured separately.

## Configuration

Configure serial port presets in [`config/config.yaml`](config/config.yaml):

```yaml
ports:
  - name: "USB0"
    device: "/dev/ttyUSB0"
    baudrate: 115200
```

Override the configuration file path with `RPI_SEMIAUTOMATOR_CONFIG`.

On the Settings page, save configured or detected ports as favorites with a
custom name and baud rate. Only favorites appear as terminal tabs on the home
page. Favorites are stored in `data/favorites.yaml` or the path specified by
`RPI_SEMIAUTOMATOR_FAVORITES`.

Create macros from the menu by right-clicking a folder, a macro, or the empty
menu area. Macros are stored in `data/macros.yaml` or the path specified by
`RPI_SEMIAUTOMATOR_MACROS`. Each macro is bound to the active port's alias, not
its device path. A folder path such as `System/Startup` creates nested folders.
Macros are shown and can be run only for the matching alias; edit or delete them
in Settings. No predefined macros are included.

Settings also provides a YAML backup download and reload actions for favorites
and macros.

## CAN

The CAN monitor is integrated into the same NiceGUI application. CAN profiles
are stored in `data/can_profiles.yaml`; DBC files are stored in `data/dbc/`.
Override these paths with `RPI_SEMIAUTOMATOR_CAN_PROFILES` and
`RPI_SEMIAUTOMATOR_DBC_DIR`.

The CAN manager opens at most one connection for all clients. New profiles use
`listen_only: true`, and application-level transmission is disabled by default.
To transmit, save and connect a profile with `listen_only: false`, then
explicitly enable transmission. There is no cyclic transmission. The DBC
generator previews each message and rejects values outside signal ranges.
Uploaded DBC files are validated and saved under random names; their contents
are never executed. DBC data does not automatically provide required CRCs,
alive counters, or ECU enable sequences.

python-can provides the `pcan`, `vector`, `kvaser`, `socketcan`, and `virtual`
backends. Native PEAK, Vector, and Kvaser backends require their Linux drivers
and vendor SDKs, which are not installed in the standard container image.
Check device, SDK, kernel, and architecture compatibility for each adapter.
SocketCAN interfaces and bit rates are configured on the host. Compose does not
set `privileged: true` or grant blanket CAN permissions; configure only the
minimum required device and network access. CAN FD requires compatible hardware
and driver configuration.

## D-Bus

The D-Bus panel is available at `/dbus`; the former `/tcu` page redirects to it.
The versioned API is available under `/api/v1/dbus`; `/api/v1/tcu` remains as
a compatibility alias.
Integration is disabled by default and uses a shared backend service. This
repository does not specify the target bus or a remote access path. Configure a
protected proxy that exposes a local Unix socket before enabling the feature.
D-Bus TCP is not supported.

See [configuration](docs/dbus/configuration.md),
[architecture](docs/dbus/architecture.md), [REST API](docs/dbus/api.md),
[WebSocket events](docs/dbus/websocket.md), and
[troubleshooting](docs/dbus/troubleshooting.md). Swagger/OpenAPI remains
available at `/docs` and `/openapi.json`.

## Run with Docker

```bash
docker compose up --build
```

Open `http://<raspberry-pi-ip>:8080` in a browser.

The application detects devices such as `/dev/ttyUSB*`, `/dev/ttyACM*`, and
`/dev/ttyAMA*`. `lsusb` also lists USB devices that do not provide a serial
interface; those cannot be used as serial terminals. Docker mounts `/dev` and
limits access with `device_cgroup_rules` to the supported serial device types.
The `./data:/app/data` bind mount preserves saved favorites across container
restarts.

## Local development

```bash
pip install -r requirements.txt
python app/main.py
```

## Tests

```bash
pip install -r requirements.txt pytest
pytest
```

## UI settings

- Dark mode is saved per browser.
- Terminal height is saved per serial port in the browser.
- Serial port tabs are centered on the home page; the menu contains macros.
- Manage favorites, baud rates, and macros in Settings.
- Right-click the empty menu area, a folder, or a macro to add a macro to that
  folder. Use `/` in folder paths to create nested folders.

## API

All endpoints can optionally be secured with `RPI_SEMIAUTOMATOR_API_TOKEN`.
Send it as a bearer token in the `Authorization` header or as `?token=...`.
Swagger/OpenAPI is available at `/docs` (schema: `/openapi.json`).

- `GET /api/ports` — serial ports and status.
- `POST /api/send` — send a command, for example
  `{"device":"/dev/ttyUSB0","command":"...","raw":false}`.
- `WS /api/ws?device=/dev/ttyUSB0` — stream received/transmitted data and send
  text or `{"command":"..."}`.
- `GET /api/aliases` — serial ports configured with aliases.
- `POST /api/aliases/{alias}/send` and `WS /api/aliases/{alias}/ws` — send and
  stream data using an alias instead of a device path.
- `GET /api/macros` and `GET /api/aliases/{alias}/macros` — list macros.
- `POST /api/macros`, `PUT /api/macros`, and `DELETE /api/macros/{label}` —
  create, edit, and delete alias-bound macros.
- `GET /api/backup` — download a YAML backup of favorites and macros.
- `/api/can/...` — manage CAN profiles and connections, receive frames, control
  transmission, and manage DBC files and message previews. New CAN profiles use
  `listen_only: true`.
- `/api/v1/dbus/...` — D-Bus API routes (`/api/v1/tcu/...` is a compatibility
  alias); see the
  [D-Bus API documentation](docs/dbus/api.md).

An alias is the port name from `config/config.yaml` or the name of a saved
favorite; favorite names take precedence. Aliases must be unique. Ambiguous
aliases are rejected with HTTP 409. URL-encode aliases containing spaces.
Device-path endpoints remain available.

The macro API reads and updates custom macros in `data/macros.yaml` (or
`RPI_SEMIAUTOMATOR_MACROS`) without changing port presets in
`config/config.yaml`. Backups contain saved favorites and custom macros.
