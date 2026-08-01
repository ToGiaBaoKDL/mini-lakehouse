SHELL := /bin/sh

.DEFAULT_GOAL := help

RELEASE ?= $(shell git rev-parse HEAD)
LAKEHOUSE_ENVIRONMENT ?= dev
LOCAL_UID ?= $(shell id -u)
DOCKER_GID ?= $(shell stat -c '%g' /var/run/docker.sock 2>/dev/null || printf '0')
AIRFLOW_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-airflow
CATALOG_ADMIN_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-catalog-admin
DBT_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-dbt-transformer
ARXIV_INSPECTOR_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-arxiv-inspector
EMR_DEPLOYER_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-emr-deployer
IMAGE_PUBLISHER_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-image-publisher
OCR_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-ocr-worker
AIRFLOW_COMPOSE := docker compose --project-name airflow -f compose.airflow.yaml
AIRFLOW_COMPOSE_CONFIG := AIRFLOW_DB_PASSWORD=unused AIRFLOW_FERNET_KEY=unused AIRFLOW_JWT_SECRET=unused $(AIRFLOW_COMPOSE)
INSPECTOR_COMPOSE := docker compose --project-name arxiv-inspector -f compose.arxiv-inspector.yaml
TERRAFORM_DIR := infra/terraform/environments/$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_STATE_DIR := infra/terraform/bootstrap/state
EMR_BUILD_DIR := dist/emr
PARAMETER_PREFIX := /lakehouse/$(LAKEHOUSE_ENVIRONMENT)
AIRFLOW_BOOTSTRAP_PARAMETER := $(PARAMETER_PREFIX)/secrets/airflow_bootstrap_id
AWS_CONFIG_DIR ?= $(HOME)/.aws
export LAKEHOUSE_ENVIRONMENT
export LOCAL_UID
export DOCKER_GID
export AIRFLOW_AWS_PROFILE
export ARXIV_INSPECTOR_AWS_PROFILE
export OCR_AWS_PROFILE
export AWS_CONFIG_DIR

.PHONY: help preflight platform-validate lint test check compose-validate \
	terraform-fmt terraform-state-init terraform-state-plan terraform-state-apply \
	terraform-init terraform-validate terraform-plan terraform-apply terraform-destroy \
	airflow-build arxiv-inspector-build ocr-worker-build images-build \
	airflow-up airflow-down airflow-logs airflow-dags \
	arxiv-inspector-up arxiv-inspector-down arxiv-inspector-logs \
	services-up services-down services-ps \
	ecr-login release-preflight ecr-publish ecr-deploy \
	catalog-apply catalog-validate dbt-deps dbt-validate dbt-build \
	emr-jobs-package emr-jobs-publish-preflight emr-jobs-publish \
	airflow-bootstrap-init ocr-kaggle-runner-publish

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-28s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Quality

preflight: ## Verify local tools and configuration.
	@test -r "$(AWS_CONFIG_DIR)/config" || { \
		printf '%s\n' "Set AWS_CONFIG_DIR to a directory containing a readable config file."; \
		exit 1; \
	}
	@command -v aws >/dev/null
	@command -v docker >/dev/null
	@command -v jq >/dev/null
	@docker compose version >/dev/null

platform-validate: ## Validate settings and YAML contracts without AWS I/O.
	uv run --package lakehouse --extra cli python -m lakehouse.validate

lint: ## Run formatting, linting, and static type checks.
	uv run ruff format --check .
	uv run ruff check .
	uv run --all-packages --all-extras pyright
	uv run --project orchestration pyright --project orchestration
	uv run pyright --project jobs/emr

test: ## Run unit tests.
	uv run --all-packages --all-extras pytest -m "not integration"

compose-validate: ## Validate self-hosted service Compose files.
	$(AIRFLOW_COMPOSE_CONFIG) config --quiet
	$(INSPECTOR_COMPOSE) config --quiet

check: ## Run the local quality gate.
	uv lock --check
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

# AWS infrastructure

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

