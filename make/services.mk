METADATA_POSTGRES_COMPOSE := docker compose --project-name metadata-postgres -f infra/runtime/postgres/compose.yaml
AIRFLOW_COMPOSE := docker compose --project-name airflow -f automation/airflow/deploy/compose.yaml
ARXIV_LENS_COMPOSE := docker compose --project-name arxiv-lens -f arxiv-lens/deploy/compose.yaml
LIGHTDASH_COMPOSE := docker compose --project-name lightdash -f analytics/lightdash/deploy/compose.yaml
CLOUDFLARE_COMPOSE := docker compose --project-name cloudflare -f infra/runtime/cloudflare/compose.yaml
T0_TRADING_COMPOSE := docker compose --project-name t0-trading -f t0-trading/deploy/compose.yaml
CLOUDFLARE_CONNECTOR_IMAGE := $(shell sed -n '1p' infra/runtime/cloudflare/image)
NETDATA_COMPOSE := docker compose --project-name netdata -f sysops/netdata/compose.yaml
NETDATA_IMAGE := $(shell sed -n '1p' sysops/netdata/image)

METADATA_POSTGRES_COMPOSE_CONFIG := METADATA_POSTGRES_PASSWORD=unused $(METADATA_POSTGRES_COMPOSE)
AIRFLOW_COMPOSE_CONFIG := AIRFLOW_DATABASE_PASSWORD=unused AIRFLOW_FERNET_KEY=unused AIRFLOW_JWT_SECRET=unused AIRFLOW_ADMIN_PASSWORDS='{"admin":"unused"}' AIRFLOW_REMOTE_LOG_URI=s3://validation/airflow $(AIRFLOW_COMPOSE)
LIGHTDASH_COMPOSE_CONFIG := AWS_REGION=ap-southeast-1 LIGHTDASH_DATABASE_PASSWORD=unused LIGHTDASH_IMAGE=lightdash:local LIGHTDASH_S3_BUCKET=validation LIGHTDASH_SECRET=unused-unused-unused-unused-unused-unused LIGHTDASH_SITE_URL=https://analytics.tgblab.io.vn $(LIGHTDASH_COMPOSE)
CLOUDFLARE_COMPOSE_CONFIG := CLOUDFLARE_IMAGE=$(CLOUDFLARE_CONNECTOR_IMAGE) CLOUDFLARE_TUNNEL_TOKEN_FILE=/dev/null LOCAL_GID=0 $(CLOUDFLARE_COMPOSE)
NETDATA_COMPOSE_CONFIG := NETDATA_CONFIG_SHA256=validation NETDATA_HOSTNAME=validation-host NETDATA_IMAGE=$(NETDATA_IMAGE) NETDATA_POSTGRES_PGPASS='*:*:*:lakehouse_monitor:validation' $(NETDATA_COMPOSE)
T0_TRADING_COMPOSE_CONFIG := T0_LANDING_URI=s3://validation/landing $(T0_TRADING_COMPOSE)

.PHONY: metadata-postgres-secrets-init metadata-postgres-up metadata-postgres-down metadata-postgres-logs \
	metadata-postgres-backup metadata-postgres-restore \
	airflow-secrets-init airflow-up airflow-down airflow-logs airflow-dags \
	arxiv-lens-up arxiv-lens-down arxiv-lens-logs \
	lightdash-secrets-init lightdash-ci-secret-sync \
	t0-trading-ssi-secret-sync t0-trading-certify t0-trading-down t0-trading-logs \
	t0-trading-secrets-init \
	lightdash-up lightdash-down lightdash-logs \
	signoz-secrets-init \
	services-up services-down services-ps

metadata-postgres-secrets-init: ## Initialize the PostgreSQL bootstrap credential exactly once.
	infra/runtime/postgres/initialize-secrets bootstrap

airflow-secrets-init: ## Initialize Airflow database and runtime secrets exactly once.
	infra/runtime/postgres/initialize-secrets airflow
	automation/airflow/deploy/initialize-secrets

lightdash-secrets-init: ## Initialize Lightdash database and runtime secrets exactly once.
	infra/runtime/postgres/initialize-secrets lightdash
	analytics/lightdash/deploy/initialize-secrets

