METADATA_POSTGRES_COMPOSE := docker compose --project-name metadata-postgres -f infra/runtime/postgres/compose.yaml
AIRFLOW_COMPOSE := docker compose --project-name airflow -f orchestration/deploy/compose.yaml
INSPECTOR_COMPOSE := docker compose --project-name arxiv-inspector -f apps/arxiv_inspector/deploy/compose.yaml

METADATA_POSTGRES_COMPOSE_CONFIG := METADATA_POSTGRES_PASSWORD=unused AIRFLOW_DATABASE_PASSWORD=unused $(METADATA_POSTGRES_COMPOSE)
AIRFLOW_COMPOSE_CONFIG := AIRFLOW_DATABASE_PASSWORD=unused AIRFLOW_FERNET_KEY=unused AIRFLOW_JWT_SECRET=unused AIRFLOW_ADMIN_PASSWORDS='{"admin":"unused"}' AIRFLOW_REMOTE_LOG_URI=s3://validation/airflow $(AIRFLOW_COMPOSE)

AIRFLOW_AWS_CONFIG := $(AWS_IDENTITY_DIR)/airflow/host-config
METADATA_POSTGRES_AWS_CONFIG := $(AWS_IDENTITY_DIR)/metadata-postgres/host-config
SERVICES_DEPLOYER_AWS_CONFIG := $(AWS_IDENTITY_DIR)/services-deployer/host-config

AIRFLOW_RUNTIME_SECRET := lakehouse/$(LAKEHOUSE_ENVIRONMENT)/airflow/runtime
AIRFLOW_DATABASE_SECRET := lakehouse/$(LAKEHOUSE_ENVIRONMENT)/metadata-postgres/airflow
METADATA_POSTGRES_BOOTSTRAP_SECRET := lakehouse/$(LAKEHOUSE_ENVIRONMENT)/metadata-postgres/bootstrap

.PHONY: metadata-postgres-secrets-init metadata-postgres-up metadata-postgres-down metadata-postgres-logs \
	airflow-secrets-init airflow-up airflow-down airflow-logs airflow-dags \
	arxiv-inspector-up arxiv-inspector-down arxiv-inspector-logs \
	component-image-pull airflow-deploy arxiv-inspector-deploy dbt-task-install ocr-worker-install \
	services-up services-down services-ps

metadata-postgres-secrets-init: ## Initialize shared PostgreSQL secrets exactly once.
	@command -v sha256sum >/dev/null
	@set -eu; \
		for SECRET_ID in "$(METADATA_POSTGRES_BOOTSTRAP_SECRET)" "$(AIRFLOW_DATABASE_SECRET)"; do \
			if CURRENT="$$(aws secretsmanager get-secret-value \
				--secret-id "$${SECRET_ID}" --query SecretString --output text 2>/dev/null)"; then \
				printf '%s' "$${CURRENT}" | jq -e \
					'.version == 1 and (.password | type == "string" and length > 0)' >/dev/null || { \
					printf '%s\n' "Secret $${SECRET_ID} is not schema version 1."; exit 1; \
				}; \
				printf '%s\n' "Secret $${SECRET_ID} is already initialized."; continue; \
			fi; \
			SECRET_FILE="$$(mktemp)"; \
			trap 'rm -f "$${SECRET_FILE}"' EXIT HUP INT TERM; \
			umask 077; \
			uv run python -c \
				'import json,secrets; print(json.dumps({"version":1,"password":secrets.token_urlsafe(32)}))' \
				>"$${SECRET_FILE}"; \
			CLIENT_TOKEN="$$(sha256sum "$${SECRET_FILE}" | cut -d ' ' -f 1)"; \
			aws secretsmanager put-secret-value \
				--secret-id "$${SECRET_ID}" --client-request-token "$${CLIENT_TOKEN}" \
				--secret-string "file://$${SECRET_FILE}" >/dev/null; \
			rm -f "$${SECRET_FILE}"; trap - EXIT HUP INT TERM; \
			printf '%s\n' "Initialized $${SECRET_ID}."; \
		done

