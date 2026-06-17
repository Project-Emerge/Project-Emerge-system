# Offloading Manager

Part of [Project Emerge](https://github.com/Project-Emerge/Project-Emerge-system.git) — a distributed robotics system. The Offloading Manager is responsible for deciding **which robots offload which computational tasks** to which modules, and for coordinating those decisions in real time.

---

## Overview

Robots in the Project Emerge swarm can offload specific computations (aruco detection, aggregate runtime, neighborhood tracking) to dedicated Docker-based modules. The Offloading Manager sits between the robots and the modules, monitoring system stress and deciding when to start or stop offloading for each robot.

```
Robots ←─── WebSocket ───→ Offloading Manager ←─── WebSocket ───→ Modules
                                    │
                              Docker Monitor
                          (polls container stats)
```

---

## Features

- **Real-time WebSocket communication** with robots and compute modules using JSON-RPC 2.0
- **Automatic stress monitoring** — polls Docker container stats at a configurable interval
- **Pluggable decision modules** — the offloading strategy is decoupled from the rest of the system
- **REST API** for querying system state and manually triggering offloading requests
- **Graceful lifecycle management** via FastAPI's lifespan context

---

## Architecture

### Core Components

| Component | Description |
|---|---|
| `Model` | Central facade; owns the registries and delegates to the decision module |
| `RobotRegistry` | Tracks connected robots and their current offloading state |
| `ModuleRegistry` | Tracks connected compute modules and their latest resource stats |
| `DecisionModule` | Abstract strategy for offloading decisions; currently `OnlyDeleteDecisionModule` |
| `ModuleMonitor` | Background task that polls Docker stats and triggers re-evaluation |

### Offloading Types

Each robot has an `OffloadingType` state with three boolean flags:

| Flag | Module |
|---|---|
| `aggregate` | Aggregate runtime (`project-emerge-aggregate-runtime`) |
| `aruco` | Aruco marker detection (`project-emerge-aruco-detector`) |
| `neighbor` | Neighborhood tracking (`project-emerge-neighborhood-system`) |

### Decision Strategy: `OnlyDeleteDecisionModule`

The current strategy is conservative: when a module's CPU or memory usage exceeds the configured threshold, it removes one robot from offloading in that module per evaluation cycle. It never proactively assigns new offloading.

---

## API

### REST Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Get offloading state of all robots |
| `GET` | `/{robot_id}` | Get offloading state of a single robot |
| `PUT` | `/{robot_id}` | Request an offloading state change for a robot |
| `GET` | `/stress/system` | Get CPU/memory stats for all modules and robot counts |

### WebSocket Endpoints

| Path | Description |
|---|---|
| `/ws/robots/{robot_id}` | Robot connection (JSON-RPC 2.0) |
| `/ws/position` | Aruco module connection |
| `/ws/aggregate` | Aggregate module connection |
| `/ws/neighbor` | Neighbor module connection |

### JSON-RPC Protocol

Robots and modules communicate over WebSocket using JSON-RPC 2.0.

**Manager → Robot** (request): ask the robot to accept/reject an offloading change
```json
{ "jsonrpc": 2.0, "method": "change_status", "params": { "id": 1, "type": { "aggregate": true, "aruco": false, "neighbor": false } }, "id": 0 }
```

**Robot → Manager** (self-initiated request): robot requests its own offloading change
```json
{ "jsonrpc": 2.0, "method": "change_status", "params": { "id": 1, "type": { "aruco": true, ... } }, "id": 5 }
```

**Manager → Module** (notification): notify a module to start/stop computing for a robot
```json
{ "jsonrpc": 2.0, "method": "change_status", "params": { "id": 1, "calc": true } }
```

---

## Configuration

All settings are read from environment variables (or a `.env` file):

| Variable | Default | Description |
|---|---|---|
| `ONLY_DELETE_DECISION_MAX_CPU` | `80` | CPU % threshold above which robots are removed from offloading |
| `ONLY_DELETE_DECISION_MAX_MEMORY` | `80` | Memory % threshold above which robots are removed from offloading |
| `CONTAINERS_NETWORK` | `project-emerge-network` | Docker network to monitor |
| `CONTAINERS_POLLING_INTERVAL` | `5` | Stats polling interval (seconds) |
| `ROBOT_RESPONSE_TIMEOUT` | `10` | Timeout (seconds) waiting for a robot to respond to a status change |

---

## Running with Docker

```bash
docker build -t offloading-manager . 
docker run -p 8000:8000 offloading-manager
```

The service is exposed on port `8000`.

---

## Development

### Requirements

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv)

### Running locally

```bash
uv run fastapi dev 
```

### Running tests

```bash
uv run pytest
```

Tests cover unit tests for registries and the decision module, REST API integration tests, and WebSocket protocol tests.

