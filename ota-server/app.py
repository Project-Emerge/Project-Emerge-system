"""OTA update server for the dropbot firmware.

Serves the manifest+binary the firmware's OTA client polls for, and accepts
new firmware uploads over HTTP so publishing doesn't require shell access to
the host running this container.

  GET  /api/firmware/latest   -> current data/manifest.json
  GET  /firmware/<name>       -> a published binary from data/firmware/
  POST /api/firmware/publish  -> upload a new version (see publish.sh)
"""

import json
import os
import re
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
FIRMWARE_DIR = DATA_DIR / "firmware"
MANIFEST_PATH = DATA_DIR / "manifest.json"
PORT = int(os.environ.get("PORT", "80"))

# ota_0 / ota_1 slot size from the firmware's partitions.csv - an image
# bigger than this can never be installed, so publishing one is refused.
MAX_IMAGE_SIZE = 1024 * 1024

# Version strings become part of a filename on disk (dropbot-<version>.bin)
# and are echoed back verbatim in manifest.json, so keep them to a safe,
# predictable charset - this also blocks path traversal via e.g. "../../x".
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

# If set, /api/firmware/publish requires a matching X-Publish-Token header.
# Left unset by default so `curl`-ing a firmware up just works on a trusted
# LAN - see README.md before exposing this port more broadly.
PUBLISH_TOKEN = os.environ.get("PUBLISH_TOKEN")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE + 64 * 1024  # image + multipart overhead


def read_manifest() -> dict | None:
    if not MANIFEST_PATH.is_file():
        return None
    with MANIFEST_PATH.open() as f:
        return json.load(f)


def write_manifest(version: str, filename: str, size: int) -> dict:
    manifest = {"version": version, "url": f"/firmware/{filename}", "size": size}
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


@app.errorhandler(HTTPException)
def handle_http_exception(err: HTTPException):
    return jsonify({"error": err.description}), err.code


@app.get("/api/firmware/latest")
def latest_firmware():
    manifest = read_manifest()
    if manifest is None:
        abort(404, description="No firmware has been published yet")
    return jsonify(manifest)


@app.get("/firmware/<path:filename>")
def download_firmware(filename: str):
    return send_from_directory(FIRMWARE_DIR, filename)


@app.post("/api/firmware/publish")
def publish_firmware():
    if PUBLISH_TOKEN and request.headers.get("X-Publish-Token") != PUBLISH_TOKEN:
        abort(401, description="missing or invalid X-Publish-Token header")

    version = request.form.get("version", "").strip()
    if not VERSION_RE.match(version):
        abort(400, description="'version' must match ^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

    firmware = request.files.get("firmware")
    if firmware is None or firmware.filename == "":
        abort(400, description="missing 'firmware' file field")

    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"dropbot-{version}.bin"
    dest = FIRMWARE_DIR / filename
    firmware.save(dest)

    size = dest.stat().st_size
    if size > MAX_IMAGE_SIZE:
        dest.unlink()
        abort(400, description=f"image is {size} bytes, exceeds the {MAX_IMAGE_SIZE}-byte OTA slot")

    manifest = write_manifest(version, filename, size)
    return jsonify(manifest), 201


if __name__ == "__main__":
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    if not PUBLISH_TOKEN:
        print(
            "ota-server: PUBLISH_TOKEN not set - /api/firmware/publish accepts uploads "
            "from anyone who can reach this port",
            flush=True,
        )
    app.run(host="0.0.0.0", port=PORT, threaded=True)
