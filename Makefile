.PHONY: bootstrap test build compose-config up down logs ps

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

down:
	docker compose down

logs:
	docker compose logs --follow

ps:
	docker compose ps
