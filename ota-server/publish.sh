#!/usr/bin/env bash
# Publishes a firmware image to a running OTA server via its HTTP upload
# endpoint. Thin wrapper around curl - see README.md to call it directly.
#
# Usage: ./publish.sh <version> <path-to-bin> [server-url]
#   ./publish.sh 1.2.3 ./dropbot-1.2.3.bin
#   ./publish.sh 1.2.3 ./dropbot-1.2.3.bin http://192.168.8.1
#
# <path-to-bin> must be the flashable image produced by `espflash save-image`
# (not the raw ELF) - see README.md for the full release flow.
#
# server-url defaults to http://localhost. If the server was started with
# PUBLISH_TOKEN set, export PUBLISH_TOKEN in this shell too before running.
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "Usage: $0 <version> <path-to-bin> [server-url]" >&2
    exit 1
fi

VERSION="$1"
SRC="$2"
SERVER_URL="${3:-http://localhost}"

if [ ! -f "$SRC" ]; then
    echo "error: $SRC not found" >&2
    exit 1
fi

ARGS=(-sS -f -X POST "${SERVER_URL%/}/api/firmware/publish" -F "version=${VERSION}" -F "firmware=@${SRC}")
if [ -n "${PUBLISH_TOKEN:-}" ]; then
    ARGS+=(-H "X-Publish-Token: ${PUBLISH_TOKEN}")
fi

curl "${ARGS[@]}"
echo
