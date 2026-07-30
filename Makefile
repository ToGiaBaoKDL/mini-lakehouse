SHELL := /bin/sh

.DEFAULT_GOAL := help

PROJECT_NAME ?= lakehouse-platform
RELEASE ?= $(shell git rev-parse HEAD)
AIRFLOW_COMPOSE := docker compose --project-name $(PROJECT_NAME)-airflow -f compose.airflow.yaml
INSPECTOR_COMPOSE := docker compose --project-name $(PROJECT_NAME)-document-inspector -f compose.document-inspector.yaml
TERRAFORM_DIR := infra/terraform/environments/dev
TERRAFORM_STATE_DIR := infra/terraform/bootstrap/state
EMR_BUILD_DIR := dist/emr
AWS_CONFIG_DIR ?= ./.aws
AWS_CONFIG_FILE ?= $(AWS_CONFIG_DIR)/config
AWS_SHARED_CREDENTIALS_FILE ?= $(AWS_CONFIG_DIR)/credentials
export AWS_CONFIG_FILE
export AWS_SHARED_CREDENTIALS_FILE

.PHONY: help setup preflight platform-validate lint test check compose-validate \
	terraform-fmt terraform-state-init terraform-state-plan terraform-state-apply \
	terraform-init terraform-validate terraform-plan terraform-apply terraform-destroy \
	build-airflow build-document-inspector \
	airflow-up airflow-down airflow-logs airflow-dags \
	document-inspector-up document-inspector-down document-inspector-logs start down ps \
	catalog-apply catalog-validate dbt-deps dbt-validate dbt-build \
	emr-jobs-package emr-jobs-publish-preflight emr-jobs-publish \
	ocr-kaggle-runner-publish

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-28s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .env without overwriting an existing file.
	@test -f .env || cp .env.example .env

preflight: ## Verify local tools and configuration.
	@test -s .env || { printf '%s\n' "Missing .env; run 'make setup' first."; exit 1; }
	@test -d "$(AWS_CONFIG_DIR)" || { printf '%s\n' "Set AWS_CONFIG_DIR to a readable AWS config directory."; exit 1; }
	@command -v docker >/dev/null
	@command -v jq >/dev/null
	@docker compose version >/dev/null

platform-validate: ## Validate settings and YAML contracts without AWS I/O.
	uv run --extra cli python -m lakehouse_platform.platform.validate

lint: ## Run formatting, linting, and static type checks.
	uv run ruff format --check .
	uv run ruff check .
	uv run --extra orchestration --extra catalog --extra document-inspector --extra ocr \
		pyright
	uv run pyright --project jobs/emr

test: ## Run unit tests.
	uv run --extra catalog --extra document-inspector --extra ocr \
		pytest -m "not integration"

compose-validate: ## Validate self-hosted service Compose files.
	docker compose --env-file .env.example \
		--project-name $(PROJECT_NAME)-airflow -f compose.airflow.yaml config --quiet
	docker compose --env-file .env.example \
		--project-name $(PROJECT_NAME)-document-inspector \
		-f compose.document-inspector.yaml config --quiet

check: ## Run the local quality gate.
	uv lock --check
	uv lock --check --project jobs/emr
	uv lock --check --directory ocr/runners/glm_ocr
	$(MAKE) platform-validate
	$(MAKE) dbt-validate
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) terraform-fmt
	$(MAKE) terraform-validate
	$(MAKE) compose-validate

terraform-fmt: ## Check Terraform formatting.
	terraform -chdir=infra/terraform fmt -check -recursive

terraform-state-init: ## Initialize the one-time remote-state bootstrap stack.
	terraform -chdir=$(TERRAFORM_STATE_DIR) init

terraform-state-plan: terraform-state-init ## Plan the remote-state bootstrap stack.
	terraform -chdir=$(TERRAFORM_STATE_DIR) plan

terraform-state-apply: terraform-state-init ## Create the versioned remote-state bucket.
	terraform -chdir=$(TERRAFORM_STATE_DIR) apply

terraform-init: ## Initialize the dev Terraform environment.
	@test -n "$${TF_STATE_BUCKET:-}" || { printf '%s\n' "TF_STATE_BUCKET is required."; exit 1; }
	terraform -chdir=$(TERRAFORM_DIR) init -backend-config="bucket=$${TF_STATE_BUCKET}"

terraform-validate: ## Initialize without remote state and validate all Terraform roots.
	terraform -chdir=$(TERRAFORM_STATE_DIR) init -backend=false -lockfile=readonly
	terraform -chdir=$(TERRAFORM_STATE_DIR) validate
	terraform -chdir=$(TERRAFORM_DIR) init -backend=false -lockfile=readonly
	terraform -chdir=$(TERRAFORM_DIR) validate

terraform-plan: terraform-init ## Plan the AWS dev platform.
	terraform -chdir=$(TERRAFORM_DIR) plan

terraform-apply: terraform-init ## Apply the reviewed AWS dev platform plan.
	terraform -chdir=$(TERRAFORM_DIR) apply

terraform-destroy: terraform-init ## Destroy the AWS dev platform.
	terraform -chdir=$(TERRAFORM_DIR) destroy

build-airflow: ## Build the self-hosted Airflow image.
	$(AIRFLOW_COMPOSE) build

build-document-inspector: ## Build the Document Inspector image.
	$(INSPECTOR_COMPOSE) build

airflow-up: preflight ## Start self-hosted Airflow.
	$(AIRFLOW_COMPOSE) up -d --build --wait --wait-timeout 300

airflow-down: ## Stop self-hosted Airflow while preserving metadata.
	$(AIRFLOW_COMPOSE) down --remove-orphans

