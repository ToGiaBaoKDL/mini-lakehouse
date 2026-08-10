.PHONY: images-check airflow-build arxiv-inspector-build dbt-engineering-build \
	dbt-research-build ocr-worker-build images-build

images-check: ## Validate every Dockerfile without building image layers.
	docker buildx build --check --file orchestration/runtime/Dockerfile .
	docker buildx build --check --file apps/arxiv_inspector/Dockerfile .
	docker buildx build --check --build-arg DBT_PROJECT=engineering --file dbt/Dockerfile .
	docker buildx build --check --build-arg DBT_PROJECT=research --file dbt/Dockerfile .
	docker buildx build --check --file ocr/Dockerfile .
	docker buildx build --check --file jobs/emr/Dockerfile .

airflow-build: ## Build the local Airflow image.
	docker build --file orchestration/runtime/Dockerfile --tag airflow:local .

arxiv-inspector-build: ## Build the local ArXiv Inspector image.
	docker build --file apps/arxiv_inspector/Dockerfile --tag arxiv-inspector:local .

dbt-engineering-build: ## Build the isolated Engineering analytics task image.
	docker build --build-arg DBT_PROJECT=engineering --file dbt/Dockerfile \
		--tag dbt-engineering:local --tag dbt-engineering:runtime .

dbt-research-build: ## Build the isolated Research analytics task image.
	docker build --build-arg DBT_PROJECT=research --file dbt/Dockerfile \
		--tag dbt-research:local --tag dbt-research:runtime .

ocr-worker-build: ## Build the isolated OCR task image.
	docker build --file ocr/Dockerfile --tag ocr-worker:local --tag ocr-worker:runtime .

images-build: airflow-build arxiv-inspector-build dbt-engineering-build dbt-research-build ocr-worker-build ## Build all local images.
