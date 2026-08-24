SHELL := /bin/sh

.DEFAULT_GOAL := help

LAKEHOUSE_ENVIRONMENT ?= dev
LOCAL_UID ?= $(shell id -u)
DOCKER_GID ?= $(shell stat -c '%g' /var/run/docker.sock 2>/dev/null || printf '0')
AWS_IDENTITY_DIR ?= $(HOME)/.config/lakehouse/$(LAKEHOUSE_ENVIRONMENT)/aws
HOST_BIND_ADDRESS ?= 127.0.0.1
AIRFLOW_BASE_URL ?= http://$(HOST_BIND_ADDRESS):8080
RUNTIME_PARAMETER_PREFIX := /lakehouse/$(LAKEHOUSE_ENVIRONMENT)
LIGHTDASH_CLI_VERSION := 1.146.0
LIGHTDASH_CONTENT_DIRS := $(wildcard analytics/lightdash/projects/*/content)

export LAKEHOUSE_ENVIRONMENT
export LOCAL_UID
export DOCKER_GID
export AWS_IDENTITY_DIR
export HOST_BIND_ADDRESS
export AIRFLOW_BASE_URL

include make/infra.mk
include make/images.mk
include make/services.mk
include make/data.mk

.PHONY: help preflight lakehouse-validate lightdash-validate lint test compose-validate check

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-28s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

preflight: ## Verify local tools required by service operations.
	@command -v aws >/dev/null
	@command -v docker >/dev/null
	@command -v jq >/dev/null
	@docker compose version >/dev/null

lakehouse-validate: ## Validate settings and YAML contracts without AWS I/O.
	uv run --package lakehouse --extra cli python -m lakehouse.validate

lightdash-validate: ## Validate managed Lightdash content with the pinned CLI.
	@test "$$(lightdash --version | sed -n '1p')" = "$(LIGHTDASH_CLI_VERSION)" || { \
		printf '%s\n' "Lightdash CLI $(LIGHTDASH_CLI_VERSION) is required."; exit 1; \
	}
	@set -eu; for content in $(LIGHTDASH_CONTENT_DIRS); do \
		lightdash lint --path "$$content"; \
	done

lint: ## Run formatting, linting, and static type checks.
	bash -n lakehouse/emr/release/publish
	sh -n infra/runtime/host/install-aws-cli \
		infra/runtime/host/install-tailscale \
		infra/runtime/host/reconcile-docker-logging \
		infra/runtime/host/reconcile-metadata-backup \
		infra/runtime/identity/install-aws-signing-helper \
		infra/runtime/identity/workload-identities \
		infra/runtime/delivery/deploy-component \
		infra/runtime/delivery/pull-image \
		infra/runtime/cloudflare/deploy \
		infra/runtime/cloudflare/sync-secret \
		infra/runtime/postgres/backup \
		infra/runtime/postgres/initialize-secrets \
		infra/runtime/postgres/deploy \
		infra/runtime/postgres/restore \
		observability/signoz/deploy/deploy \
		observability/signoz/collector/deploy \
		lakehouse/emr/release/package \
		automation/airflow/deploy/deploy \
		automation/airflow/deploy/initialize-secrets \
		automation/airflow/deploy/reconcile \
		apps/arxiv_inspector/deploy/deploy \
		apps/arxiv_inspector/deploy/reconcile \
		analytics/lightdash/deploy/deploy \
		analytics/lightdash/deploy/initialize-secrets \
		analytics/lightdash/deploy/reconcile \
		analytics/lightdash/deploy/sync-ci-secret \
		analytics/dbt-project/deploy/deploy \
		ocr/deploy/deploy
	uv run ruff format --check .
	uv run ruff check .
	uv run --all-packages --all-extras pyright
	uv run --project automation/airflow pyright --project automation/airflow
	uv run pyright --project lakehouse/emr

test: ## Run unit tests.
	uv run --all-packages --all-extras pytest -m "not integration"
	uv run --project automation/airflow pytest \
		-c automation/airflow/pyproject.toml automation/airflow/bundle/tests

compose-validate: ## Validate self-hosted service Compose files.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) config --quiet
	$(AIRFLOW_COMPOSE_CONFIG) config --quiet
	$(INSPECTOR_COMPOSE) config --quiet
	$(LIGHTDASH_COMPOSE_CONFIG) config --quiet
	$(CLOUDFLARE_COMPOSE_CONFIG) config --quiet
	COLLECTOR_IMAGE=validation:local COLLECTOR_CONFIG_SHA256=validation \
		COLLECTOR_HOSTNAME=validation-host PG_MONITOR_PASSWORD=validation docker compose \
		--project-name signoz-collection-agent \
		-f observability/signoz/collector/compose.yaml config --quiet

check: ## Run the complete local quality gate.
	uv lock --check
	uv lock --check --project analytics/dbt-project/runtime
	uv lock --check --project automation/airflow
	uv lock --check --project lakehouse/emr
	uv lock --check --directory ocr/glm_ocr
	$(MAKE) lakehouse-validate
	$(MAKE) dbt-validate
	$(MAKE) lightdash-validate
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) terraform-fmt
	$(MAKE) terraform-validate
	$(MAKE) compose-validate
	$(MAKE) images-check
