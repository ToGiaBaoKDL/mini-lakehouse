SHELL := /bin/sh

.DEFAULT_GOAL := help

PROJECT_NAME ?= mini-lakehouse
CORE_COMPOSE := docker compose --project-name $(PROJECT_NAME) -f compose.core.yaml
COMPOSE := $(CORE_COMPOSE) -f compose.prefect.yaml
THIRD_PARTY_SERVICES := postgres object-store object-store-bootstrap polaris-admin polaris trino \
	redis prefect-server

.PHONY: help setup preflight config validate lint test check pull build build-core \
	build-orchestration start-core start up-core up down restart clean reset ps \
	ps-all logs logs-follow smoke wait-prefect-deploy prefect-deployments \
	prefect-deploy reconcile

help: ## Show the available project commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create the local environment file without overwriting an existing one.
	@test -f .env || cp .env.example .env
	@printf '%s\n' "Local environment is ready. Place the AIStor license at ./minio.license."

preflight: ## Verify files and tools required for a local deployment.
	@test -s .env || { printf '%s\n' "Missing .env; run 'make setup' first."; exit 1; }
	@test -s minio.license || { printf '%s\n' "Missing or empty minio.license."; exit 1; }
	@command -v docker >/dev/null || { printf '%s\n' "Docker is required."; exit 1; }
	@docker compose version >/dev/null

config: ## Render and validate the complete Compose configuration.
	$(COMPOSE) config --quiet

validate: config ## Validate declarative lakehouse contracts and settings.
	uv run lakehouse validate

lint: ## Run formatting, linting, and static type checks.
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright

test: ## Run unit tests only.
	uv run pytest -m "not integration"

check: ## Run the same non-integration quality gate as CI.
	uv lock --check
	uv lock --check --directory runners/kaggle/glm_ocr
	$(MAKE) lint
	$(MAKE) test
	uv run dbt parse --project-dir dbt/analytics --profiles-dir dbt/analytics
	$(MAKE) config

pull: preflight ## Pull all pinned third-party service images.
	$(COMPOSE) pull $(THIRD_PARTY_SERVICES)

build: ## Build local images sequentially to keep peak resource usage bounded.
	$(MAKE) build-core
	$(MAKE) build-orchestration

build-core: preflight ## Build only the local core runtime image.
	$(CORE_COMPOSE) build lakehouse-bootstrap

build-orchestration: preflight ## Build only the local orchestration image.
	$(COMPOSE) build prefect-worker

start-core: preflight ## Start core services from existing images without building.
	$(CORE_COMPOSE) up -d --no-build --remove-orphans --wait --wait-timeout 300

start: preflight ## Start all services from existing images without building.
	$(COMPOSE) up -d --no-build --remove-orphans --wait --wait-timeout 300
	$(MAKE) wait-prefect-deploy

up-core: ## Build and start only the core data plane.
	$(MAKE) build-core
	$(MAKE) start-core

up: ## Build and start the complete local stack.
	$(MAKE) build
	$(MAKE) start

down: ## Stop the complete stack while preserving named volumes.
	$(COMPOSE) down --remove-orphans

restart: ## Recreate the complete stack while preserving named volumes.
	$(MAKE) down
	$(MAKE) start

clean: ## Destroy the complete local stack, including all named volumes.
	$(COMPOSE) down --volumes --remove-orphans

reset: ## Destroy all local state, pull pinned images, and deploy a clean stack.
	$(MAKE) clean
	$(MAKE) pull
	$(MAKE) up

ps: ## Show the complete stack status.
	$(COMPOSE) ps

ps-all: ## Show running and completed one-shot containers.
	$(COMPOSE) ps --all

logs: ## Print recent stack logs (use ARGS="service").
	$(COMPOSE) logs --tail=200 $(ARGS)

logs-follow: ## Follow stack logs (use ARGS="service").
	$(COMPOSE) logs --follow --tail=200 $(ARGS)

smoke: preflight ## Verify AIStor, Polaris, Trino, Prefect, and bucket bootstrap.
	@curl --fail --silent http://localhost:9000/minio/health/live >/dev/null
	@curl --fail --silent http://localhost:8182/q/health/ready >/dev/null
	@curl --fail --silent http://localhost:8080/v1/info >/dev/null
	@curl --fail --silent http://localhost:4200/api/health >/dev/null
	$(COMPOSE) run --rm --no-deps object-store-bootstrap
	$(COMPOSE) exec -T trino trino --execute "SHOW SCHEMAS FROM prod"

wait-prefect-deploy: preflight ## Wait for Prefect deployment registration.
	@container_id="$$( $(COMPOSE) ps --all --quiet prefect-deploy )"; \
		test -n "$$container_id" || { printf '%s\n' "Prefect deployment container not found."; exit 1; }; \
		exit_code="$$(docker wait "$$container_id")"; \
		test "$$exit_code" -eq 0 || { printf 'Prefect deployment failed with exit code %s.\n' "$$exit_code"; exit "$$exit_code"; }

prefect-deployments: preflight ## List registered Prefect deployments.
	$(COMPOSE) exec -T prefect-worker prefect deployment ls

reconcile: preflight ## Reconcile catalog, namespaces, and access in a running stack.
	$(COMPOSE) run --rm --no-deps lakehouse-bootstrap

prefect-deploy: preflight ## Register all Prefect flow deployments.
	$(COMPOSE) run --rm --no-deps prefect-deploy
