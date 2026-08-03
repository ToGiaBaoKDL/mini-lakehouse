EMR_BUILD_DIR := dist/emr
DBT_AWS_CONFIG := $(AWS_IDENTITY_DIR)/dbt-transformer/host-config
OCR_AWS_CONFIG := $(AWS_IDENTITY_DIR)/ocr-worker/host-config

.PHONY: catalog-apply catalog-validate ocr-kaggle-runner-publish \
	dbt-deps dbt-validate dbt-build \
	emr-jobs-package emr-jobs-publish-preflight emr-jobs-publish

catalog-apply: ## Apply Glue/Iceberg YAML contracts with PyIceberg.
	uv run --package lakehouse --extra catalog --extra cli python -m lakehouse.catalog.admin apply

catalog-validate: ## Validate Glue/Iceberg state against YAML contracts.
	uv run --package lakehouse --extra catalog --extra cli python -m lakehouse.catalog.admin validate

ocr-kaggle-runner-publish: preflight ## Publish an immutable Kaggle OCR runner Dataset version.
	@test -r "$(OCR_AWS_CONFIG)" || { printf '%s\n' "Render the ocr-worker identity first."; exit 1; }
	@set -eu; \
		SECRET_ID="$$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(OCR_AWS_CONFIG)" AWS_PROFILE=default aws ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/ocr/providers/kaggle_secret_id" \
			--query Parameter.Value --output text)"; \
		CREDENTIALS="$$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(OCR_AWS_CONFIG)" AWS_PROFILE=default aws secretsmanager get-secret-value \
			--secret-id "$${SECRET_ID}" --query SecretString --output text)"; \
		KAGGLE_USERNAME="$$(printf '%s' "$${CREDENTIALS}" | jq -er '.username | select(type == "string" and length > 0)')" \
		KAGGLE_API_TOKEN="$$(printf '%s' "$${CREDENTIALS}" | jq -er '.api_token | select(type == "string" and length > 0)')" \
			uv run --project ocr --extra kaggle-publish python ocr/runners/kaggle/glm_ocr/publish.py

dbt-deps: ## Install locked dbt packages.
	uv run --project dbt/analytics dbt deps --project-dir dbt/analytics

dbt-validate: dbt-deps ## Parse dbt without accessing AWS data.
	DBT_QUERY_RESULTS_URI=s3://validation/query-results DBT_ANALYTICS_URI=s3://validation \
		uv run --project dbt/analytics dbt parse \
			--project-dir dbt/analytics --profiles-dir dbt/analytics \
			--no-partial-parse --show-all-deprecations

dbt-build: ## Build analytics with runtime references loaded from SSM.
	@test -d dbt/analytics/dbt_packages/dbt_utils || { \
		printf '%s\n' "Missing locked dbt packages; run 'make dbt-deps' first."; exit 1; \
	}
	@test -r "$(DBT_AWS_CONFIG)" || { printf '%s\n' "Render the dbt-transformer identity first."; exit 1; }
	@set -eu; \
		QUERY_RESULTS_NAME="$(RUNTIME_PARAMETER_PREFIX)/athena/dbt_output_uri"; \
		ANALYTICS_NAME="$(RUNTIME_PARAMETER_PREFIX)/storage/analytics_uri"; \
		PARAMETERS="$$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
			AWS_CONFIG_FILE="$(DBT_AWS_CONFIG)" AWS_PROFILE=default aws ssm get-parameters \
			--names "$${QUERY_RESULTS_NAME}" "$${ANALYTICS_NAME}" --output json)"; \
		QUERY_RESULTS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er --arg name "$${QUERY_RESULTS_NAME}" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		ANALYTICS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er --arg name "$${ANALYTICS_NAME}" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		AWS_CONFIG_FILE="$(DBT_AWS_CONFIG)" AWS_PROFILE=default \
		DBT_QUERY_RESULTS_URI="$${QUERY_RESULTS_URI}" \
		DBT_ANALYTICS_URI="$${ANALYTICS_URI}" uv run --project dbt/analytics dbt build \
			--project-dir dbt/analytics --profiles-dir dbt/analytics

emr-jobs-package: ## Build EMR artifacts in the matching EMR runtime.
	rm -rf $(EMR_BUILD_DIR)
	docker build --platform linux/amd64 --file jobs/emr/Dockerfile --target artifacts \
		--output type=local,dest=$(EMR_BUILD_DIR) .

emr-jobs-publish-preflight: ## Require a committed release and deployment tools.
	@test -z "$$(git status --porcelain)" || { \
		printf '%s\n' "Commit the worktree before publishing an EMR release."; exit 1; \
	}
	@command -v aws >/dev/null
	@if test -z "$${EMR_ARTIFACTS_URI:-}" || test -z "$${EMR_CODE_PARAMETER_NAME:-}"; then \
		command -v terraform >/dev/null || { \
			printf '%s\n' "Set EMR_ARTIFACTS_URI and EMR_CODE_PARAMETER_NAME when Terraform state is unavailable."; exit 1; \
		}; \
	fi

emr-jobs-publish: emr-jobs-publish-preflight emr-jobs-package ## Publish one immutable EMR release.
	@set -eu; \
		ARTIFACTS_URI="$${EMR_ARTIFACTS_URI:-}"; \
		CODE_PARAMETER="$${EMR_CODE_PARAMETER_NAME:-}"; \
		if test -z "$${ARTIFACTS_URI}" && test -z "$${CODE_PARAMETER}"; then \
			ARTIFACTS_URI="$$($(AWS_TERRAFORM) output -raw emr_artifacts_uri)"; \
			CODE_PARAMETER="$$($(AWS_TERRAFORM) output -raw emr_code_parameter_name)"; \
		elif test -z "$${ARTIFACTS_URI}" || test -z "$${CODE_PARAMETER}"; then \
			printf '%s\n' "EMR_ARTIFACTS_URI and EMR_CODE_PARAMETER_NAME must be set together."; exit 1; \
		fi; \
		EMR_CODE_URI="$${ARTIFACTS_URI%/}/$(RELEASE)"; \
		aws s3 sync \
			$(EMR_BUILD_DIR)/ "$${EMR_CODE_URI}/" --only-show-errors; \
		aws ssm put-parameter \
			--name "$${CODE_PARAMETER}" --type String --value "$${EMR_CODE_URI}" --overwrite >/dev/null; \
		printf '%s\n' "Published EMR release $${EMR_CODE_URI}"
