# VisionSystem

Indoor localization system for ArUco markers based on fixed overhead cameras (one to four), guided ChArUco calibration, multi-camera fusion, and real-time MQTT publication of position and heading.

---

## Table of Contents

1. [Requirements and Installation](#1-requirements-and-installation)
2. [Graphical Calibration Panel](#2-graphical-calibration-panel)
   - [2.1 Camera Count and Allocation on this PC](#21-camera-count-and-allocation-on-this-pc)
3. [Camera Configuration](#3-camera-configuration)
   - [3.1 Visual Source Selection](#31-visual-source-selection)
   - [3.2 Field of View and Zoom Adjustment](#32-field-of-view-and-zoom-adjustment)
4. [Intrinsic Calibration (ChArUco)](#4-intrinsic-calibration-charuco)
   - [4.1 Board Generation and Printing](#41-board-generation-and-printing)
   - [4.2 Video Source Verification (Probe)](#42-video-source-verification-probe)
   - [4.3 Interactive Intrinsics Wizard](#43-interactive-intrinsics-wizard)
   - [4.4 Calibration from Photo Directory](#44-calibration-from-photo-directory)
5. [Reference Marker Map](#5-reference-marker-map)
   - [5.1 Map from Single Camera or 2D Photo](#51-map-from-single-camera-or-2d-photo)
   - [5.2 Anchor Marker Selection (`--mode anchors`)](#52-anchor-marker-selection---mode-anchors)
   - [5.3 Large Arena: Multi-Camera Stitching](#53-large-arena-multi-camera-stitching)
   - [5.4 Selection and Rotation of Frame Origin](#54-selection-and-rotation-of-frame-origin)
6. [Extrinsic Calibration](#6-extrinsic-calibration)
7. [Runtime Execution](#7-runtime-execution)
   - [7.0 Execution with Docker Compose](#70-execution-with-docker-compose)
   - [7.1 Local Execution (Single PC)](#71-local-execution-single-pc)
   - [7.2 Distributed Mode (One PC per Camera)](#72-distributed-mode-one-pc-per-camera)
   - [7.3 Server Launch and Debug GUI](#73-server-launch-and-debug-gui)
   - [7.4 Docker: Server and Nodes on Separate PCs](#74-docker-server-and-nodes-on-separate-pcs)
8. [Simulator](#8-simulator)
   - [8.1 Synthetic Simulation](#81-synthetic-simulation)
   - [8.2 Live Simulation with Real Webcams](#82-live-simulation-with-real-webcams)
9. [MQTT Protocol and Configuration](#9-mqtt-protocol-and-configuration)
   - [9.1 Topic Table](#91-topic-table)
   - [9.2 Complete Configuration Example](#92-complete-configuration-example)
   - [9.3 Automatic Targets and Anchor Frame](#93-automatic-targets-and-anchor-frame)
   - [9.4 Inter-Camera Consistency](#94-inter-camera-consistency)
   - [9.5 Tracker Filter](#95-tracker-filter)
10. [Diagnostics and Geometric Conventions](#10-diagnostics-and-geometric-conventions)
11. [Physical Acceptance Testing](#11-physical-acceptance-testing)

---

## 1. Requirements and Installation

The project requires **Python >= 3.12** and manages dependencies and lockfiles using `uv`.

```bash
UV_CACHE_DIR=/tmp/visionsystem-uv-cache uv sync --all-groups
UV_CACHE_DIR=/tmp/visionsystem-uv-cache uv run pytest
```

---

## 2. Graphical Calibration Panel

The entire workflow from Sections 3 through 7 is also available as a unified Tkinter panel: a single window showing which cameras are already calibrated and with what reprojection error, launching each phase with the correct arguments.

```bash
uv run vision-calibrate-gui --config config.local.json --board-format a3
# or
make calibrate-gui CONFIG=config.local.json BOARD_FORMAT=a3
```

The panel works on the roster stored in the configuration. `--cameras` forces it before the window opens, rewriting `config.local.json` exactly as saving step 1 would. It takes the same two spellings as the step's field: the camera ids, or just how many.

```bash
# The arena has these two cameras, whatever the file said before.
uv run vision-calibrate-gui --config config.local.json --cameras cam_2 cam_3

# Three cameras, names left to the panel: cam_0, cam_1, cam_2.
uv run vision-calibrate-gui --config config.local.json --cameras 3
# or
make calibrate-gui CAMERAS=3
```

Requires `tkinter` (Debian/Ubuntu: `sudo apt install python3-tk`). Panel labels are in English; OpenCV wizard windows display their step guidance, and the panel console streams the raw output of invoked child commands.

The left-hand navigation follows the order of this guide. Each step indicates a filled dot when executable and a hollow dot when prerequisites are missing:

| Menu Item | Reference Section | Launched Commands |
| --- | --- | --- |
| `1 Deployment` | [2.1 Camera Count and Allocation on this PC](#21-camera-count-and-allocation-on-this-pc) | writes `config.local.json` and `config.local.setup.json`, opens `vision-server-gui` |
| `2 Cameras` | [3. Camera Configuration](#3-camera-configuration) | `vision-select-cameras`, `vision-configure-cameras`, probe |
| `3 Board` | [4.1 Board Generation and Printing](#41-board-generation-and-printing) | `vision-calibrate board` |
| `4 Intrinsics` | [4.3](#43-interactive-intrinsics-wizard) and [4.4](#44-calibration-from-photo-directory) | `vision-calibrate intrinsics`, folder calibration |
| `5 Reference markers` | [5. Reference Marker Map](#5-reference-marker-map) | `vision-reference-map`, `vision-reference-stitch`, `vision-select-origin` |
| `6 Extrinsics` | [6. Extrinsic Calibration](#6-extrinsic-calibration) | `vision-calibrate extrinsics` |
| `7 Runtime` | [7. Runtime Execution](#7-runtime-execution) | `vision-localizer`, `vision-server-gui` |

Step 2 works on the cameras attached to this PC. Its **Cameras to reconfigure** field overrides that for the three buttons: `all` covers the whole roster (useful when one machine sets the arena up), or name the ones to redo, e.g. `cam_0 cam_2`.

Selecting a blocked step displays **why** it is blocked, distinguishing between scenarios that require different actions: a camera missing intrinsics (missing file), a camera whose settings changed after calibration (file exists but is invalidated: recalibrate intrinsics), a calibration that failed quality thresholds, or fewer than the required 3 reference markers for extrinsics.

**Process Execution Architecture.** Interactive phases (camera selection, field of view, intrinsics/extrinsics wizards, reference map, stitching, origin selection) run as **child processes**: they open their OpenCV window with standard keyboard controls (`SPACE`, `BACKSPACE`, `R`, `ENTER`, `ESC`), streaming stdout to the panel console. `Cancel` terminates them cleanly. Non-interactive phases (board generation, source probe, folder calibration) run directly inside the panel, with per-camera progress and per-image feedback.

> **Exit code is not the final verdict.** Canceling a wizard with `ESC` exits with a non-zero code, and a multi-camera session that aborted on the third camera still recorded the first two. The panel re-reads `calibrations/` whenever a process terminates: **the status table is authoritative**, not the exit code.

The `Board` column checks the artifact's `board_checksum` against the selected format: calibrating with an A3 board and opening the panel in A4 marks rows as `stale` with note `differs (a4)`. Always ensure `--board-format` matches the physically printed board.

The panel writes `config.local.json` **only from step 1**, and only when clicking `Save deployment`: other steps continue to delegate to `vision-select-cameras` and `vision-configure-cameras`. All standalone CLI commands described in subsequent sections remain fully functional — the panel is a client of these commands, and the CLI remains the primary interface on headless hosts.

### 2.1 Camera Count and Allocation on this PC

Step `1 Deployment` resolves two distinct deployment questions:

| Field | Meaning |
| --- | --- |
| `Deployment` | `single-pc` (all webcams attached to this host, one process fuses) or `distributed` (one `vision-node` per camera, across multiple PCs) |
| `Cameras in the deployment` | **the cameras the whole arena has**, named — `cam_2 cam_3` — or simply counted — `4`. This is the shared roster: must be identical on all PCs |
| `Base configuration` | the example anything not yet configured is cut from; `config.example.json` by default |
| `Webcams attached to this PC` | **which of those cameras are plugged in here**, e.g. `cam_1 cam_2`. Empty = all |
| `MQTT broker` / `Broker port` | broker endpoint as seen from this machine |

Clicking `Save deployment` writes two adjacent files:

- `config.local.json` — the **shared roster**. Copied identically to each PC in the deployment: server and nodes must agree on `site`, `system_id`, and the `cameras` list, or the coordinator will indefinitely await observations from unassigned cameras.
- `config.local.setup.json` — the **local configuration** that stays on this machine: deployment mode, locally attached cameras, broker host. Never read by remote nodes.

**The roster field is the roster.** Typing `cam_2 cam_3` makes the deployment exactly those two, in that order — the same thing `vision-select-cameras --cameras cam_2 cam_3` has always done, which is why there is no separate "remove these" list: dropping a camera is naming the ones that stay. Typing a number instead is the shorthand for "this many, you pick the names": it keeps the cameras already configured and fills up from `cam_0`. Either way the field comes back showing ids, so saving twice cannot cut the roster twice.

Cameras that survive are **never overwritten**: ID, video source, and settings are carried across untouched, and files in `calibrations/` are left alone, so reshaping the roster never discards a calibration. A camera that is named but does not exist yet is created.

**New cameras come from the example, not from library defaults.** Setting the roster is starting from scratch, so whatever does not exist yet is cut from `Base configuration` (`config.example.json`). With no configuration file at all, the whole document comes from there — `site`, ArUco dictionary, marker sizes, fusion settings. With one already present, each added `cam_N` takes its settings and its documented source from the example (`cam_0` → 5, `cam_1` → 1, `cam_2` → 2, `cam_3` → 4). A source another camera already holds is replaced with the first free one, so reshaping the roster cannot put two cameras on one device. Nothing already configured is ever overwritten, and a missing example is reported rather than fatal: the roster falls back to the built-in defaults. The path is editable in the field, or with `--base`.

The `Config` field in the header may point at a file that **does not exist yet** — step 1 is the step that creates it. Until it does, the later steps stay blocked and say to come back here.

**Example 1+2+1.** A 4-camera arena across 3 PCs: server PC has `cam_0`, first client has `cam_1` and `cam_2`, second client has `cam_3`. On **every** PC, set `Cameras in the deployment = cam_0 cam_1 cam_2 cam_3` (or just `4`) and `Deployment = distributed`; only `Webcams attached to this PC` varies (`cam_0`, `cam_1 cam_2`, and `cam_3` respectively).

From that point on, the panel operates **only on local cameras**: probe, FOV, intrinsics, and extrinsics see `cam_1 cam_2` without waiting for the others, while the Step 1 table continues displaying the full roster with the `Attached to` column distinguishing `this PC` from `another PC`. Step `7 Runtime` is the only step requiring all roster cameras to be calibrated, because the server reconstructs observations across all cameras: remote extrinsics must be copied to the server (see [7.4](#74-docker-server-and-nodes-on-separate-pcs)).

`Show launch commands` prints ready-to-run `make` invocations to the console — one per local camera with the correct broker address — and `Open server panel` opens the server monitoring GUI ([7.3](#73-server-launch-and-debug-gui)) **even before any calibrations exist**, which is where inter-process communication diagnostics are inspected.

---

## 3. Camera Configuration

> Corresponds to `2 Cameras` in the [graphical calibration panel](#2-graphical-calibration-panel).

### 3.1 Visual Source Selection

The system supports arenas with **2, 3, or 4 cameras**: the template defines up to four logical slots (`cam_0`..`cam_3`), but the saved configuration contains only the cameras actually present. Default OpenCV sources are `5`, `1`, `2`, `4` at 1920×1080 @ 30 FPS. To associate them visually:

```bash
uv run vision-select-cameras \
  --base config.example.json \
  --output config.local.json
```

- Click a video frame and press `1`, `2`, `3`, or `4` to assign it to the corresponding logical camera (`cam_0`..`cam_3`).
- `C` clears assignments, `R` rescans connected devices, `ENTER` saves the file, `ESC` cancels.
- **Fewer than four cameras:** assign only available cameras; unassigned slots are omitted from the output. To declare the roster explicitly (recommended so keybindings match physical cameras):
  ```bash
  uv run vision-select-cameras --base config.example.json \
    --cameras cam_0 cam_1 --output config.local.json
  ```
  With two cameras, the window displays two slots mapped to keys `1` and `2`.
- **Distributed deployment: assign only cameras on this PC.** `--cameras` *reduces* the roster, which on a client with two of four cameras would strip the other two from the file. To assign local cameras while preserving the global roster, use `--local-cameras`:
  ```bash
  uv run vision-select-cameras --base config.local.json --output config.local.json --force \
    --local-cameras cam_1 cam_2
  ```
  The window displays all four cameras but accepts only keys `2` and `3`: the others remain labeled `(other PC)` with the sources their respective PCs chose. This is identical to what Step `2 Cameras` executes in distributed mode.
- To restrict scanning to known device indices:
  ```bash
  uv run vision-select-cameras --sources 5 1 2 4 --output config.local.json
  ```
- Available as a subcommand: `uv run vision-calibrate select-cameras`.
- **Stable video sources.** On Linux, `/dev/videoN` device indices depend on enumeration order: a camera that was `source 3` at calibration time may become `source 1` after rebooting or reconnecting a USB hub, causing the node to fail with `cannot open source 3`. On save, assigned numeric indices are automatically substituted with the stable `/dev/v4l/by-id/...` symlink whenever available. Remote cameras are preserved as-is. Use `--keep-source-numbers` to retain numeric indices.

### 3.2 Field of View and Zoom Adjustment

```bash
uv run vision-configure-cameras --config config.local.json
```

- Displays all configured cameras simultaneously (two, three, or four). Click any view and use `+`/`-` to adjust digital zoom (`digital_zoom`).
- `N` sets standard preset `1.75x`; `W` restores full wide-angle `1.00x`.
- `A` applies selected zoom to all configured cameras; `ENTER` saves configuration (bumping `revision`), `ESC` cancels.
- Save to an alternate file:
  ```bash
  uv run vision-configure-cameras --config config.local.json --output config.con-fov.json
  ```
- Publish directly to the MQTT broker (`config/set`):
  ```bash
  uv run vision-configure-cameras --config config.local.json --publish-mqtt
  ```

> **Note:** Any FOV or resolution change modifies intrinsic camera matrices. After modifying zoom, intrinsic and extrinsic calibrations must be repeated.

---

## 4. Intrinsic Calibration (ChArUco)

> Corresponds to `2 Board` and `3 Intrinsics` in the [graphical calibration panel](#2-graphical-calibration-panel).

### 4.1 Board Generation and Printing

Generates a high-resolution printable PDF (with a 100 mm verification ruler) and source PNG:

```bash
# A4 format (6x8 board, 30 mm squares, 22 mm markers)
uv run vision-calibrate board --format a4 --output calibration-assets

# A3 format (7x9 board, 40 mm squares, 30 mm markers, recommended for larger arenas)
uv run vision-calibrate board --format a3 --output calibration-assets

# Both formats
uv run vision-calibrate board --format both --output calibration-assets
```

Print the PDF at **100% scale (no page scaling)**, measure the 100 mm ruler with a physical scale, and mount the sheet on a rigid, completely flat surface.

### 4.2 Video Source Verification (Probe)

Verifies that all video sources respond at the configured resolution, measuring actual FPS, pixel format, and duplicate frames:

```bash
uv run vision-calibrate --config config.local.json probe
```

### 4.3 Interactive Intrinsics Wizard

Runs on-screen interactive guidance in real time (tracking motion, orientation, scale, and stability):

```bash
# Calibrate all cameras sequentially (A4 board by default)
uv run vision-calibrate --config config.local.json intrinsics --camera all

# With A3 board
uv run vision-calibrate --config config.local.json intrinsics --camera all --board-format a3

# Single camera
uv run vision-calibrate --config config.local.json intrinsics --camera cam_0 --board-format a3
```

- Pose capture is automatic when the board is held still in a valid, unseen position.
- Keyboard controls: `SPACE` toggles auto-capture, `BACKSPACE` removes latest sample, `R` resets all samples, `ENTER` confirms and saves, `ESC` cancels.

### 4.4 Calibration from Photo Directory

If ChArUco images have already been acquired as image files:

```bash
# Single folder
uv run vision-calibrate-folder \
  --config config.local.json \
  --input photos/cam_0 \
  --camera cam_0 \
  --board-format a3 \
  --output calibrations

# Multi-camera hierarchy (subfolders cam_0/, cam_1/, cam_2/, cam_3/)
uv run vision-calibrate-folder \
  --config config.local.json \
  --input photos \
  --board-format a3 \
  --output calibrations
```

Also available as a subcommand:
```bash
uv run vision-calibrate --config config.local.json from-folder --input photos --board-format a3
```

---

## 5. Reference Marker Map

> Corresponds to `4 Reference markers` in the [graphical calibration panel](#2-graphical-calibration-panel).

To compute camera extrinsics, the system requires 3D coordinates for fixed reference markers placed on the arena floor or work plane.

### 5.1 Map from Single Camera or 2D Photo

If a single camera captures the entire reference marker area:

```bash
uv run vision-reference-map \
  --config config.local.json \
  --width-m 5.40 \
  --height-m 3.80 \
  --marker-size-m 0.15 \
  --plane-z-m 0.0 \
  --output reference-markers.json
```

- In the live mosaic, select camera with `1`–`4` and press `SPACE` to capture.
- In the interactive window, click in order: **origin (0,0)**, **+X point**, **opposite corner (+X,+Y)**, and **+Y point**.
- Using a static image or floor plan:
  ```bash
  uv run vision-reference-map \
    --image room.jpg \
    --config config.local.json \
    --width-m 5.40 \
    --height-m 3.80 \
    --marker-size-m 0.15 \
    --output reference-markers.json
  ```

### 5.2 Anchor Marker Selection (`--mode anchors`)

Instead of clicking arbitrary points, the 4 clicks snap to the centers of 4 ArUco "anchor" markers:

```bash
uv run vision-reference-map \
  --config config.local.json \
  --mode anchors \
  --width-m 0.60 \
  --height-m 0.60 \
  --marker-size-m 0.07 \
  --output reference-markers.json
```

- With `--auto-capture`, snapshot occurs automatically as soon as markers stabilize:
  ```bash
  uv run vision-reference-map \
    --config config.local.json \
    --mode anchors \
    --auto-capture \
    --marker-size-m 0.07 \
    --output reference-markers.json
  ```
- With explicit `--anchor-ids` (origin, +X, +X+Y, +Y), the map is computed with no manual clicks:
  ```bash
  uv run vision-reference-map \
    --config config.local.json \
    --mode anchors \
    --anchor-ids 13 15 19 18 \
    --width-m 0.60 \
    --height-m 0.60 \
    --marker-size-m 0.07 \
    --output reference-markers.json
  ```

### 5.3 Large Arena: Multi-Camera Stitching

In large arenas where no single camera sees all reference markers, `vision-reference-stitch` merges partial views into a single world plane via global bundle adjustment (non-linear least squares):

**Geometric Requirements:**
- 4 anchor markers placed at the corners of the arena with known metric distances;
- At least two reference markers visible to each camera;
- Adjacent cameras sharing at least one common reference marker (fully connected graph);
- Coplanar markers with identical outer black border size (`--marker-size-m`).

```bash
# Coordinated live capture with visual selection of anchor rectangle
uv run vision-reference-stitch \
  --config config.local.json \
  --camera all \
  --marker-size-m 0.09 \
  --output reference-markers.json \
  --force
```

- Captures automatically when all cameras are stable and connected.
- On the displayed top-down homography, click near the 4 markers: `origin`, `+X`, `+X+Y`, `+Y`, then press `ENTER`. The terminal prompts for physical X and Y distances in meters (can be supplied with `--width-m` and `--height-m`).
- When anchor IDs are known in advance:
  ```bash
  uv run vision-reference-stitch \
    --config config.local.json \
    --camera all \
    --anchor-ids 100 101 102 103 \
    --width-m 12.0 \
    --height-m 8.0 \
    --marker-size-m 0.09 \
    --output reference-markers.json \
    --force
  ```
- Pre-acquired photos can also be processed:
  ```bash
  uv run vision-reference-stitch \
    --config config.local.json \
    --images photo/cam_0.jpg photo/cam_1.jpg photo/cam_2.jpg photo/cam_3.jpg \
    --select-frame \
    --marker-size-m 0.09 \
    --output reference-markers.json \
    --force
  ```

### 5.4 Selection and Rotation of Frame Origin

To redefine which of the 4 anchors serves as `(0,0,0)`, rigidly rotating the coordinate system:

```bash
# From live view
uv run vision-select-origin --config config.local.json

# From existing image
uv run vision-select-origin \
  --image reference-markers-capture.jpg \
  --config config.local.json
```

Click the desired anchor and press `ENTER`. Configuration updates while preserving a right-handed coordinate frame.

> **Important:** Modifying the world origin requires new extrinsic calibrations for all cameras (intrinsic calibrations remain valid).

---

## 6. Extrinsic Calibration

> Corresponds to `5 Extrinsics` in the [graphical calibration panel](#2-graphical-calibration-panel), which locks the step until valid intrinsics and at least 3 reference markers are present.

Computes the 3D pose of each camera relative to the world frame (`world_from_camera`):

```bash
# Calibrate all cameras using reference markers from config / MQTT
uv run vision-calibrate --config config.local.json extrinsics --camera all

# Using separate reference markers file
uv run vision-calibrate --config config.local.json extrinsics --camera all \
  --reference-markers reference-markers.json
```

- The live window displays detected IDs, matching references, outliers, and reprojection error.
- Collects 100 stable samples and computes pose using RANSAC + Levenberg-Marquardt refinement.
- For rapid bench testing with relaxed RANSAC reprojection thresholds (3 to 30 px):
  ```bash
  uv run vision-calibrate --config config.local.json extrinsics --camera all \
    --reference-markers reference-markers.json --allow-low-quality
  ```

---

## 7. Runtime Execution

> Corresponds to `6 Runtime` in the [graphical calibration panel](#2-graphical-calibration-panel).

### 7.0 Execution with Docker Compose

From repository root, the full hardware stack automatically starts VisionSystem:

```bash
docker compose up --build -d
docker compose logs --follow vision
```

The container uses `config.local.json` and the `calibrations/` directory from this application, accesses host Linux V4L2 USB camera devices, and communicates with the broker at `mosquitto:1883`. State and diagnostic logs are persisted in named Docker volumes `vision-state` and `vision-diagnostics`.

To run the robot emulator without launching VisionSystem, run `make up-simulator` from repository root instead. That target stops any running `vision` container before launching the simulator stack.

This service runs the **monolith** `vision-localizer` (all cameras on a single PC); from this folder the same stack is started with `make all`. For distributed Docker deployments — `make server` on the fusion PC, `make client` on each camera PC — see [7.4](#74-docker-server-and-nodes-on-separate-pcs).

### 7.1 Local Execution (Single PC)

Starts the localizer opening all configured cameras, performing tag detection, drift checks, and fusion:

```bash
# Production mode (connected to MQTT broker)
export VISION_MQTT_HOST=localhost
export VISION_MQTT_PORT=1883
uv run vision-localizer --config config.local.json

# Offline mode with visual debug (camera mosaic + 2D world map)
uv run vision-localizer --config config.local.json --no-mqtt --debug

# Output JSON pose stream to stdout on each cycle
uv run vision-localizer --config config.local.json --no-mqtt --print-poses
```

### 7.2 Distributed Mode (One PC per Camera)

Scalable architecture for expansive arenas: 2 to 4 edge PCs (each running a single camera) publish lightweight observation payloads (~8 KB/s) over MQTT to a central fusion server. Any number of nodes may participate: **all nodes and the server must share the exact same camera roster** (`--cameras`), otherwise the coordinator marks missing roster slots as offline.

#### Clock Synchronization Requirements:
All nodes and the server must synchronize system clocks via **NTP/Chrony** with offset strictly under 2–3 ms (multi-camera fusion uses UTC timestamps in nanoseconds).

```bash
# Install chrony (Debian/Ubuntu)
sudo apt install chrony && sudo systemctl enable --now chronyd
```

#### On Each Camera Node PC (`cam_X`):
1. Associate camera declaring the full deployment roster (e.g. 2-camera arena `cam_0` and `cam_1`; on this PC assign only `cam_X`):
   ```bash
   uv run vision-select-cameras --base config.example.json \
     --cameras cam_0 cam_1 --camera cam_X --output config.local.json
   ```
   Omitting `--cameras` preserves all four slots from the template: in a 2- or 3-camera deployment, unassigned slots would remain in the roster.

   On a PC hosting **more than one** camera (e.g. `cam_1` and `cam_2` in a 4-camera arena), define the global roster once (via step 1 of the panel, [2.1](#21-camera-count-and-allocation-on-this-pc), or with `--cameras`), then assign local devices with `--local-cameras cam_1 cam_2`, which does **not** strip `cam_0` and `cam_3` from the configuration. Using `--cameras cam_1 cam_2` on that PC would produce a 2-camera roster and cause the server to wait for missing cameras indefinitely.
2. Calibrate intrinsics and extrinsics for this camera:
   ```bash
   uv run vision-calibrate --config config.local.json intrinsics --camera cam_X --board-format a3
   uv run vision-calibrate --config config.local.json extrinsics --camera cam_X
   ```
3. Launch camera node:
   ```bash
   export VISION_MQTT_HOST=192.168.1.10
   uv run vision-node --camera cam_X
   ```

#### On Fusion Server PC (No Local Cameras):
```bash
export VISION_MQTT_HOST=192.168.1.10
uv run vision-server --debug
```

The server shares the same roster as nodes: with 2 cameras, fusion solves over two observations per tag; with 3 or 4, residuals improve while the pipeline remains identical.

### 7.3 Server Launch and Debug GUI

In distributed deployments, processes operate independently and operational debugging focuses on *who is communicating with whom*. The graphical panel launches the server on the local machine and displays end-to-end chain status plus a 2D arena map in a single interface:

```bash
# Server on the same machine as the broker
uv run vision-server-gui --config config.local.json

# Broker (and camera nodes) on another machine
uv run vision-server-gui --config config.local.json --mqtt-host 192.168.1.10
```

Requires `tkinter` (`sudo apt install python3-tk`). Panel labels are in English.

The panel is structured into three sections:

1. **Server launch** — MQTT host/port, `config`, `calibrations`, state cache, and flags `--debug`, `--no-mqtt`, `--verbose`. `Start server` launches `vision-server` as a child process in a separate session; `Stop server` issues `SIGTERM` followed by `SIGKILL` after 10 s without blocking the UI. MQTT parameters become `VISION_MQTT_HOST`/`VISION_MQTT_PORT` for the child process.
2. **Deployment cameras** — one row per roster camera showing pipeline stages from left to right: *Node* (node process publishing metrics), *Camera up* (webcam open with active source), *Frames* (captured frames), *Obs. published* (observations published by node), *Server* and *Obs. received* (observations received by coordinator), observation age, and calibration state. The `Notes` column includes literal error messages (e.g. `cannot open source 3`) for direct hardware diagnosis.
3. **World view / Console** — two tabs: 2D arena map (calibrated cameras in orange, reference markers in purple, tracked robots with trails and heading vectors) and server console streaming MQTT events (`MISSING_CALIBRATION`, `CALIBRATION_DRIFT`, `CAMERA_DISAGREEMENT`, ...).

The world view renders fused poses published on `<base_topic>/pose/<tag_id>` rather than recalculating them, acting as an independent monitor of what the server publishes to downstream clients. Hollow markers indicate predicted or stale poses (> 1.5 s).

The panel listens passively to the broker without publishing: when the server runs on a remote host, point the panel to the shared broker to monitor execution without clicking `Start server`.

Quick Diagnostic Reference:

| Symptom | Typical Cause |
| --- | --- |
| Note `no vision-node is publishing for this camera` | Node not started (`make client CAMERA=cam_X`), or publishing to a different broker |
| Node `●`, note `the webcam does not open` + `cannot open source N` | Configured source missing on this PC: device index changed. Run `vision-select-cameras --local-cameras cam_X` to save stable `/dev/v4l/by-id/...` path ([3.1](#31-visual-source-selection)) |
| Node `●`, note `the webcam is open but delivers no frame` | Device busy by another process, or USB bandwidth exhausted (multiple 1080p uncompressed streams on single controller) |
| Node `●`, Camera up `●`, Server `○` | Mismatched broker or `base_topic` (`site`/`system_id`), or firewall blocking port 1883 |
| Node `○`, Server `●` | Node publishing observations but not metrics: process initializing or log level modified |
| Both `●`, high latency / age | Clocks not synchronized via NTP/Chrony, or network congestion |
| `calibration missing on the node PC` | Missing `calibrations/cam_X.json` **on node PC**: node performs no detection |
| `calibration missing on the server` | Missing `calibrations/cam_X.json` **on server PC**: observations arrive but cannot be fused ([7.4](#74-docker-server-and-nodes-on-separate-pcs)) |
| Note `drift: recalibrate` | Camera physically shifted: repeat extrinsic calibration |
| World view empty with cameras online | No mobile markers in view, or incorrect marker `size_m` (observations rejected on reprojection threshold) |

### 7.4 Docker: Server and Nodes on Separate PCs

All Docker operations for VisionSystem are defined in `apps/vision/Makefile`. Key variables include `CAMERA` (target camera ID), `MQTT_HOST`/`MQTT_PORT` (broker address for node container), `GUI_MQTT_HOST`, and `CONFIG`.

| Command | Function |
| --- | --- |
| `make all` | Full stack on single PC: broker, dashboard, and monolith `vision-localizer` |
| `make server` | Distributed deployment, server side: stops monolith and starts broker + `vision-server` |
| `make client CAMERA=cam_0` | Single camera node; broker on the same PC |
| `make client CAMERA=cam_1 MQTT_HOST=192.168.1.10` | Remote camera node targeting server PC broker |
| `make gui` | Host-side server launch and debug panel |
| `make logs` / `make logs-client CAMERA=cam_1` | Logs for server / node |
| `make ps`, `make down`, `make down-client CAMERA=cam_1` | Status and termination |

Root shortcuts include `make vision-all`, `make vision-server`, `make vision-client CAMERA=... MQTT_HOST=...`, and `make vision-gui`.

`make all` and `make server` are mutually exclusive: `make server` stops the `vision` monolith container before startup to avoid duplicate pose publications.

#### Prerequisites Across All Hosts

```bash
# 1. Synchronized system clocks: Docker containers inherit host clock
sudo apt install chrony && sudo systemctl enable --now chronyd
chronyc tracking          # offset must remain strictly below 2-3 ms

# 2. Shared roster and base_topic in config.local.json on each PC
#    (identical site, system_id, and cameras list)
jq '{site, system_id, cameras: [.cameras[].id]}' config.local.json
```

#### PC A — Broker, Fusion Server, and Local Node

```bash
cd apps/vision

make server                 # broker + fusion server
make client CAMERA=cam_0    # camera connected directly to this PC
make logs                   # or: make logs-client CAMERA=cam_0

sudo ufw allow 1883/tcp     # broker must accept connections from external nodes
ip -4 addr show | grep inet # IP address to configure on remote nodes
```

The `vision-server` service is defined in root `compose.yaml` under profile `distributed`. The node uses `compose.node.yaml` within `apps/vision`, instantiated per camera as Compose project `vision-node-<camera>`.

#### PC B — Remote Camera Node Only

Each remote PC requires a copy of `apps/vision` with its **local** `config.local.json` (matching PC A's roster) and local camera calibrations; broker and server run on PC A.

```bash
cd apps/vision

make client CAMERA=cam_1 MQTT_HOST=192.168.1.10
make logs-client CAMERA=cam_1 MQTT_HOST=192.168.1.10
make down-client CAMERA=cam_1 MQTT_HOST=192.168.1.10
```

`MQTT_HOST` is required for each command to point to the server broker. To deploy without git repository access on PC B, transfer the built image:

```bash
# On PC A
docker save vision-node-cam_1-vision-node | ssh user@pc-b docker load
```

#### Calibrations: Server Requires All Extrinsics

The coordinator reconstructs observations using extrinsic matrices located in **its own** `calibrations/` directory: PC A must contain the calibration JSON for every camera in the roster, including those attached to remote PCs.

```bash
# Option 1: direct copy from node PC to server PC
scp calibrations/cam_1.json user@pc-a:~/Project-Emerge-system/apps/vision/calibrations/

# Option 2: MQTT distribution (bridge automatically persists payload to disk)
mosquitto_pub -h 192.168.1.10 -t 'vision/<site>/<system_id>/calibration/cam_1/set' \
  -q 1 -f calibrations/cam_1.json
```

#### Deployment Verification

```bash
# From PC A: inspect fused poses published by the server
mosquitto_sub -h localhost -t 'vision/+/+/pose/+' -v | head

# Coordinator metrics (active cameras, received observation rates)
mosquitto_sub -h localhost -t 'vision/+/+/metrics' -v | head

# Launch monitoring GUI
make gui
```

Clean Shutdown:

```bash
make down                                  # broker + server (PC A)
make down-client CAMERA=cam_0              # local node
make down-client CAMERA=cam_1 MQTT_HOST=192.168.1.10   # remote node (PC B)
```

---

## 8. Simulator

### 8.1 Synthetic Simulation

Tests the complete distributed pipeline (4 synthetic nodes + MQTT broker + fusion server) on a single machine without attached cameras:

```bash
uv run vision-simulate --config config.local.json
```

- Automatically starts an `eclipse-mosquitto:2` Docker container if no broker is active at `localhost:1883`.
- Opens a 2D visualization window (*VisionSystem - world*) rendering fused tracking for synthetic target (default ID 23).
- Options:
  - `--tag-id 23`: simulated mobile tag ID.
  - `--hz 25.0`: observation publication rate.
  - `--noise-px 0.5`: Gaussian pixel noise standard deviation.
  - `--broker none`: use an existing external broker.
  - `--keep-broker`: retain Docker broker container on exit.

### 8.2 Live Simulation with Real Webcams

Spawns 4 `vision-node` processes and `vision-server` locally, connecting to physical webcams:

```bash
uv run vision-simulate --live --config config.local.json --node-debug
```

- `--cameras cam_0 cam_1`: launch a subset of cameras.
- `--allow-low-quality`: bypass automatic camera exclusion during drift tests.

---

## 9. MQTT Protocol and Configuration

Default base topic: `vision/<site>/<system_id>`.

### 9.1 Topic Table

| Topic | QoS | Retained | Direction | Description |
|---|:---:|:---:|:---:|---|
| `config/set` | 1 | Yes | Inbound | Publish full system configuration update |
| `config/state` | 1 | Yes | Outbound | Currently active system configuration |
| `config/result` | 1 | No | Outbound | Configuration validation outcome (`accepted: true/false`) |
| `calibration/<cam>/set` | 1 | Yes | Inbound | Publish calibration artifact for camera |
| `calibration/<cam>/state` | 1 | Yes | Outbound | Currently applied camera calibration artifact |
| `observations/<cam>` | 0 | No | Outbound (Node) | Raw ArUco corner detections from node |
| `pose/<tag_id>` | 0 | No | Outbound (Server) | Fused 3D pose in `world` frame |
| `camera/<cam>/status` | 1 | No | Outbound | Connection state, FPS, and calibration status |
| `metrics` | 0 | No | Outbound | Aggregate system performance metrics |
| `event` | 1 | No | Outbound | Drift notifications, errors, and system alerts |
| `status` | 1 | Yes | Outbound | System online status and Last Will & Testament |
| `/config/aruco-map` | 1 | Yes | Inbound | Global mapping: ArUco marker ID → robot device ID |
| `/pose/<device_id>` | 0 | No | Outbound | Dashboard-compatible pose for mapped markers |

Topics starting with `/` are global topics that omit the Vision base prefix. For each mobile tag, `pose/<tag_id>` continues publishing detailed 3D pose data. When `/config/aruco-map` associates that marker with a robot ID, Vision additionally publishes `/pose/<device_id>` formatted for dashboard telemetry (`x_m`, `y_m`, `heading_rad`, `speed_m_s`, `timestamp_us`) along with elevation, full orientation, linear/angular velocity, contributing camera IDs, reprojection error, and quality metric. `position_variance_m2` is not populated because VisionSystem does not synthesize an empirical position covariance.

### 9.2 Complete Configuration Example

```json
{
  "request_id": "config-init-01",
  "config": {
    "schema_version": 1,
    "revision": 1,
    "site": "default",
    "system_id": "indoor-01",
    "cameras": [
      {"id": "cam_0", "source": 5, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0},
      {"id": "cam_1", "source": 1, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0},
      {"id": "cam_2", "source": 2, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0},
      {"id": "cam_3", "source": 4, "width": 1920, "height": 1080, "fps": 30.0, "digital_zoom": 1.0}
    ],
    "aruco": {
      "dictionary": "DICT_4X4_50",
      "mobile_markers": [{"id": 23, "size_m": 0.12, "name": "robot"}],
      "auto_mobile_markers": {"enabled": false, "default_size_m": 0.12, "ignored_ids": []},
      "reference_markers": [
        {"id": 13, "size_m": 0.07, "position_m": [0.6, 0.0, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        {"id": 15, "size_m": 0.07, "position_m": [0.0, 0.5, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        {"id": 18, "size_m": 0.07, "position_m": [0.0, 0.0, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]},
        {"id": 19, "size_m": 0.07, "position_m": [0.6, 0.5, 0.0], "orientation_xyzw": [0.0, 0.0, 0.0, 1.0]}
      ],
      "anchor_frame": {
        "origin_id": 18,
        "x_axis_id": 13,
        "y_axis_id": 15,
        "opposite_id": 19,
        "x_distance_m": 0.6,
        "y_distance_m": 0.5,
        "plane_z_m": 0.0
      }
    },
    "fusion": {
      "window_ms": 40.0,
      "publish_hz": 20.0,
      "max_reprojection_error_px": 4.0,
      "max_camera_disagreement_m": 0.25,
      "max_fused_reprojection_error_px": 8.0,
      "huber_scale_px": 1.5,
      "tracker_filter": "one_euro",
      "one_euro_min_cutoff_hz": 2.0,
      "one_euro_beta": 5.0,
      "one_euro_derivative_cutoff_hz": 1.0,
      "tracker_position_gain": 0.65,
      "tracker_velocity_gain": 0.12,
      "tracker_orientation_gain": 0.55,
      "tracker_max_innovation_m": 0.15,
      "stale_after_ms": 250.0
    },
    "debug": {
      "mosaic": false,
      "world_view": false,
      "trail_seconds": 3.0
    }
  }
}
```

### 9.3 Automatic Targets and Anchor Frame

- **Automatic Targets (`auto_mobile_markers`)**: When enabled, any detected marker not listed in `reference_markers` or `ignored_ids` is tracked as a mobile target using `default_size_m`.
- **Anchor Frame (`anchor_frame`)**: Constrains the positions of the 4 primary reference markers strictly onto the corners of the specified rectangle, ensuring an orthogonal and stable world coordinate system.

### 9.4 Inter-Camera Consistency

Each camera observing a tag computes an independent estimate in world coordinates. Before solving the joint pose, fusion evaluates consensus across individual camera estimates and rejects outliers:

- `max_camera_disagreement_m`: Maximum allowed distance between a single camera's estimate and the median of all other estimates. Cameras exceeding this threshold are excluded from that fusion cycle and listed under `rejected_by` in the pose message.
- `max_fused_reprojection_error_px`: Maximum RMS reprojection error for the joint multi-camera solution. If remaining observations cannot be explained by a single rigid pose, the pose update is dropped and the tag continues on dead reckoning.

Frequent camera rejections indicate structural issues rather than sensor noise: a `CAMERA_DISAGREEMENT` event is emitted. Typical root causes are incorrect `size_m` (including via `auto_mobile_markers.default_size_m`) or invalid extrinsic calibration. An incorrect physical size shifts a camera's estimate along its optical axis: while each camera view appears stable in isolation, their rays do not intersect and the fused pose oscillates.

### 9.5 Tracker Filter

The tracker defaults to a **One Euro Filter** on 3D position. When stationary, the filter attenuates jitter; during rapid maneuvers, it automatically increases its cutoff frequency to eliminate lag:

- `one_euro_min_cutoff_hz`: Quiescent baseline cutoff; higher values increase responsiveness at the expense of noise filtering.
- `one_euro_beta`: Speed adaptation coefficient; higher values reduce lag during fast transients.
- `one_euro_derivative_cutoff_hz`: Cutoff frequency for estimated velocity used in cutoff adaptation and dead-reckoning extrapolation.

The defaults (`2.0`, `5.0`, `1.0`) provide responsive tracking for 20–30 FPS video feeds. The legacy alpha-beta filter remains selectable with `"tracker_filter": "alpha_beta"`, parameterized by `tracker_position_gain` and `tracker_velocity_gain`.

---

## 10. Diagnostics and Geometric Conventions

- **World Axis Convention**: Metric, right-handed coordinate frame with **XY on the floor** and **Z pointing upward**.
- **Orientation**: Normalized quaternions represented as `(x, y, z, w)`.
- **Structured Diagnostic Logging**: Written automatically to `diagnostics/vision-system.jsonl` (JSON Lines format, rotated at 20 MB). Records full telemetry, accepted/rejected UVC controls, matrix condition numbers, stability metrics, and outlier rejection causes.
- **Drift Monitoring**: At runtime, if reference markers observed by a camera deviate by more than 2 cm or 2° for more than 2 consecutive seconds, the camera is automatically removed from fusion and a `CALIBRATION_DRIFT` event is dispatched over MQTT.

---

## 11. Physical Acceptance Testing

The test suite validates algorithmic correctness, geometric transforms, and protocol handling:

```bash
uv run pytest
uv run ruff check
```

For on-site physical acceptance, verify metric tracking accuracy by placing calibrated tags at known measured distances (2, 3, and 4 meters) along the arena floor grid. Low reprojection pixel residuals alone do not guarantee metric floor-plane accuracy.
