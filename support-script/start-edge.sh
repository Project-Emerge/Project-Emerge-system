#!/usr/bin/env bash
#
# Starts a standalone EdgeRobotRuntime container for a specific robot.
# It joins the 'project-emerge-network' and connects to the offloading manager and MQTT broker.
#
# Usage:   control-panel/start-edge.sh [ROBOT_ID]
# Example: control-panel/start-edge.sh 5
#
# To stop: docker rm -f emerge-edge-[ROBOT_ID]
#

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EDGE_IMAGE="emerge-edge-runtime"
NETWORK="project-emerge-network"
ROBOT_ID="${1:-5}"
CONTAINER_NAME="emerge-edge-$ROBOT_ID"

echo "==> Building the edge runtime image ($EDGE_IMAGE)"
docker build -f aggregate-runtime/Dockerfile.edge -t "$EDGE_IMAGE" .

echo "==> Starting EDGE runtime for robot $ROBOT_ID (container: $CONTAINER_NAME)"
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

docker run -d --name "$CONTAINER_NAME" --network "$NETWORK" \
  -e ROBOT_ID="$ROBOT_ID" \
  -e MQTT_URL=tcp://mqtt-broker:1883 \
  -e OFFLOADING_MANAGER_HOST=offloading-manager -e OFFLOADING_MANAGER_PORT=8000 \
  "$EDGE_IMAGE"

echo "==> Edge runtime started successfully!"
echo "    Container: $CONTAINER_NAME"
echo "    Check logs with: docker logs -f $CONTAINER_NAME"
echo "    Stop it with: docker rm -f $CONTAINER_NAME"