# Container images and releases

airflow-build: ## Build the local Airflow image.
	docker build --target airflow --tag airflow:local .

arxiv-inspector-build: ## Build the local ArXiv Inspector image.
	docker build \
		--target arxiv-inspector \
		--tag arxiv-inspector:local \
		.

ocr-worker-build: ## Build the isolated local OCR task image.
	docker build \
		--target ocr-worker \
		--tag ocr-worker:local \
		--tag ocr-worker:runtime \
		.

images-build: airflow-build arxiv-inspector-build ocr-worker-build ## Build all local images.

release-preflight: preflight ## Require a committed release and initialized Terraform state.
	@test -z "$$(git status --porcelain)" || { \
		printf '%s\n' "Commit the worktree before publishing or deploying a release."; \
		exit 1; \
	}
	@command -v terraform >/dev/null
	@terraform -chdir=$(TERRAFORM_DIR) output -json container_repository_urls >/dev/null

ecr-login: preflight ## Authenticate Docker to the environment ECR registry.
	@command -v terraform >/dev/null
	@set -eu; \
		REPOSITORIES="$$(terraform -chdir=$(TERRAFORM_DIR) \
			output -json container_repository_urls)"; \
		REGISTRY="$$(printf '%s' "$${REPOSITORIES}" | jq -er '.airflow' | cut -d/ -f1)"; \
		aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" \
			ecr get-login-password \
			| docker login --username AWS --password-stdin "$${REGISTRY}" >/dev/null; \
		printf '%s\n' "Authenticated Docker to $${REGISTRY}."

