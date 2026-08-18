# Project Emerge

Polyglot monorepo for the Project Emerge services. Deployable applications live in `apps/` regardless of implementation language; infrastructure shared by the stack lives in `infra/`.

## Repository layout

```text
apps/
  dashboard/          React dashboard and Node MQTT/WebSocket gateway
  <python-service>/   Future Python deployables
  <scala-service>/    Future Scala deployables
packages/             Optional language-specific shared libraries
infra/mosquitto/      MQTT broker configuration
compose.yaml          Local/edge service orchestration
Makefile              Language-neutral entry points
```

The root npm workspace lists JavaScript applications explicitly. Do not replace it with an `apps/*` glob: future Python and Scala applications are not npm packages.

Each deployable owns its language manifest, tests, and Dockerfile. A Python service should own its `pyproject.toml` and lockfile; a Scala service should own `build.sbt`, `project/`, and the standard `src/main` and `src/test` trees. Add its native build and test commands to the aggregate `Makefile` targets when the service is introduced.

## Local dashboard development

Requirements: Node.js 24 and npm 11.

```bash
cp .env.example .env
make bootstrap
npm run dev
```

Open `http://localhost:5173`. Vite forwards `/api` and `/ws` to the gateway on port `8787`. The root `.env` remains the configuration source after the dashboard's move into `apps/dashboard`.

The equivalent direct commands remain available:

```bash
npm test
npm run build
npm start
```

## Container stack

Start the production-style dashboard and Mosquitto broker with:

```bash
make up
make ps
```

The dashboard is available at `http://localhost:8787`; MQTT clients connect to `mqtt://localhost:1883`. Override the published host ports with `DASHBOARD_PORT` and `MQTT_PORT` in the root `.env` when necessary. Containers communicate over the `emerge-backplane` network, where the broker has the stable address `mosquitto:1883`.

The broker intentionally allows anonymous clients because the current robots operate on a trusted LAN. Port `1883` must not be exposed to an untrusted network without adding authentication, ACLs, and preferably TLS.

Stop the stack without deleting state:

```bash
make down
```

`dashboard-firmware` stores uploaded OTA images and manifests. `mosquitto-data` stores retained MQTT messages and persistent sessions. Both named volumes survive container recreation and normal `docker compose down`. Running `docker compose down -v` permanently deletes both volumes and their data.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `MQTT_URL` | `mqtt://192.168.8.1:1883` | Broker used by standalone dashboard development. Compose always uses `mqtt://mosquitto:1883`. |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | — | Optional credentials for an external broker. |
| `HTTP_PORT` | `8787` | Standalone gateway port. |
| `FIRMWARE_DIRECTORY` | `firmware` | Standalone firmware storage directory. Compose mounts its named volume separately. |
| `DASHBOARD_PORT` | `8787` | Dashboard port published by Compose. |
| `MQTT_PORT` | `1883` | MQTT port published by Compose. |
| `VITE_OTA_SERVER` | Current dashboard host and port | Optional OTA address suggested by the UI. |
| `VITE_GATEWAY_URL` | Same-origin `/ws` | Optional WebSocket gateway override. |

## MQTT and OTA behavior

The gateway subscribes to `/pose/+`, `/telemetry/+`, `/imu/+`, `/config/ota`, and `/config/robots/+`. Motor configuration is published with QoS 1 and retention on `/config/robots/{id}`.

Manual dashboard driving publishes normalized differential-wheel commands to `/motors/{id}` while the joystick is held. Moving commands use the payload `{"Move":{"left":-1..1,"right":-1..1}}` at 10 Hz with QoS 0; release and safety stops use `"Stop"` with QoS 1. Motor commands are never retained. Robot telemetry separately reports motor state as `Motoring` or `Stopped`. Robot firmware should independently stop both wheels when fresh commands have not arrived for 300 ms so a broken browser or network connection cannot leave the robot moving.

For OTA updates, provide a dashboard `host:port` reachable by the robots, the firmware version, and the compiled `.bin` file in **Settings → OTA update**. Use the dashboard's LAN address rather than `localhost`. The gateway stores the image, retains the OTA server on `/config/ota`, and publishes a non-retained `/ota/check/{id}` command to each discovered robot.
