DBT_RUNTIME := env -u VIRTUAL_ENV uv run --project dbt/runtime
AWS_WORKLOAD_ENV := env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
	AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_PROFILE=default

.PHONY: catalog-apply catalog-validate ocr-kaggle-runner-publish \
	ocr-modal-runner-deploy \
	dbt-deps dbt-validate dbt-build \
	emr-jobs-package

catalog-apply: ## Apply Glue/Iceberg YAML contracts with PyIceberg.
	uv run --package lakehouse --extra catalog --extra cli python -m lakehouse.catalog.admin apply

catalog-validate: ## Validate Glue/Iceberg state against YAML contracts.
	uv run --package lakehouse --extra catalog --extra cli python -m lakehouse.catalog.admin validate

ocr-kaggle-runner-publish: preflight ## Publish an immutable Kaggle OCR runner Dataset version.
	@set -eu; \
		SECRET_ID="$$(aws ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/ocr/providers/kaggle_secret_id" \
			--query Parameter.Value --output text)"; \
		CREDENTIALS="$$(aws secretsmanager get-secret-value \
			--secret-id "$${SECRET_ID}" --query SecretString --output text)"; \
		KAGGLE_USERNAME="$$(printf '%s' "$${CREDENTIALS}" | jq -er '.username | select(type == "string" and length > 0)')" \
		KAGGLE_API_TOKEN="$$(printf '%s' "$${CREDENTIALS}" | jq -er '.api_token | select(type == "string" and length > 0)')" \
			uv run --project ocr --extra kaggle-publish python ocr/runners/kaggle/glm_ocr/publish.py

ocr-modal-runner-deploy: preflight ## Deploy the persistent Modal OCR runner.
	@set -eu; \
		SECRET_ID="$$(aws ssm get-parameter \
			--name "$(RUNTIME_PARAMETER_PREFIX)/ocr/providers/modal_secret_id" \
			--query Parameter.Value --output text)"; \
		CREDENTIALS="$$(aws secretsmanager get-secret-value \
			--secret-id "$${SECRET_ID}" --query SecretString --output text)"; \
		MODAL_ENVIRONMENT="$$(uv run --project ocr python -c \
			'from document_ocr.config import load_ocr_config; print(load_ocr_config("arxiv_glm_ocr").runner.modal.environment)')"; \
		MODAL_TOKEN_ID="$$(printf '%s' "$${CREDENTIALS}" | jq -er '.token_id | select(type == "string" and length > 0)')" \
		MODAL_TOKEN_SECRET="$$(printf '%s' "$${CREDENTIALS}" | jq -er '.token_secret | select(type == "string" and length > 0)')" \
			uv run --project ocr --extra worker modal deploy \
				--env "$${MODAL_ENVIRONMENT}" ocr/runners/modal/glm_ocr/app.py

dbt-deps: ## Install locked dbt packages.
	DBT_DOMAIN=all DBT_SCHEMA=analytics_validation \
	DBT_QUERY_RESULTS_URI=s3://validation/query-results DBT_ANALYTICS_URI=s3://validation \
		$(DBT_RUNTIME) dbt deps --project-dir dbt

dbt-validate: dbt-deps ## Parse dbt without accessing AWS data.
	@set -eu; for domain in engineering research; do \
		DBT_DOMAIN="$$domain" DBT_SCHEMA="analytics_$$domain" \
		DBT_QUERY_RESULTS_URI=s3://validation/query-results DBT_ANALYTICS_URI=s3://validation \
			$(DBT_RUNTIME) dbt parse \
				--project-dir dbt --profiles-dir dbt \
				--no-partial-parse --show-all-deprecations; \
	done

dbt-build: ## Build DBT_DOMAIN analytics with its isolated runtime identity.
	@test -n "$(DBT_DOMAIN)" || { printf '%s\n' "Usage: make dbt-build DBT_DOMAIN=<domain>"; exit 2; }
	@case "$(DBT_DOMAIN)" in engineering|research) ;; *) printf '%s\n' "Unknown dbt domain: $(DBT_DOMAIN)"; exit 2;; esac
	@test -d "dbt/dbt_packages/dbt_utils" || { \
		printf '%s\n' "Missing locked dbt packages; run 'make dbt-deps' first."; exit 1; \
	}
	@test -r "$(AWS_IDENTITY_DIR)/dbt-$(DBT_DOMAIN)/host-config" || { printf '%s\n' "Render the dbt-$(DBT_DOMAIN) identity first."; exit 1; }
	@set -eu; \
		QUERY_RESULTS_NAME="$(RUNTIME_PARAMETER_PREFIX)/athena/dbt_$(DBT_DOMAIN)_output_uri"; \
		ANALYTICS_NAME="$(RUNTIME_PARAMETER_PREFIX)/storage/analytics_uri"; \
		PARAMETERS="$$($(AWS_WORKLOAD_ENV) AWS_CONFIG_FILE="$(AWS_IDENTITY_DIR)/dbt-$(DBT_DOMAIN)/host-config" aws ssm get-parameters \
			--names "$${QUERY_RESULTS_NAME}" "$${ANALYTICS_NAME}" --output json)"; \
		QUERY_RESULTS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er --arg name "$${QUERY_RESULTS_NAME}" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		ANALYTICS_URI="$$(printf '%s' "$${PARAMETERS}" | jq -er --arg name "$${ANALYTICS_NAME}" \
			'.Parameters[] | select(.Name == $$name) | .Value')"; \
		$(AWS_WORKLOAD_ENV) AWS_CONFIG_FILE="$(AWS_IDENTITY_DIR)/dbt-$(DBT_DOMAIN)/host-config" \
		DBT_QUERY_RESULTS_URI="$${QUERY_RESULTS_URI}" \
		DBT_ANALYTICS_URI="$${ANALYTICS_URI}" \
		DBT_DOMAIN="$(DBT_DOMAIN)" DBT_SCHEMA="analytics_$(DBT_DOMAIN)" \
		$(DBT_RUNTIME) dbt build --selector "$(DBT_DOMAIN)" \
			--project-dir dbt --profiles-dir dbt

emr-jobs-package: ## Build EMR artifacts in the matching EMR runtime.
	jobs/emr/release/package
