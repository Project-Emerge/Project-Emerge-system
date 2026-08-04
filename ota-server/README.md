# OTA Update Server

A small Flask server that serves firmware images to the dropbot's OTA client
([`ota_manager.rs`](https://github.com/Project-Emerge/Project-Emerge-dropbot-firmware/blob/main/src/tasks/ota_manager.rs)
in `Project-Emerge-dropbot-firmware`), and accepts new firmware uploads over
HTTP so publishing a release doesn't require shell access to the docker
host - just `curl`.

## How it works

- Every 30 minutes (or immediately after an MQTT message on `dio/ota/check`),
  each dropbot does `GET http://{OTA_SERVER_HOST}/api/firmware/latest`.
- This server answers with the contents of `data/manifest.json`.
- If `manifest.json`'s `version` differs from the firmware's compiled-in
  `FIRMWARE_VERSION`, the device downloads the binary at `manifest.json`'s
  `url` (served from `data/firmware/`) straight into its inactive OTA slot
  and reboots into it.
- **The firmware's request URL never includes a port** - it's always
  `http://{OTA_SERVER_HOST}/...`, i.e. port 80. `docker-compose.yml` maps
  this container to host port 80 accordingly. If you need a different port,
  see [Running](#running) below.

`data/manifest.json` and `data/firmware/` are the only state; publishing is
just a `POST` that writes them.

## Publishing a new firmware version

1. In `Project-Emerge-dropbot-firmware`, build and export a **flashable
   image** - not the raw ELF:
   ```bash
   cargo build --release
   espflash save-image --chip esp32c6 target/riscv32imac-unknown-none-elf/release/dropbot-firmware dropbot-1.2.3.bin
   ```
2. Upload it to the running server, either with the helper script:
   ```bash
   ./publish.sh 1.2.3 ./dropbot-1.2.3.bin
   # or, targeting a remote docker host:
   ./publish.sh 1.2.3 ./dropbot-1.2.3.bin http://192.168.8.1
   ```
   or directly with `curl` from any machine on the LAN (the script is
   nothing more than this):
   ```bash
   curl -f -X POST http://192.168.8.1/api/firmware/publish \
     -F "version=1.2.3" \
     -F "firmware=@dropbot-1.2.3.bin"
   ```
   Either way, the server computes the file's size itself, writes
   `data/firmware/dropbot-1.2.3.bin`, and (re)writes `data/manifest.json` to
   point at it - responding with the new manifest as JSON. It refuses images
   larger than 1 MiB (the `ota_0`/`ota_1` slot size in the firmware's
   `partitions.csv`) and rejects malformed version strings.
3. Bump `FIRMWARE_VERSION = "1.2.3"` in the firmware's `.cargo/config.toml`
   for the *next* build you intend to ship. This is what a running device
   compares against the server's `manifest.json.version` to decide whether
   it's out of date - it's unrelated to what you just published, so a
   device already running 1.2.3 won't update again until you publish
   something newer than whatever it was built with.
4. Devices pick up the change on their next poll (within 30 min), or
   immediately if you publish to the MQTT broker:
   ```bash
   mosquitto_pub -h localhost -t dio/ota/check -m ""
   ```

Publishing again simply repeats step 2 with a new version/file; older
binaries are left on disk under their own filename in `data/firmware/`,
only `manifest.json` moves to point at the newest one.

## Flashing a device for OTA the first time

A device needs the OTA-capable partition table before it can receive
updates at all - this is a one-time, per-device step (existing single-slot
installs don't have the `otadata`/`ota_0`/`ota_1` layout this server relies
on):

```bash
espflash erase-flash
laze build -b dropbot -a dropbot-firmware flash -- --partition-table=partitions.csv
```

`--partition-table` currently needs to be passed explicitly on every
reflash (it isn't wired into the vendored `laze` flash task).

## Running

```bash
docker compose up -d --build ota-server
```

By default this binds host port 80, matching the firmware's hardcoded
port-less URL (`OTA_SERVER_HOST` defaults to `192.168.8.1` with no port
suffix). If port 80 is already taken on the docker host:

- either free it, or
- pick a different port for both sides: change `ports:` in
  `docker-compose.yml` to e.g. `"8080:80"`, and set the firmware's
  `OTA_SERVER_HOST` build-time env to include it, e.g.
  `OTA_SERVER_HOST=192.168.8.1:8080` in `.cargo/config.toml` - it's spliced
  directly into the request URL, so a `host:port` string works.

Either way, `OTA_SERVER_HOST` must resolve to this docker host's LAN IP as
seen by the robots.

### Requiring a token to publish

`POST /api/firmware/publish` is open by default - anyone who can reach the
port can push firmware to every robot that polls this server. On a trusted,
isolated LAN that's usually fine; to lock it down, set `OTA_PUBLISH_TOKEN`
in the environment before `docker compose up`:

```bash
export OTA_PUBLISH_TOKEN=some-shared-secret
docker compose up -d ota-server
```

and then pass the same value when publishing:

```bash
PUBLISH_TOKEN=some-shared-secret ./publish.sh 1.2.3 ./dropbot-1.2.3.bin
# or with curl directly:
curl -f -X POST http://192.168.8.1/api/firmware/publish \
  -H "X-Publish-Token: some-shared-secret" \
  -F "version=1.2.3" -F "firmware=@dropbot-1.2.3.bin"
```

`GET /api/firmware/latest` and `GET /firmware/<name>` are always open (the
devices don't send any credentials).

## Layout

```
ota-server/
├── Dockerfile
├── app.py             # the server (Flask): routes, manifest + upload handling
├── requirements.txt
├── publish.sh          # curl wrapper around POST /api/firmware/publish
├── data/                # bind-mounted into the container at /data - gitignored
│   ├── manifest.json
│   └── firmware/
│       └── dropbot-<version>.bin
└── README.md
```

## Endpoints

- `GET /api/firmware/latest` → contents of `data/manifest.json`, e.g.:
  ```json
  {"version": "1.2.3", "url": "/firmware/dropbot-1.2.3.bin", "size": 123456}
  ```
  Returns `404` if nothing has been published yet.
- `GET /firmware/<name>` → the raw binary at `data/firmware/<name>`.
- `POST /api/firmware/publish` → multipart form upload, fields `version`
  (e.g. `1.2.3`) and `firmware` (the `.bin` file). Returns the new manifest
  as JSON with `201`, or a JSON `{"error": "..."}` with `400`/`401`/`413` on
  failure.

## Caveats

- Version comparison on the firmware side is a plain string inequality -
  whatever `manifest.json` says becomes "latest", there's no semver
  ordering or rollback protection. Don't publish an older version by
  mistake, and don't publish a version string equal to what a device is
  already running if you want it to actually re-flash.
- Without `OTA_PUBLISH_TOKEN` set, publishing is unauthenticated - see
  [Requiring a token to publish](#requiring-a-token-to-publish). Don't
  expose this port to the internet either way.
