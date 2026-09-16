# Comandi del sottoprogetto VisionSystem.
#
#   make all                                       tutto su un PC, un solo processo di fusione
#   make server                                    broker + server di fusione (deployment distribuito)
#   make client CAMERA=cam_0                       nodo camera sullo stesso PC del server
#   make client CAMERA=cam_1 MQTT_HOST=192.168.1.10   nodo camera su un PC remoto
#   make gui                                       pannello di avvio e debug (gira sull'host)
.PHONY: all server client gui logs logs-server logs-client ps down down-client test

# Camera gestita da `make client`; un progetto Compose per camera, cosi' sullo
# stesso PC possono convivere piu' nodi.
CAMERA ?= cam_0
# Host del broker visto dai container del nodo: host-gateway quando il broker
# gira sullo stesso PC, l'IP del PC server quando il nodo e' remoto.
MQTT_HOST ?= host.docker.internal
MQTT_PORT ?= 1883
# Host del broker visto dai processi sull'host (GUI, test manuali).
GUI_MQTT_HOST ?= localhost
CONFIG ?= config.local.json

REPO_ROOT := $(abspath $(CURDIR)/../..)
COMPOSE := docker compose -f $(REPO_ROOT)/compose.yaml
NODE_COMPOSE = docker compose -f compose.node.yaml -p vision-node-$(CAMERA)

NODE_ENV = VISION_MQTT_HOST=$(MQTT_HOST) VISION_MQTT_PORT=$(MQTT_PORT) VISION_CAMERA=$(CAMERA)

# Stack completo su una sola macchina: broker, dashboard e il monolite
# `vision-localizer`, che apre tutte le camere del roster e fonde da solo.
all:
	$(COMPOSE) up --build -d

# Deployment distribuito, lato server: nessuna camera aperta qui, le osservazioni
# arrivano dai nodi. Il monolite viene fermato perche' pubblicherebbe le stesse pose.
server:
	$(COMPOSE) stop vision
	$(COMPOSE) --profile distributed up --build -d mosquitto vision-server

# Deployment distribuito, lato client: una camera, osservazioni via MQTT.
client:
	@echo "nodo $(CAMERA) -> broker $(MQTT_HOST):$(MQTT_PORT)"
	$(NODE_ENV) $(NODE_COMPOSE) up --build -d

# Pannello Tkinter: avvia/ferma il server, mostra il roster e la vista world.
# Gira sull'host (serve X/Wayland), non in un container.
gui:
	uv run vision-server-gui --config $(CONFIG) --mqtt-host $(GUI_MQTT_HOST)

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