signoz-secrets-init: ## Initialize the pg_monitor credential for the SigNoz collection agent.
	infra/runtime/postgres/initialize-secrets pg_monitor

lightdash-ci-secret-sync: ## Store the local Lightdash CI token payload in Secrets Manager.
	analytics/lightdash/deploy/sync-ci-secret ".secrets/$(LAKEHOUSE_ENVIRONMENT)/lightdash/ci.json"

t0-trading-ssi-secret-sync: ## Store the local SSI FastConnect v3 credential in Secrets Manager.
	t0-trading/deploy/sync-ssi-secret ".secrets/$(LAKEHOUSE_ENVIRONMENT)/t0-trading/ssi.json"

t0-trading-secrets-init: ## Initialize the T0 trading PostgreSQL credential exactly once.
	infra/runtime/postgres/initialize-secrets t0_trading

t0-trading-certify: ## Capture sanitized SSI Data REST and Stream DATA evidence.
	uv run t0-trading certify

t0-trading-down: ## Stop the bounded stream capture canary.
	$(T0_TRADING_COMPOSE_CONFIG) down --remove-orphans

t0-trading-logs: ## Follow bounded stream capture logs.
	$(T0_TRADING_COMPOSE_CONFIG) logs --follow --tail=200

metadata-postgres-up: preflight ## Start shared metadata PostgreSQL without changing application databases.
	infra/runtime/postgres/deploy

metadata-postgres-down: ## Stop shared metadata PostgreSQL while preserving data.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) down --remove-orphans

metadata-postgres-logs: ## Follow metadata PostgreSQL logs.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

metadata-postgres-backup: ## Upload encrypted metadata PostgreSQL daily dumps to the backup bucket.
	infra/runtime/postgres/backup $(ARGS)

metadata-postgres-restore: ## Drop and restore one application database from a slot backup (usage: make metadata-postgres-restore ARGS='lightdash 2026-08-15 pm').
	infra/runtime/postgres/restore $(ARGS)

airflow-up: preflight ## Start self-hosted Airflow against shared metadata PostgreSQL.
	automation/airflow/deploy/reconcile airflow:local

airflow-down: ## Stop self-hosted Airflow while preserving metadata and bundle cache.
	$(AIRFLOW_COMPOSE_CONFIG) down --remove-orphans

airflow-logs: ## Follow Airflow logs.
	$(AIRFLOW_COMPOSE_CONFIG) logs --follow --tail=200 $(ARGS)

airflow-dags: ## List parsed Airflow DAGs and bundle versions.
	$(AIRFLOW_COMPOSE_CONFIG) exec -T airflow-scheduler airflow dags list

arxiv-lens-up: preflight ## Start the read-only ArXiv Lens.
	arxiv-lens/deploy/reconcile arxiv-lens:local

arxiv-lens-down: ## Stop ArXiv Lens.
	$(ARXIV_LENS_COMPOSE) down --remove-orphans

arxiv-lens-logs: ## Follow ArXiv Lens logs.
	$(ARXIV_LENS_COMPOSE) logs --follow --tail=200

lightdash-up: preflight ## Start the locally built Lightdash image.
	analytics/lightdash/deploy/reconcile lightdash:local

lightdash-down: ## Stop Lightdash while preserving its metadata database.
	$(LIGHTDASH_COMPOSE_CONFIG) down --remove-orphans

lightdash-logs: ## Follow Lightdash logs.
	$(LIGHTDASH_COMPOSE_CONFIG) logs --follow --tail=200

services-up: ## Start all self-hosted services in dependency order.
	$(MAKE) airflow-up
	$(MAKE) lightdash-up
	$(MAKE) arxiv-lens-up

services-down: ## Stop all self-hosted services while preserving state.
	$(MAKE) arxiv-lens-down
	$(MAKE) lightdash-down
	$(MAKE) airflow-down
	$(MAKE) metadata-postgres-down

services-ps: ## Show self-hosted service state.
	$(METADATA_POSTGRES_COMPOSE_CONFIG) ps
	$(AIRFLOW_COMPOSE_CONFIG) ps
	$(LIGHTDASH_COMPOSE_CONFIG) ps
	$(ARXIV_LENS_COMPOSE) ps
	$(T0_TRADING_COMPOSE_CONFIG) ps
