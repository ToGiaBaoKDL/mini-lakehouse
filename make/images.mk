IMAGE_PUBLISHER_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-image-publisher

.PHONY: images-check airflow-build arxiv-inspector-build dbt-task-build ocr-worker-build images-build \
	release-preflight images-login images-publish

images-check: ## Validate every Dockerfile without building image layers.
	docker buildx build --check --file orchestration/Dockerfile .
	docker buildx build --check --file apps/arxiv_inspector/Dockerfile .
	docker buildx build --check --file dbt/analytics/Dockerfile .
	docker buildx build --check --file ocr/Dockerfile .
	docker buildx build --check --file jobs/emr/Dockerfile .

airflow-build: ## Build the local Airflow image.
	docker build --file orchestration/Dockerfile --tag airflow:local .

arxiv-inspector-build: ## Build the local ArXiv Inspector image.
	docker build --file apps/arxiv_inspector/Dockerfile --tag arxiv-inspector:local .

dbt-task-build: ## Build the isolated dbt analytics task image.
	docker build --file dbt/analytics/Dockerfile --tag dbt-task:local --tag dbt-task:runtime .

ocr-worker-build: ## Build the isolated OCR task image.
	docker build --file ocr/Dockerfile --tag ocr-worker:local --tag ocr-worker:runtime .

images-build: airflow-build arxiv-inspector-build dbt-task-build ocr-worker-build ## Build all local images.

release-preflight: preflight ## Require a committed release and initialized AWS Terraform state.
	@test -z "$$(git status --porcelain)" || { \
		printf '%s\n' "Commit the worktree before publishing or deploying a release."; exit 1; \
	}
	@command -v terraform >/dev/null
	@terraform -chdir=$(AWS_TERRAFORM_DIR) output -json container_repository_urls >/dev/null

images-login: preflight ## Authenticate Docker to the environment ECR registry.
	@command -v terraform >/dev/null
	@set -eu; \
		REPOSITORIES="$$(terraform -chdir=$(AWS_TERRAFORM_DIR) output -json container_repository_urls)"; \
		REGISTRY="$$(printf '%s' "$${REPOSITORIES}" | jq -er '.airflow' | cut -d/ -f1)"; \
		aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" ecr get-login-password \
			| docker login --username AWS --password-stdin "$${REGISTRY}" >/dev/null; \
		printf '%s\n' "Authenticated Docker to $${REGISTRY}."

images-publish: release-preflight images-login ## Publish immutable multi-architecture images for RELEASE.
	@set -eu; \
		REPOSITORIES="$$(terraform -chdir=$(AWS_TERRAFORM_DIR) output -json container_repository_urls)"; \
		printf '%s' "$${REPOSITORIES}" | jq -er 'keys[]' | while read -r SERVICE; do \
			REPOSITORY="$$(printf '%s' "$${REPOSITORIES}" | jq -er --arg service "$${SERVICE}" '.[$$service]')"; \
			if aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" ecr describe-images \
				--repository-name "$${REPOSITORY##*/}" --image-ids imageTag="$(RELEASE)" >/dev/null 2>&1; then \
				printf '%s\n' "$${SERVICE}:$(RELEASE) is already published."; continue; \
			fi; \
			case "$${SERVICE}" in \
				airflow) DOCKERFILE=orchestration/Dockerfile ;; \
				arxiv-inspector) DOCKERFILE=apps/arxiv_inspector/Dockerfile ;; \
				dbt-task) DOCKERFILE=dbt/analytics/Dockerfile ;; \
				ocr-worker) DOCKERFILE=ocr/Dockerfile ;; \
				*) printf '%s\n' "Unknown image $${SERVICE}."; exit 1 ;; \
			esac; \
			docker buildx build --platform linux/amd64,linux/arm64 \
				--file "$${DOCKERFILE}" --tag "$${REPOSITORY}:$(RELEASE)" --push .; \
		done
