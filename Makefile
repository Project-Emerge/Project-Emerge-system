.PHONY: bootstrap test build compose-config up up-simulator down logs ps \
	vision-all vision-server vision-client vision-gui

SIMULATOR_SERVICES := dashboard aggregate-runtime simulator neighborhood-system mosquitto

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

up-simulator:
	docker compose stop vision
	docker compose --profile simulator up --build -d $(SIMULATOR_SERVICES)

down:
	docker compose down

logs:
	docker compose logs --follow

ps:
	docker compose ps

# VisionSystem: i target completi (e le variabili CAMERA/MQTT_HOST) stanno in
# apps/vision/Makefile, questi sono solo scorciatoie dalla root.
vision-all:
	$(MAKE) -C apps/vision all

vision-server:
	$(MAKE) -C apps/vision server

vision-client:
	$(MAKE) -C apps/vision client $(if $(CAMERA),CAMERA=$(CAMERA)) $(if $(MQTT_HOST),MQTT_HOST=$(MQTT_HOST))

vision-gui:
	$(MAKE) -C apps/vision gui
