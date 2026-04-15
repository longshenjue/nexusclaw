.PHONY: dev sandbox down logs ps build-sandbox build

dev:
	cp .env.dev .env
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

sandbox:
	cp .env.sandbox .env
	docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d

down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down
	@docker ps -aq --filter "name=claw-sandbox" | xargs -r docker rm -f 2>/dev/null || true
	@echo "Sandbox containers cleaned up"

build:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml build backend
	docker build -t claw-sandbox ./docker/sandbox/

logs:
	docker compose logs -f backend

ps:
	docker compose ps

build-sandbox:
	docker build -t claw-sandbox ./docker/sandbox/
