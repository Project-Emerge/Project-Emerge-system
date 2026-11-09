.PHONY: bootstrap test build compose-config up up-core up-simulator down logs ps \
	vision-all vision-server vision-client vision-gui

CORE_SERVICES := dashboard aggregate-runtime neighborhood-system mosquitto
SIMULATOR_SERVICES := $(CORE_SERVICES) simulator

bootstrap:
	npm ci

test:
	npm test
	cd apps/aggregate-runtime && sbt test

build:
	npm run build
	cd apps/aggregate-runtime && sbt assembly

compose-config:
	docker compose config --quiet

up:
	docker compose up --build -d

# Everything except vision: pairs with the distributed vision-server/vision-client.
up-core:
	docker compose stop vision
	docker compose up --build -d $(CORE_SERVICES)

up-simulator:
	docker compose stop vision
	docker compose --profile simulator up --build -d $(SIMULATOR_SERVICES)

# Every profile plus every camera node started with `make vision-client`.
down:
	docker compose --profile distributed --profile simulator down --remove-orphans
	for project in $$(docker compose ls -aq --filter name=vision-node-); do \
		docker compose -f apps/vision/compose.node.yaml -p $$project down; \
	done

logs:
	docker compose logs --follow

ps:
	docker compose ps

# VisionSystem: the complete targets (and CAMERA/MQTT_HOST variables) are in
# apps/vision/Makefile; these are shortcuts from the root.
vision-all:
	$(MAKE) -C apps/vision all

vision-server:
	$(MAKE) -C apps/vision server

vision-client:
	$(MAKE) -C apps/vision client $(if $(CAMERA),CAMERA=$(CAMERA)) $(if $(MQTT_HOST),MQTT_HOST=$(MQTT_HOST))

vision-gui:
	$(MAKE) -C apps/vision gui
