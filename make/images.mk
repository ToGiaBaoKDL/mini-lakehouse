LIGHTDASH_BUILD_CONTEXT := https://github.com/lightdash/lightdash.git#f57276359a0ffcf38c201f95503e671bf80910cd

.PHONY: images-check airflow-build arxiv-lens-build dbt-image-build \
	lakehouse-ingest-build t0-trading-build lightdash-build images-build

images-check: ## Validate every Dockerfile without building image layers.
	docker buildx build --check --file automation/airflow/runtime/Dockerfile .
	docker buildx build --check --file arxiv-lens/Dockerfile .
	docker buildx build --check --file analytics/dbt-project/Dockerfile .
	docker buildx build --check --file lakehouse/emr/Dockerfile .
	docker buildx build --check --file lakehouse/ingest/Dockerfile .
	docker buildx build --check --file t0-trading/Dockerfile .

airflow-build: ## Build the local Airflow image.
	docker build --file automation/airflow/runtime/Dockerfile --tag airflow:local .

arxiv-lens-build: ## Build the local ArXiv Lens image.
	docker build --file arxiv-lens/Dockerfile --tag arxiv-lens:local .

dbt-image-build: ## Build the shared dbt analytics task image.
	docker build --file analytics/dbt-project/Dockerfile --tag dbt:local --tag dbt:runtime .

lakehouse-ingest-build: ## Build the bounded source-capture task image.
	docker build --file lakehouse/ingest/Dockerfile --tag lakehouse-ingest:runtime .

t0-trading-build: ## Build the bounded T0 market-data task image.
	docker build --file t0-trading/Dockerfile --tag t0-trading:runtime .

lightdash-build: ## Build the pinned upstream Lightdash image for local use.
	docker build --file dockerfile --tag lightdash:local "$(LIGHTDASH_BUILD_CONTEXT)"

images-build: airflow-build arxiv-lens-build dbt-image-build lakehouse-ingest-build t0-trading-build lightdash-build ## Build all local images.