airflow-secrets-init: ## Initialize Airflow runtime secrets exactly once.
	@command -v sha256sum >/dev/null
	@set -eu; \
		if CURRENT="$$(aws secretsmanager get-secret-value \
			--secret-id "$(AIRFLOW_RUNTIME_SECRET)" --query SecretString --output text 2>/dev/null)"; then \
			printf '%s' "$${CURRENT}" | jq -e \
				'.version == 1 and ([.fernet_key, .jwt_secret, .admin_password] | all(type == "string" and length > 0))' >/dev/null || { \
				printf '%s\n' "Existing Airflow runtime secret is not schema version 1."; exit 1; \
			}; \
			printf '%s\n' "Airflow runtime secret is already initialized."; exit 0; \
		fi; \
		SECRET_FILE="$$(mktemp)"; \
		trap 'rm -f "$${SECRET_FILE}"' EXIT HUP INT TERM; \
		umask 077; \
		uv run python -c \
			'import base64,json,secrets; print(json.dumps({"version":1,"fernet_key":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),"jwt_secret":secrets.token_urlsafe(48),"admin_password":secrets.token_urlsafe(32)}))' \
			>"$${SECRET_FILE}"; \
		CLIENT_TOKEN="$$(sha256sum "$${SECRET_FILE}" | cut -d ' ' -f 1)"; \
		aws secretsmanager put-secret-value \
			--secret-id "$(AIRFLOW_RUNTIME_SECRET)" --client-request-token "$${CLIENT_TOKEN}" \
			--secret-string "file://$${SECRET_FILE}" >/dev/null; \
		printf '%s\n' "Initialized Airflow runtime secret."

metadata-postgres-up: preflight ## Start shared metadata PostgreSQL and reconcile the Airflow database.
	@test -r "$(METADATA_POSTGRES_AWS_CONFIG)" || { \
		printf '%s\n' "Run 'make workload-identities-render' first."; exit 1; \
	}
	@set -eu; \
		aws_runtime() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(METADATA_POSTGRES_AWS_CONFIG)" AWS_PROFILE=default aws "$$@"; }; \
		POSTGRES_SECRET="$$(aws_runtime secretsmanager get-secret-value \
			--secret-id "$(METADATA_POSTGRES_BOOTSTRAP_SECRET)" --query SecretString --output text)"; \
		AIRFLOW_SECRET="$$(aws_runtime secretsmanager get-secret-value \
			--secret-id "$(AIRFLOW_DATABASE_SECRET)" --query SecretString --output text)"; \
		printf '%s' "$${POSTGRES_SECRET}" | jq -e '.version == 1 and (.password | length > 0)' >/dev/null; \
		printf '%s' "$${AIRFLOW_SECRET}" | jq -e '.version == 1 and (.password | length > 0)' >/dev/null; \
		export METADATA_POSTGRES_PASSWORD="$$(printf '%s' "$${POSTGRES_SECRET}" | jq -r '.password')"; \
		export AIRFLOW_DATABASE_PASSWORD="$$(printf '%s' "$${AIRFLOW_SECRET}" | jq -r '.password')"; \
		$(METADATA_POSTGRES_COMPOSE) up -d --wait --wait-timeout 180 metadata-postgres; \
		$(METADATA_POSTGRES_COMPOSE) run --rm metadata-postgres-bootstrap

metadata-postgres-down: ## Stop shared metadata PostgreSQL while preserving data.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) down --remove-orphans

