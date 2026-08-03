DBT_AWS_CONFIG := $(AWS_IDENTITY_DIR)/dbt-transformer/host-config
OCR_AWS_CONFIG := $(AWS_IDENTITY_DIR)/ocr-worker/host-config

.PHONY: catalog-apply catalog-validate ocr-kaggle-runner-publish \
	dbt-deps dbt-validate dbt-build \
	emr-jobs-package

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
	jobs/emr/release/package