ecr-publish: release-preflight ecr-login ## Publish immutable service images for RELEASE.
	@set -eu; \
		REPOSITORIES="$$(terraform -chdir=$(TERRAFORM_DIR) \
			output -json container_repository_urls)"; \
		printf '%s' "$${REPOSITORIES}" | jq -er 'keys[]' | while read -r SERVICE; do \
			REPOSITORY="$$(printf '%s' "$${REPOSITORIES}" \
				| jq -er --arg service "$${SERVICE}" '.[$$service]')"; \
			if aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" \
				ecr describe-images --repository-name "$${REPOSITORY##*/}" \
				--image-ids imageTag="$(RELEASE)" >/dev/null 2>&1; then \
				printf '%s\n' "$${SERVICE}:$(RELEASE) is already published."; \
				continue; \
			fi; \
			$(MAKE) "$${SERVICE}-build"; \
			docker tag "$${SERVICE}:local" "$${REPOSITORY}:$(RELEASE)"; \
			docker push "$${REPOSITORY}:$(RELEASE)"; \
		done

ecr-deploy: release-preflight ecr-login ## Pull RELEASE from ECR and deploy it with local Compose.
	@set -eu; \
		REPOSITORIES="$$(terraform -chdir=$(TERRAFORM_DIR) \
			output -json container_repository_urls)"; \
		AIRFLOW_RELEASE="$$(printf '%s' "$${REPOSITORIES}" | jq -er '.airflow'):$(RELEASE)"; \
		INSPECTOR_RELEASE="$$(printf '%s' "$${REPOSITORIES}" \
			| jq -er '."arxiv-inspector"'):$(RELEASE)"; \
		OCR_RELEASE="$$(printf '%s' "$${REPOSITORIES}" \
			| jq -er '."ocr-worker"'):$(RELEASE)"; \
		for IMAGE in "$${AIRFLOW_RELEASE}" "$${INSPECTOR_RELEASE}" "$${OCR_RELEASE}"; do \
			docker pull "$${IMAGE}"; \
		done; \
		docker tag "$${OCR_RELEASE}" ocr-worker:runtime; \
		$(MAKE) services-up \
			AIRFLOW_IMAGE="$${AIRFLOW_RELEASE}" \
			ARXIV_INSPECTOR_IMAGE="$${INSPECTOR_RELEASE}"

# Airflow

airflow-bootstrap-init: preflight ## Initialize the Airflow bootstrap secret exactly once.
	@test -n "$${AWS_PROFILE:-}" || { \
		printf '%s\n' "Set AWS_PROFILE to the Terraform administrator profile."; \
		exit 1; \
	}
	@command -v sha256sum >/dev/null
	@set -eu; \
		BOOTSTRAP_ID="$$(aws --profile "$${AWS_PROFILE}" \
			ssm get-parameter --name "$(AIRFLOW_BOOTSTRAP_PARAMETER)" \
			--query Parameter.Value --output text)"; \
		if aws --profile "$${AWS_PROFILE}" \
			secretsmanager get-secret-value --secret-id "$${BOOTSTRAP_ID}" \
			--query VersionId --output text >/dev/null 2>&1; then \
			printf '%s\n' "Airflow bootstrap secret is already initialized."; \
			exit 0; \
		fi; \
		SECRET_FILE="$$(mktemp)"; \
		trap 'rm -f "$${SECRET_FILE}"' EXIT HUP INT TERM; \
		umask 077; \
		uv run python -c \
			'import base64,json,secrets; print(json.dumps({"version":1,"database_password":secrets.token_urlsafe(32),"fernet_key":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),"jwt_secret":secrets.token_urlsafe(48)}))' \
			> "$${SECRET_FILE}"; \
		CLIENT_TOKEN="$$(sha256sum "$${SECRET_FILE}" | cut -d ' ' -f 1)"; \
		aws --profile "$${AWS_PROFILE}" \
			secretsmanager put-secret-value --secret-id "$${BOOTSTRAP_ID}" \
			--client-request-token "$${CLIENT_TOKEN}" \
			--secret-string "file://$${SECRET_FILE}" >/dev/null; \
		printf '%s\n' "Initialized Airflow bootstrap secret."

airflow-up: preflight ## Start self-hosted Airflow.
	@set -eu; \
		BOOTSTRAP_ID="$$(aws --profile "$(AIRFLOW_AWS_PROFILE)" \
			ssm get-parameter \
			--name "$(AIRFLOW_BOOTSTRAP_PARAMETER)" \
			--query Parameter.Value --output text)"; \
		BOOTSTRAP="$$(aws --profile "$(AIRFLOW_AWS_PROFILE)" \
			secretsmanager get-secret-value \
			--secret-id "$${BOOTSTRAP_ID}" --query SecretString --output text)"; \
		printf '%s' "$${BOOTSTRAP}" | jq -e \
			'.version == 1 and \
			([.database_password, .fernet_key, .jwt_secret] \
			| all(type == "string" and length > 0))' >/dev/null; \
		AIRFLOW_DB_PASSWORD="$$(printf '%s' "$${BOOTSTRAP}" | jq -r '.database_password')" \
		AIRFLOW_FERNET_KEY="$$(printf '%s' "$${BOOTSTRAP}" | jq -r '.fernet_key')" \
		AIRFLOW_JWT_SECRET="$$(printf '%s' "$${BOOTSTRAP}" | jq -r '.jwt_secret')" \
			$(AIRFLOW_COMPOSE) up -d --wait --wait-timeout 300

airflow-down: ## Stop self-hosted Airflow while preserving metadata.
	$(AIRFLOW_COMPOSE_CONFIG) down --remove-orphans

airflow-logs: ## Follow Airflow logs.
	$(AIRFLOW_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-dags: ## List parsed Airflow DAGs.
	$(AIRFLOW_COMPOSE_CONFIG) exec -T airflow-scheduler airflow dags list

# ArXiv Inspector

arxiv-inspector-up: preflight ## Start the read-only ArXiv Inspector.
	$(INSPECTOR_COMPOSE) up -d --wait --wait-timeout 180

arxiv-inspector-down: ## Stop ArXiv Inspector.
	$(INSPECTOR_COMPOSE) down --remove-orphans

arxiv-inspector-logs: ## Follow ArXiv Inspector logs.
	$(INSPECTOR_COMPOSE) logs --follow --tail=200

# Local services

services-up: ## Start all self-hosted services.
	$(MAKE) airflow-up
	$(MAKE) arxiv-inspector-up

services-down: ## Stop all self-hosted services.
	$(MAKE) arxiv-inspector-down
	$(MAKE) airflow-down

services-ps: ## Show self-hosted service state.
	$(AIRFLOW_COMPOSE_CONFIG) ps
	$(INSPECTOR_COMPOSE) ps

# Catalog

catalog-apply: ## Apply Glue/Iceberg YAML contracts with PyIceberg.
	AWS_PROFILE="$(CATALOG_ADMIN_AWS_PROFILE)" \
		uv run --package lakehouse --extra catalog --extra cli \
			python -m lakehouse.catalog.admin apply

catalog-validate: ## Validate Glue/Iceberg state against YAML contracts.
	AWS_PROFILE="$(CATALOG_ADMIN_AWS_PROFILE)" \
		uv run --package lakehouse --extra catalog --extra cli \
			python -m lakehouse.catalog.admin validate

# OCR

ocr-kaggle-runner-publish: preflight ## Publish a new immutable Kaggle OCR runner Dataset version.
	@set -eu; \
		SECRET_ID="$$(aws --profile "$(OCR_AWS_PROFILE)" \
			ssm get-parameter \
			--name "$(PARAMETER_PREFIX)/ocr/providers/kaggle_secret_id" \
			--query Parameter.Value --output text)"; \
		CREDENTIALS="$$(aws --profile "$(OCR_AWS_PROFILE)" \
			secretsmanager get-secret-value \
			--secret-id "$${SECRET_ID}" \
			--query SecretString --output text)"; \
		KAGGLE_USERNAME="$$(printf '%s' "$${CREDENTIALS}" | jq -er \
			'.username | select(type == "string" and length > 0)')" \
		KAGGLE_API_TOKEN="$$(printf '%s' "$${CREDENTIALS}" | jq -er \
			'.api_token | select(type == "string" and length > 0)')" \
			uv run --project ocr --extra kaggle-publish \
				python ocr/runners/kaggle/glm_ocr/publish.py

# Analytics

dbt-deps: ## Install locked dbt packages.
	uv run --package analytics dbt deps --project-dir dbt/analytics

dbt-validate: dbt-deps ## Parse dbt without accessing AWS data.
	DBT_QUERY_RESULTS_URI=s3://validation/query-results \
		DBT_ANALYTICS_URI=s3://validation \
		uv run --package analytics dbt parse \
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
		PARAMETERS="$$(aws --profile "$(DBT_AWS_PROFILE)" ssm get-parameters \
			--names \
				"$(PARAMETER_PREFIX)/athena/dbt_output_uri" \
				"$(PARAMETER_PREFIX)/storage/analytics_uri" \
			--output json)"; \
		QUERY_RESULTS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er \
			--arg name "$(PARAMETER_PREFIX)/athena/dbt_output_uri" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		ANALYTICS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er \
			--arg name "$(PARAMETER_PREFIX)/storage/analytics_uri" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		AWS_PROFILE="$(DBT_AWS_PROFILE)" \
		DBT_QUERY_RESULTS_URI="$${QUERY_RESULTS_URI}" \
		DBT_ANALYTICS_URI="$${ANALYTICS_URI}" \
		uv run --package analytics dbt build \
			--project-dir dbt/analytics --profiles-dir dbt/analytics

# EMR jobs

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
		aws --profile "$(EMR_DEPLOYER_AWS_PROFILE)" s3 sync \
			$(EMR_BUILD_DIR)/ "$${EMR_CODE_URI}/" --only-show-errors; \
		aws --profile "$(EMR_DEPLOYER_AWS_PROFILE)" ssm put-parameter \
			--name "$${EMR_CODE_PARAMETER}" \
			--type String \
			--value "$${EMR_CODE_URI}" \
			--overwrite >/dev/null; \
		printf '%s\n' "Published EMR release $${EMR_CODE_URI}"
