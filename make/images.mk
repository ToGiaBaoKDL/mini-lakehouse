IMAGE_PUBLISHER_AWS_PROFILE ?= lakehouse-$(LAKEHOUSE_ENVIRONMENT)-image-publisher

.PHONY: images-check airflow-build arxiv-inspector-build dbt-task-build ocr-worker-build images-build \
	release-preflight image-publish-preflight image-login image-publish \
	airflow-publish arxiv-inspector-publish dbt-task-publish ocr-worker-publish

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

release-preflight: preflight ## Require an immutable committed release.
	@test -z "$$(git status --porcelain)" || { \
		printf '%s\n' "Commit the worktree before publishing or deploying a release."; exit 1; \
	}

image-publish-preflight: release-preflight ## Validate one component image release.
	@command -v terraform >/dev/null
	@case "$(COMPONENT)" in \
		airflow|arxiv-inspector|dbt-task|ocr-worker) ;; \
		*) printf '%s\n' "COMPONENT must be airflow, arxiv-inspector, dbt-task, or ocr-worker."; exit 1 ;; \
	esac
	@$(AWS_TERRAFORM) output -json container_repository_urls >/dev/null

image-login: preflight ## Authenticate Docker to the environment ECR registry.
	@command -v terraform >/dev/null
	@set -eu; \
		REPOSITORIES="$$($(AWS_TERRAFORM) output -json container_repository_urls)"; \
		REGISTRY="$$(printf '%s' "$${REPOSITORIES}" | jq -er '.airflow' | cut -d/ -f1)"; \
		aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" ecr get-login-password \
			| docker login --username AWS --password-stdin "$${REGISTRY}" >/dev/null; \
		printf '%s\n' "Authenticated Docker to $${REGISTRY}."

image-publish: image-publish-preflight image-login ## Publish one immutable multi-architecture component image.
	@set -eu; \
		REPOSITORIES="$$($(AWS_TERRAFORM) output -json container_repository_urls)"; \
		REPOSITORY="$$(printf '%s' "$${REPOSITORIES}" | jq -er --arg component "$(COMPONENT)" '.[$$component]')"; \
		if aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" ecr describe-images \
			--repository-name "$${REPOSITORY##*/}" --image-ids imageTag="$(RELEASE)" >/dev/null 2>&1; then \
			printf '%s\n' "$(COMPONENT):$(RELEASE) is already published."; exit 0; \
		fi; \
		case "$(COMPONENT)" in \
			airflow) DOCKERFILE=orchestration/runtime/Dockerfile ;; \
			arxiv-inspector) DOCKERFILE=apps/arxiv_inspector/Dockerfile ;; \
			dbt-task) DOCKERFILE=dbt/analytics/Dockerfile ;; \
			ocr-worker) DOCKERFILE=ocr/Dockerfile ;; \
		esac; \
		docker buildx build --platform linux/amd64,linux/arm64 \
			--file "$${DOCKERFILE}" --tag "$${REPOSITORY}:$(RELEASE)" --push .; \
		DIGEST="$$(aws --profile "$(IMAGE_PUBLISHER_AWS_PROFILE)" ecr describe-images \
			--repository-name "$${REPOSITORY##*/}" --image-ids imageTag="$(RELEASE)" \
			--query 'imageDetails[0].imageDigest' --output text)"; \
		printf '%s@%s\n' "$${REPOSITORY}" "$${DIGEST}"

airflow-publish: ## Publish only the Airflow runtime image.
	@$(MAKE) image-publish COMPONENT=airflow

arxiv-inspector-publish: ## Publish only the ArXiv Inspector image.
	@$(MAKE) image-publish COMPONENT=arxiv-inspector

dbt-task-publish: ## Publish only the dbt task image.
	@$(MAKE) image-publish COMPONENT=dbt-task

ocr-worker-publish: ## Publish only the OCR worker image.
	@$(MAKE) image-publish COMPONENT=ocr-worker
