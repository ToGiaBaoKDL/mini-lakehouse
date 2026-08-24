LIGHTDASH_BUILD_CONTEXT := https://github.com/lightdash/lightdash.git#f57276359a0ffcf38c201f95503e671bf80910cd

.PHONY: images-check airflow-build arxiv-inspector-build dbt-image-build \
	lightdash-build ocr-worker-build images-build

images-check: ## Validate every Dockerfile without building image layers.
	docker buildx build --check --file automation/airflow/runtime/Dockerfile .
	docker buildx build --check --file apps/arxiv_inspector/Dockerfile .
	docker buildx build --check --file analytics/dbt-project/Dockerfile .
	docker buildx build --check --file ocr/Dockerfile .
	docker buildx build --check --file lakehouse/emr/Dockerfile .

airflow-build: ## Build the local Airflow image.
	docker build --file automation/airflow/runtime/Dockerfile --tag airflow:local .

arxiv-inspector-build: ## Build the local ArXiv Inspector image.
	docker build --file apps/arxiv_inspector/Dockerfile --tag arxiv-inspector:local .

dbt-image-build: ## Build the shared dbt analytics task image.
	docker build --file analytics/dbt-project/Dockerfile --tag dbt:local --tag dbt:runtime .

lightdash-build: ## Build the pinned upstream Lightdash image for local use.
	docker build --file dockerfile --tag lightdash:local "$(LIGHTDASH_BUILD_CONTEXT)"

ocr-worker-build: ## Build the isolated OCR task image.
	docker build --file ocr/Dockerfile --tag ocr-worker:local --tag ocr-worker:runtime .

images-build: airflow-build arxiv-inspector-build dbt-image-build lightdash-build ocr-worker-build ## Build all local images.
