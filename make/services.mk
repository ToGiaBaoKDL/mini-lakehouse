METADATA_POSTGRES_COMPOSE := docker compose --project-name metadata-postgres -f infra/runtime/postgres/compose.yaml
AIRFLOW_COMPOSE := docker compose --project-name airflow -f orchestration/deploy/compose.yaml
INSPECTOR_COMPOSE := docker compose --project-name arxiv-inspector -f apps/arxiv_inspector/deploy/compose.yaml

METADATA_POSTGRES_COMPOSE_CONFIG := METADATA_POSTGRES_PASSWORD=unused AIRFLOW_DATABASE_PASSWORD=unused $(METADATA_POSTGRES_COMPOSE)
AIRFLOW_COMPOSE_CONFIG := AIRFLOW_DATABASE_PASSWORD=unused AIRFLOW_FERNET_KEY=unused AIRFLOW_JWT_SECRET=unused AIRFLOW_ADMIN_PASSWORDS='{"admin":"unused"}' AIRFLOW_REMOTE_LOG_URI=s3://validation/airflow $(AIRFLOW_COMPOSE)

.PHONY: metadata-postgres-secrets-init metadata-postgres-up metadata-postgres-down metadata-postgres-logs \
	airflow-secrets-init airflow-up airflow-down airflow-logs airflow-dags \
	arxiv-inspector-up arxiv-inspector-down arxiv-inspector-logs \
	services-up services-down services-ps

metadata-postgres-secrets-init: ## Initialize shared PostgreSQL secrets exactly once.
	infra/runtime/postgres/initialize-secrets

airflow-secrets-init: ## Initialize Airflow runtime secrets exactly once.
	orchestration/deploy/initialize-secrets

metadata-postgres-up: preflight ## Start shared metadata PostgreSQL and reconcile the Airflow database.
	infra/runtime/postgres/deploy

metadata-postgres-down: ## Stop shared metadata PostgreSQL while preserving data.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) down --remove-orphans

metadata-postgres-logs: ## Follow metadata PostgreSQL logs.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-up: preflight ## Start self-hosted Airflow against shared metadata PostgreSQL.
	orchestration/deploy/reconcile airflow:local

airflow-down: ## Stop self-hosted Airflow while preserving metadata and bundle cache.
	$(AIRFLOW_COMPOSE_CONFIG) down --remove-orphans

airflow-logs: ## Follow Airflow logs.
	$(AIRFLOW_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-dags: ## List parsed Airflow DAGs and bundle versions.
	$(AIRFLOW_COMPOSE_CONFIG) exec -T airflow-scheduler airflow dags list

arxiv-inspector-up: preflight ## Start the read-only ArXiv Inspector.
	apps/arxiv_inspector/deploy/reconcile arxiv-inspector:local

arxiv-inspector-down: ## Stop ArXiv Inspector.
	$(INSPECTOR_COMPOSE) down --remove-orphans

arxiv-inspector-logs: ## Follow ArXiv Inspector logs.
	$(INSPECTOR_COMPOSE) logs --follow --tail=200

services-up: ## Start all self-hosted services in dependency order.
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