airflow-logs: ## Follow Airflow logs.
	$(AIRFLOW_COMPOSE) logs --follow --tail=200 $(ARGS)

airflow-dags: ## List parsed Airflow DAGs.
	$(AIRFLOW_COMPOSE) exec -T airflow-scheduler airflow dags list

document-inspector-up: preflight ## Start the read-only Document Inspector.
	$(INSPECTOR_COMPOSE) up -d --build --wait --wait-timeout 180

document-inspector-down: ## Stop Document Inspector.
	$(INSPECTOR_COMPOSE) down --remove-orphans

document-inspector-logs: ## Follow Document Inspector logs.
	$(INSPECTOR_COMPOSE) logs --follow --tail=200

start: ## Start all self-hosted services.
	$(MAKE) airflow-up
	$(MAKE) document-inspector-up

down: ## Stop all self-hosted services.
	$(MAKE) document-inspector-down
	$(MAKE) airflow-down

ps: ## Show self-hosted service state.
	$(AIRFLOW_COMPOSE) ps
	$(INSPECTOR_COMPOSE) ps

catalog-apply: ## Apply Glue/Iceberg YAML contracts with PyIceberg.
	AWS_PROFILE="$${CATALOG_ADMIN_AWS_PROFILE:-lakehouse-dev-catalog-admin}" \
		uv run --extra catalog python -m lakehouse_platform.platform.catalog.admin apply

catalog-validate: ## Validate Glue/Iceberg state against YAML contracts.
	AWS_PROFILE="$${CATALOG_ADMIN_AWS_PROFILE:-lakehouse-dev-catalog-admin}" \
		uv run --extra catalog python -m lakehouse_platform.platform.catalog.admin validate

ocr-kaggle-runner-publish: ## Publish a new immutable Kaggle OCR runner Dataset version.
	uv run --project ocr --extra kaggle-publish \
		python ocr/runners/kaggle/glm_ocr/publish.py

dbt-deps: ## Install locked dbt packages.
	uv run --extra analytics dbt deps --project-dir dbt/analytics

dbt-validate: dbt-deps ## Parse dbt without accessing AWS data.
	DBT_QUERY_RESULTS_URI=s3://validation/query-results \
		DBT_ANALYTICS_URI=s3://validation \
		AWS_REGION=ap-southeast-1 \
		uv run --extra analytics dbt parse \
			--project-dir dbt/analytics \
			--profiles-dir dbt/analytics \
			--no-partial-parse \
			--show-all-deprecations

dbt-build: ## Build analytics with runtime references loaded from SSM.
	@test -d dbt/analytics/dbt_packages/dbt_utils || { \
		printf '%s\n' "Missing locked dbt packages; run 'make dbt-deps' first."; exit 1; \
	}
	@set -eu; \
		command -v aws >/dev/null; \
		command -v jq >/dev/null; \
		AWS_PROFILE_NAME="$${DBT_AWS_PROFILE:-lakehouse-dev-dbt-transformer}"; \
		PARAMETER_PREFIX="/lakehouse/$${LAKEHOUSE_ENVIRONMENT:-dev}"; \
		PARAMETERS="$$(aws --profile "$${AWS_PROFILE_NAME}" ssm get-parameters \
			--names \
				"$${PARAMETER_PREFIX}/athena/dbt_output_uri" \
				"$${PARAMETER_PREFIX}/storage/analytics_uri" \
			--output json)"; \
		QUERY_RESULTS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er \
			--arg name "$${PARAMETER_PREFIX}/athena/dbt_output_uri" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		ANALYTICS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er \
			--arg name "$${PARAMETER_PREFIX}/storage/analytics_uri" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		AWS_PROFILE="$${AWS_PROFILE_NAME}" \
		DBT_QUERY_RESULTS_URI="$${QUERY_RESULTS_URI}" \
		DBT_ANALYTICS_URI="$${ANALYTICS_URI}" \
		uv run --extra analytics dbt build \
			--project-dir dbt/analytics --profiles-dir dbt/analytics

emr-jobs-package: ## Build EMR artifacts in the matching EMR 7.13 runtime.
	rm -rf $(EMR_BUILD_DIR)
	docker build \
		--platform linux/amd64 \
		--file jobs/emr/Dockerfile \
		--target artifacts \
		--output type=local,dest=$(EMR_BUILD_DIR) \
		.

emr-jobs-publish-preflight: ## Require a committed release and deployment tools.
	@test -z "$$(git status --porcelain)" || { \
		printf '%s\n' "Commit the worktree before publishing an EMR release."; exit 1; \
	}
	@command -v aws >/dev/null
	@command -v terraform >/dev/null

emr-jobs-publish: emr-jobs-publish-preflight emr-jobs-package ## Publish one versioned EMR release selected by RELEASE.
	@EMR_CODE_URI="$$(terraform -chdir=$(TERRAFORM_DIR) output -raw emr_artifacts_uri)/$(RELEASE)"; \
		EMR_CODE_PARAMETER="$$(terraform -chdir=$(TERRAFORM_DIR) output -raw emr_code_parameter_name)"; \
		EMR_DEPLOYER_PROFILE="$${EMR_DEPLOYER_AWS_PROFILE:-lakehouse-dev-emr-deployer}"; \
		aws --profile "$${EMR_DEPLOYER_PROFILE}" s3 sync \
			$(EMR_BUILD_DIR)/ "$${EMR_CODE_URI}/" --only-show-errors; \
		aws --profile "$${EMR_DEPLOYER_PROFILE}" ssm put-parameter \
			--name "$${EMR_CODE_PARAMETER}" \
			--type String \
			--value "$${EMR_CODE_URI}" \
			--overwrite >/dev/null; \
		printf '%s\n' "Published EMR release $${EMR_CODE_URI}"
