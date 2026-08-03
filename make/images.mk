.PHONY: images-check airflow-build arxiv-inspector-build dbt-task-build ocr-worker-build images-build

images-check: ## Validate every Dockerfile without building image layers.
	docker buildx build --check --file orchestration/runtime/Dockerfile .
	docker buildx build --check --file apps/arxiv_inspector/Dockerfile .
	docker buildx build --check --file dbt/analytics/Dockerfile .
	docker buildx build --check --file ocr/Dockerfile .
	docker buildx build --check --file jobs/emr/Dockerfile .

airflow-build: ## Build the local Airflow image.
	docker build --file orchestration/runtime/Dockerfile --tag airflow:local .

arxiv-inspector-build: ## Build the local ArXiv Inspector image.
	docker build --file apps/arxiv_inspector/Dockerfile --tag arxiv-inspector:local .

dbt-task-build: ## Build the isolated dbt analytics task image.
	docker build --file dbt/analytics/Dockerfile --tag dbt-task:local --tag dbt-task:runtime .

ocr-worker-build: ## Build the isolated OCR task image.
	docker build --file ocr/Dockerfile --tag ocr-worker:local --tag ocr-worker:runtime .

images-build: airflow-build arxiv-inspector-build dbt-task-build ocr-worker-build ## Build all local images.
