# VisionSystem subproject commands.
#
#   make all                                       everything on one PC, single fusion process
#   make server                                    broker + fusion server (distributed deployment)
#   make client CAMERA=cam_0                       camera node on the same PC as the server
#   make client CAMERA=cam_1 MQTT_HOST=192.168.1.10   camera node on a remote PC
#   make gui                                       server launch and debug GUI panel (host)
#   make calibrate-gui                             calibration panel (host)
#
# How many cameras the deployment has and which are attached to THIS PC are configured
# in step 1 of the calibration panel, which writes config.local.json (shared roster)
# and config.local.setup.json (local part) and prints the commands below pre-populated.
.PHONY: all server client gui calibrate-gui logs logs-server logs-client ps down down-client test

# Camera managed by `make client`; one Compose project per camera so multiple
# nodes can coexist on the same PC.
CAMERA ?= cam_0
# Broker host seen by the node containers: host-gateway when broker runs on the
# same PC, server PC IP when the node is remote.
MQTT_HOST ?= host.docker.internal
MQTT_PORT ?= 1883
# Broker seen by host processes (GUI, manual tests).
GUI_MQTT_HOST ?= localhost
GUI_MQTT_PORT ?= $(MQTT_PORT)
CONFIG ?= config.local.json
# ChArUco board format used during calibration: a4 or a3.
BOARD_FORMAT ?= a4
# How many cameras the deployment has. Set it to force the roster in the panel
# (make calibrate-gui CAMERAS=3); empty keeps whatever the configuration says.
CAMERAS ?=

REPO_ROOT := $(abspath $(CURDIR)/../..)
COMPOSE := docker compose -f $(REPO_ROOT)/compose.yaml
NODE_COMPOSE = docker compose -f compose.node.yaml -p vision-node-$(CAMERA)

NODE_ENV = VISION_MQTT_HOST=$(MQTT_HOST) VISION_MQTT_PORT=$(MQTT_PORT) VISION_CAMERA=$(CAMERA)

# Full stack on a single machine: broker, dashboard, and the monolith
# `vision-localizer`, which opens all roster cameras and fuses alone.
all:
	$(COMPOSE) up --build -d

# Distributed deployment, server side: no camera open here, observations arrive
# from nodes. The monolith is stopped because it would publish the same poses.
server:
	$(COMPOSE) stop vision
	$(COMPOSE) --profile distributed up --build -d mosquitto vision-server

# Distributed deployment, client side: one camera, observations over MQTT.
client:
	@echo "node $(CAMERA) -> broker $(MQTT_HOST):$(MQTT_PORT)"
	$(NODE_ENV) $(NODE_COMPOSE) up --build -d

# Tkinter panel: start/stop server, display roster and world view.
# Runs on host (needs X/Wayland), not in a container.
gui:
	uv run vision-server-gui --config $(CONFIG) --mqtt-host $(GUI_MQTT_HOST) \
		--mqtt-port $(GUI_MQTT_PORT)

# Tkinter calibration panel: displays camera status and launches steps
# (camera selection, board, intrinsics, references, extrinsics). Runs on host.
calibrate-gui:
	uv run vision-calibrate-gui --config $(CONFIG) --board-format $(BOARD_FORMAT) \
		$(if $(CAMERAS),--cameras $(CAMERAS))

logs: logs-server

logs-server:
	$(COMPOSE) logs --follow vision-server

logs-client:
	$(NODE_ENV) $(NODE_COMPOSE) logs --follow

ps:
	$(COMPOSE) ps
	$(NODE_ENV) $(NODE_COMPOSE) ps

down:
	$(COMPOSE) --profile distributed down

down-client:
	$(NODE_ENV) $(NODE_COMPOSE) down

test:
	uv run pytest
