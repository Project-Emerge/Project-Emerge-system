# Offloading demo

A robot's aggregate computation can live in two places, coordinated by the offloading-manager, and the
two runtimes keep one **coherent distributed field** by exchanging scafi exports over MQTT
(`robots/{id}/export`).

| Mode | Who computes for the robot | How |
|---|---|---|
| **Central** (offloaded) | the shared `aggregate-runtime` service | the manager tells the runtime to `enableRobot` |
| **Edge** (local) | a dedicated `EdgeRobotRuntime` for that robot | the manager tells the runtime to `disableRobot`; the edge runtime owns and drives it |

```
                              ┌────────────────────┐
                              │  aggregate-runtime │  central: owns the ENABLED robots
                              │   (distributed)    │  computes their rounds, publishes exports
                              └─────────▲──────────┘
        WS /ws/aggregate (change_status)│        ▲ robots/+/export (exports exchanged)
   dashboard ──REST PUT /{id}──►┌───────┴───────┐ │
   (Central/Edge switch)        │  offloading-  │ │
   edge runtime ──WS /ws/robots/{id}──► manager │ │
        │  (as the ROBOT)  self change_status └──┘ │
        ├───────── robots/{id}/export ────────────┘  (its node's export)
        ├───────── robots/{id}/move ──────────────►  robot-emulation  (only while it owns the robot)
        └───────── edge/{id}/status ──────────────►  edge monitor (:5174)
```

## The two UIs

- **Dashboard** — http://localhost:5173/ — the swarm view, leader/formation controls, **and** the
  per-robot **Compute: Central / Edge** switch (in the selected robot's panel; shown only for robots
  that actually have an edge runtime connected to the manager).
- **Edge monitor** — http://localhost:5174/ — one card per `EdgeRobotRuntime`, showing *how it is
  evaluating its node*: state (computing & driving / offloaded), tick rate, program, leader,
  neighbours used, export size, last actuation. Fed by `edge/{id}/status`.

## How the edge runtime behaves

`EdgeRobotRuntime` (its own container, one per robot, id from `ROBOT_ID`):

1. connects to the offloading-manager **as the robot** (`/ws/robots/{id}`) and on startup asks to
   offload its aggregate computation (self `change_status`, `aggregate=true`);
2. when **NOT offloaded** it OWNS its robot in the distributed field: it computes only that node's
   round (using neighbours' exports received over MQTT), publishes the node's export back, and drives
   the robot; when **offloaded** it owns nothing — the central runtime computes & drives it and the
   edge instance is idle (you still see it perceive the world in the monitor).

The manager uses the `only_delete` decision module, which handles the robot WebSocket handshake.

## Quick start

```bash
support-script/demo.sh 5 6      # edge runtime for robot 5, 6 emulated robots
```

Brings up (via compose): broker, dashboard, edge-monitor, central runtime, offloading-manager,
neighborhood-system; then the robot emulation and an `emerge-edge` container for robot 5. Then:

- open the **dashboard** (http://localhost:5173/), select robot 5, click **Switch to Edge**;
- watch its card on the **edge monitor** (http://localhost:5174/) start computing.

Tear down with `support-script/demo.sh --down`.

## Manual steps (instead of demo.sh)

```bash
# core services (broker, dashboard, edge-monitor, central runtime, offloading-manager, neighborhood)
docker compose up -d --build mqtt-broker dashboard edge-monitor aggregate-runtime offloading-manager neighborhood-system

# robots
cd robot-emulation && poetry run python src/robot_emulation/main.py --robots 6 --mqtt mqtt://localhost:1883 --world-size 5.0

# edge runtime for robot 5 (standalone image, kept out of docker-compose on purpose)
cd .. && docker build -f aggregate-runtime/Dockerfile.edge -t emerge-edge-runtime .
docker run --rm --network project-emerge-network \
  -e ROBOT_ID=5 -e MQTT_URL=tcp://mqtt-broker:1883 \
  -e OFFLOADING_MANAGER_HOST=offloading-manager -e OFFLOADING_MANAGER_PORT=8000 \
  emerge-edge-runtime
```

## Notes

- Both edge and central never drive the same robot at once: the central owns only the enabled robots,
  the edge owns its robot only while not offloaded, and the manager flips the state.
- A disabled (offloaded) robot is **never hidden** from the others: it stays in the environment as a
  neighbour, and its export is exchanged over MQTT, so the whole swarm sees one coherent field.