metadata-postgres-logs: ## Follow metadata PostgreSQL logs.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-up: preflight ## Start self-hosted Airflow against shared metadata PostgreSQL.
	@test -r "$(AIRFLOW_AWS_CONFIG)" || { \
		printf '%s\n' "Install or render the airflow workload identity first."; exit 1; \
	}
	@test -r "$(AWS_IDENTITY_DIR)/airflow/config" || { \
		printf '%s\n' "The airflow container identity config is missing."; exit 1; \
	}
	@set -eu; \
		aws_runtime() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(AIRFLOW_AWS_CONFIG)" AWS_PROFILE=default aws "$$@"; }; \
		RUNTIME_SECRET="$$(aws_runtime secretsmanager get-secret-value \
			--secret-id "$(AIRFLOW_RUNTIME_SECRET)" --query SecretString --output text)"; \
		DATABASE_SECRET="$$(aws_runtime secretsmanager get-secret-value \
			--secret-id "$(AIRFLOW_DATABASE_SECRET)" --query SecretString --output text)"; \
		REMOTE_LOG_URI="$$(aws_runtime ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/airflow/remote_log_uri" \
			--query Parameter.Value --output text)"; \
		printf '%s' "$${RUNTIME_SECRET}" | jq -e \
			'.version == 1 and ([.fernet_key, .jwt_secret, .admin_password] | all(type == "string" and length > 0))' >/dev/null; \
		printf '%s' "$${DATABASE_SECRET}" | jq -e '.version == 1 and (.password | length > 0)' >/dev/null; \
		AIRFLOW_DATABASE_PASSWORD="$$(printf '%s' "$${DATABASE_SECRET}" | jq -r '.password')" \
		AIRFLOW_FERNET_KEY="$$(printf '%s' "$${RUNTIME_SECRET}" | jq -r '.fernet_key')" \
		AIRFLOW_JWT_SECRET="$$(printf '%s' "$${RUNTIME_SECRET}" | jq -r '.jwt_secret')" \
		AIRFLOW_ADMIN_PASSWORDS="$$(printf '%s' "$${RUNTIME_SECRET}" | jq -c '{admin: .admin_password}')" \
		AIRFLOW_REMOTE_LOG_URI="$${REMOTE_LOG_URI}" \
			$(AIRFLOW_COMPOSE) up -d --wait --wait-timeout 300

airflow-down: ## Stop self-hosted Airflow while preserving metadata and bundle cache.
	$(AIRFLOW_COMPOSE_CONFIG) down --remove-orphans

airflow-logs: ## Follow Airflow logs.
	$(AIRFLOW_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-dags: ## List parsed Airflow DAGs and bundle versions.
	$(AIRFLOW_COMPOSE_CONFIG) exec -T airflow-scheduler airflow dags list

arxiv-inspector-up: preflight ## Start the read-only ArXiv Inspector.
	@test -r "$(AWS_IDENTITY_DIR)/arxiv-inspector/config" || { \
		printf '%s\n' "Install or render the arxiv-inspector workload identity first."; exit 1; \
	}
	$(INSPECTOR_COMPOSE) up -d --wait --wait-timeout 180

arxiv-inspector-down: ## Stop ArXiv Inspector.
	$(INSPECTOR_COMPOSE) down --remove-orphans

arxiv-inspector-logs: ## Follow ArXiv Inspector logs.
	$(INSPECTOR_COMPOSE) logs --follow --tail=200

component-image-pull: preflight
	@test -n "$(COMPONENT_IMAGE)" || { printf '%s\n' "COMPONENT_IMAGE is required."; exit 1; }
	@printf '%s' "$(COMPONENT_IMAGE)" | grep -Eq '@sha256:[0-9a-f]{64}$$' || { \
		printf '%s\n' "COMPONENT_IMAGE must be an immutable image reference by digest."; exit 1; \
	}
	@test -r "$(SERVICES_DEPLOYER_AWS_CONFIG)" || { \
		printf '%s\n' "Install or render the services-deployer workload identity first."; exit 1; \
	}
	@set -eu; \
		REGISTRY="$$(printf '%s' "$(COMPONENT_IMAGE)" | cut -d/ -f1)"; \
		trap 'docker logout "$${REGISTRY}" >/dev/null 2>&1 || true' EXIT HUP INT TERM; \
		env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(SERVICES_DEPLOYER_AWS_CONFIG)" AWS_PROFILE=default \
			aws ecr get-login-password | docker login --username AWS --password-stdin "$${REGISTRY}" >/dev/null; \
		docker pull "$(COMPONENT_IMAGE)"

airflow-deploy: ## Pull and deploy one immutable Airflow runtime release.
	@$(MAKE) component-image-pull COMPONENT_IMAGE="$(AIRFLOW_IMAGE)"
	@$(MAKE) airflow-up AIRFLOW_IMAGE="$(AIRFLOW_IMAGE)"

arxiv-inspector-deploy: ## Pull and deploy one immutable ArXiv Inspector release.
	@$(MAKE) component-image-pull COMPONENT_IMAGE="$(ARXIV_INSPECTOR_IMAGE)"
	@$(MAKE) arxiv-inspector-up ARXIV_INSPECTOR_IMAGE="$(ARXIV_INSPECTOR_IMAGE)"

dbt-task-install: ## Install one immutable dbt task image under the stable runtime alias.
	@$(MAKE) component-image-pull COMPONENT_IMAGE="$(DBT_TASK_IMAGE)"
	docker tag "$(DBT_TASK_IMAGE)" dbt-task:runtime

ocr-worker-install: ## Install one immutable OCR worker image under the stable runtime alias.
	@$(MAKE) component-image-pull COMPONENT_IMAGE="$(OCR_WORKER_IMAGE)"
	docker tag "$(OCR_WORKER_IMAGE)" ocr-worker:runtime

services-up: ## Start all self-hosted services in dependency order.
	$(MAKE) metadata-postgres-up
	$(MAKE) airflow-up
	$(MAKE) arxiv-inspector-up

services-down: ## Stop all self-hosted services while preserving state.
	$(MAKE) arxiv-inspector-down
	$(MAKE) airflow-down
	$(MAKE) metadata-postgres-down

services-ps: ## Show self-hosted service state.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) ps
	$(AIRFLOW_COMPOSE_CONFIG) ps
	$(INSPECTOR_COMPOSE) ps
