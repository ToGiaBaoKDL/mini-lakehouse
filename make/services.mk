AIRFLOW_COMPOSE := docker compose --project-name airflow -f compose.airflow.yaml
AIRFLOW_COMPOSE_CONFIG := AIRFLOW_DB_PASSWORD=unused AIRFLOW_FERNET_KEY=unused AIRFLOW_JWT_SECRET=unused AIRFLOW_REMOTE_LOG_URI=s3://validation/airflow $(AIRFLOW_COMPOSE)
INSPECTOR_COMPOSE := docker compose --project-name arxiv-inspector -f compose.arxiv-inspector.yaml
SERVICES_DEPLOYER_AWS_CONFIG := $(AWS_IDENTITY_DIR)/services-deployer/host-config

.PHONY: release-deploy airflow-bootstrap-init airflow-up airflow-down airflow-logs airflow-dags \
	arxiv-inspector-up arxiv-inspector-down arxiv-inspector-logs \
	services-up services-down services-ps

release-deploy: release-preflight ## Deploy RELEASE on the Tailscale-enrolled services host.
	@command -v tailscale >/dev/null
	infra/runtime/deploy-release \
		"$(LAKEHOUSE_ENVIRONMENT)" "$(AWS_IDENTITY_DIR)" "$(RELEASE)" "$$(tailscale ip -4)"

airflow-bootstrap-init: preflight ## Initialize the Airflow bootstrap secret exactly once.
	@test -n "$${AWS_PROFILE:-}" || { \
		printf '%s\n' "Set AWS_PROFILE to the Terraform administrator profile."; exit 1; \
	}
	@command -v sha256sum >/dev/null
	@set -eu; \
		BOOTSTRAP_ID="$$(aws --profile "$${AWS_PROFILE}" ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/secrets/airflow_bootstrap_id" \
			--query Parameter.Value --output text)"; \
		if aws --profile "$${AWS_PROFILE}" secretsmanager get-secret-value \
			--secret-id "$${BOOTSTRAP_ID}" --query VersionId --output text >/dev/null 2>&1; then \
			printf '%s\n' "Airflow bootstrap secret is already initialized."; exit 0; \
		fi; \
		SECRET_FILE="$$(mktemp)"; \
		trap 'rm -f "$${SECRET_FILE}"' EXIT HUP INT TERM; \
		umask 077; \
		uv run python -c \
			'import base64,json,secrets; print(json.dumps({"version":1,"database_password":secrets.token_urlsafe(32),"fernet_key":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),"jwt_secret":secrets.token_urlsafe(48)}))' \
			> "$${SECRET_FILE}"; \
		CLIENT_TOKEN="$$(sha256sum "$${SECRET_FILE}" | cut -d ' ' -f 1)"; \
		aws --profile "$${AWS_PROFILE}" secretsmanager put-secret-value \
			--secret-id "$${BOOTSTRAP_ID}" --client-request-token "$${CLIENT_TOKEN}" \
			--secret-string "file://$${SECRET_FILE}" >/dev/null; \
		printf '%s\n' "Initialized Airflow bootstrap secret."

airflow-up: preflight ## Start self-hosted Airflow.
	@test -r "$(SERVICES_DEPLOYER_AWS_CONFIG)" || { \
		printf '%s\n' "Run 'make workload-identities-render' first."; exit 1; \
	}
	@set -eu; \
		BOOTSTRAP_ID="$$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(SERVICES_DEPLOYER_AWS_CONFIG)" AWS_PROFILE=default aws ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/secrets/airflow_bootstrap_id" \
			--query Parameter.Value --output text)"; \
		REMOTE_LOG_URI="$$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(SERVICES_DEPLOYER_AWS_CONFIG)" AWS_PROFILE=default aws ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/airflow/remote_log_uri" \
			--query Parameter.Value --output text)"; \
		BOOTSTRAP="$$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(SERVICES_DEPLOYER_AWS_CONFIG)" AWS_PROFILE=default aws \
			secretsmanager get-secret-value --secret-id "$${BOOTSTRAP_ID}" \
			--query SecretString --output text)"; \
		printf '%s' "$${BOOTSTRAP}" | jq -e \
			'.version == 1 and ([.database_password, .fernet_key, .jwt_secret] | all(type == "string" and length > 0))' >/dev/null; \
		AIRFLOW_DB_PASSWORD="$$(printf '%s' "$${BOOTSTRAP}" | jq -r '.database_password')" \
		AIRFLOW_FERNET_KEY="$$(printf '%s' "$${BOOTSTRAP}" | jq -r '.fernet_key')" \
		AIRFLOW_JWT_SECRET="$$(printf '%s' "$${BOOTSTRAP}" | jq -r '.jwt_secret')" \
		AIRFLOW_REMOTE_LOG_URI="$${REMOTE_LOG_URI}" \
			$(AIRFLOW_COMPOSE) up -d --wait --wait-timeout 300

airflow-down: ## Stop self-hosted Airflow while preserving metadata.
	$(AIRFLOW_COMPOSE_CONFIG) down --remove-orphans

airflow-logs: ## Follow Airflow logs.
	$(AIRFLOW_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-dags: ## List parsed Airflow DAGs.
	$(AIRFLOW_COMPOSE_CONFIG) exec -T airflow-scheduler airflow dags list

arxiv-inspector-up: preflight ## Start the read-only ArXiv Inspector.
	$(INSPECTOR_COMPOSE) up -d --wait --wait-timeout 180

arxiv-inspector-down: ## Stop ArXiv Inspector.
	$(INSPECTOR_COMPOSE) down --remove-orphans

arxiv-inspector-logs: ## Follow ArXiv Inspector logs.
	$(INSPECTOR_COMPOSE) logs --follow --tail=200

services-up: ## Start all self-hosted services.
	$(MAKE) airflow-up
	$(MAKE) arxiv-inspector-up

services-down: ## Stop all self-hosted services.
	$(MAKE) arxiv-inspector-down
	$(MAKE) airflow-down

services-ps: ## Show self-hosted service state.
	$(AIRFLOW_COMPOSE_CONFIG) ps
	$(INSPECTOR_COMPOSE) ps
