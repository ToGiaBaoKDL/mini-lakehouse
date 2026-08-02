SHELL := /bin/sh

.DEFAULT_GOAL := help

RELEASE ?= $(shell git rev-parse HEAD)
LAKEHOUSE_ENVIRONMENT ?= dev
LOCAL_UID ?= $(shell id -u)
DOCKER_GID ?= $(shell stat -c '%g' /var/run/docker.sock 2>/dev/null || printf '0')
AWS_IDENTITY_DIR ?= $(HOME)/.config/lakehouse/$(LAKEHOUSE_ENVIRONMENT)/aws
HOST_BIND_ADDRESS ?= 127.0.0.1

AWS_TERRAFORM_DIR := infra/terraform/aws/environments/$(LAKEHOUSE_ENVIRONMENT)

export LAKEHOUSE_ENVIRONMENT
export LOCAL_UID
export DOCKER_GID
export AWS_IDENTITY_DIR
export HOST_BIND_ADDRESS

include make/infra.mk
include make/images.mk
include make/services.mk
include make/data.mk

.PHONY: help preflight platform-validate lint test compose-validate check

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-28s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

preflight: ## Verify local tools required by service operations.
	@command -v aws >/dev/null
	@command -v docker >/dev/null
	@command -v jq >/dev/null
	@docker compose version >/dev/null

platform-validate: ## Validate settings and YAML contracts without AWS I/O.
	uv run --package lakehouse --extra cli python -m lakehouse.validate

lint: ## Run formatting, linting, and static type checks.
	sh -n infra/runtime/install-aws-signing-helper infra/runtime/workload-identities
	uv run ruff format --check .
	uv run ruff check .
	uv run --all-packages --all-extras pyright
	uv run --project orchestration pyright --project orchestration
	uv run pyright --project jobs/emr

test: ## Run unit tests.
	uv run --all-packages --all-extras pytest -m "not integration"
	uv run --project orchestration pytest orchestration/tests

compose-validate: ## Validate self-hosted service Compose files.
	$(AIRFLOW_COMPOSE_CONFIG) config --quiet
	$(INSPECTOR_COMPOSE) config --quiet

check: ## Run the complete local quality gate.
	uv lock --check
	uv lock --check --project dbt/analytics
	uv lock --check --project orchestration
	uv lock --check --project jobs/emr
	uv lock --check --directory ocr/runners/glm_ocr
	$(MAKE) platform-validate
	$(MAKE) dbt-validate
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) terraform-fmt
	$(MAKE) terraform-validate
	$(MAKE) compose-validate
	$(MAKE) images-check
