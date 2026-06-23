#!/usr/bin/env bash
#
# Overall Project Emerge offloading demo.
#
# Brings up: MQTT broker, the central aggregate-runtime, the offloading-manager, the robot
# emulation, the dashboard (which now hosts the Central/Edge offloading switch), the edge monitor,
# and a dedicated EDGE runtime for one robot. The edge runtime connects to the manager as that robot
# and asks to offload its computation, so the central runtime drives it; from the dashboard you can
# switch its compute to Edge and watch the edge runtime take over — the robot keeps moving across the
# hand-off, on one coherent distributed field (exports exchanged over MQTT).
#
# Usage:   support-script/demo.sh [ROBOT_ID] [N_ROBOTS]
# Example: support-script/demo.sh 5 6
#
# Stop with: support-script/demo.sh --down
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EDGE_IMAGE="emerge-edge-runtime"
NETWORK="project-emerge-network"

if [[ "${1:-}" == "--down" ]]; then
  echo "==> Stopping edge runtime + core services"
  docker rm -f emerge-edge 2>/dev/null || true
  pkill -f "robot_emulation/main.py" 2>/dev/null || true
  docker compose down
  exit 0
fi

ROBOT_ID="${1:-5}"
N_ROBOTS="${2:-6}"

echo "==> Building & starting core services (broker, dashboard, edge-monitor, central runtime, offloading-manager, neighborhood-system)"
docker compose up -d --build mqtt-broker dashboard edge-monitor aggregate-runtime offloading-manager neighborhood-system

echo "==> Building the edge runtime image ($EDGE_IMAGE)"
docker build -f aggregate-runtime/Dockerfile.edge -t "$EDGE_IMAGE" .

echo "==> Starting robot emulation ($N_ROBOTS robots)"
pkill -f "robot_emulation/main.py" 2>/dev/null || true
PYTHONPATH=robot-emulation/src nohup uv run python robot-emulation/src/robot_emulation/main.py \
  --robots "$N_ROBOTS" --mqtt mqtt://localhost:1883 --world-size 5.0 > /tmp/emerge-emu.log 2>&1 &
disown || true
echo "    emulation pid $! (log: /tmp/emerge-emu.log)"

echo "==> Starting EDGE runtime for robot $ROBOT_ID (connects to the manager as the robot, requests offloading)"
docker rm -f emerge-edge 2>/dev/null || true
docker run -d --rm --name emerge-edge --network "$NETWORK" \
  -e ROBOT_ID="$ROBOT_ID" \
  -e MQTT_URL=tcp://mqtt-broker:1883 \
  -e OFFLOADING_MANAGER_HOST=offloading-manager -e OFFLOADING_MANAGER_PORT=8000 \
  "$EDGE_IMAGE" >/dev/null
echo "    edge container 'emerge-edge' (logs: docker logs -f emerge-edge)"

cat <<EOF

================================================================================
Demo is up.

  Dashboard    : http://localhost:5173/   (swarm, leader, AND the Central/Edge compute switch)
  Edge monitor : http://localhost:5174/   (live view of how each edge runtime is evaluating)
                 Offloading Manager: http://localhost:8081

  Try it    : 1. In the dashboard, select robot $ROBOT_ID (click it, or type its id + "Select Robot").
              2. Robot $ROBOT_ID starts on Central -> the central runtime computes & drives it.
              3. Click "Switch to Edge" -> the edge runtime takes over; watch its card on :5174
                 start computing (ticks rise, neighbours/export shown) while the robot keeps moving.

  Logs      : docker logs -f emerge-edge                 # the edge runtime
              docker logs -f project-emerge-aggregate-runtime   # the central runtime
              docker logs -f project-emerge-offloading-manager  # the manager

  Stop      : support-script/demo.sh --down
================================================================================
EOF
