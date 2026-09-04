.PHONY: bootstrap test build compose-config up up-simulator down logs ps

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
