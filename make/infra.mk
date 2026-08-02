AWS_TERRAFORM_STATE_DIR := infra/terraform/aws/bootstrap/state
OCI_TERRAFORM_DIR := infra/terraform/oci/environments/$(LAKEHOUSE_ENVIRONMENT)
TAILSCALE_TERRAFORM_DIR := infra/terraform/tailscale/environments/$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_VALIDATE_DATA_DIR := /tmp/lakehouse-terraform-validate-$(LOCAL_UID)
SERVICES_HOST ?= tgbao-dev-services
SERVICES_HOST_USER ?= ubuntu

.PHONY: terraform-fmt terraform-validate \
	aws-state-init aws-state-plan aws-state-apply \
	aws-init aws-plan aws-apply aws-destroy \
	tailscale-init tailscale-plan tailscale-apply \
	oci-init oci-plan oci-apply oci-destroy \
	workload-pki-init workload-identities-render \
	workload-identities-install

terraform-fmt: ## Check Terraform formatting.
	terraform -chdir=infra/terraform fmt -check -recursive

aws-state-init: ## Initialize the one-time AWS remote-state bootstrap stack.
	terraform -chdir=$(AWS_TERRAFORM_STATE_DIR) init

aws-state-plan: aws-state-init ## Plan the AWS remote-state bootstrap stack.
	terraform -chdir=$(AWS_TERRAFORM_STATE_DIR) plan

aws-state-apply: aws-state-init ## Create the versioned AWS remote-state bucket.
	terraform -chdir=$(AWS_TERRAFORM_STATE_DIR) apply

aws-init: ## Initialize the AWS environment.
	@test -n "$${TF_STATE_BUCKET:-}" || { printf '%s\n' "TF_STATE_BUCKET is required."; exit 1; }
	terraform -chdir=$(AWS_TERRAFORM_DIR) init -backend-config="bucket=$${TF_STATE_BUCKET}"

aws-plan: aws-init ## Plan the AWS data platform.
	terraform -chdir=$(AWS_TERRAFORM_DIR) plan

aws-apply: aws-init ## Apply the reviewed AWS data-platform plan.
	terraform -chdir=$(AWS_TERRAFORM_DIR) apply

aws-destroy: aws-init ## Destroy the AWS data platform.
	terraform -chdir=$(AWS_TERRAFORM_DIR) destroy

tailscale-init: ## Initialize the Tailscale environment.
	@test -n "$${TF_STATE_BUCKET:-}" || { printf '%s\n' "TF_STATE_BUCKET is required."; exit 1; }
	terraform -chdir=$(TAILSCALE_TERRAFORM_DIR) init -backend-config="bucket=$${TF_STATE_BUCKET}"

tailscale-plan: tailscale-init ## Plan private access to the services host.
	terraform -chdir=$(TAILSCALE_TERRAFORM_DIR) plan

tailscale-apply: tailscale-init ## Apply private access to the services host.
	terraform -chdir=$(TAILSCALE_TERRAFORM_DIR) apply

oci-init: ## Initialize the OCI environment.
	@test -n "$${TF_STATE_BUCKET:-}" || { printf '%s\n' "TF_STATE_BUCKET is required."; exit 1; }
	terraform -chdir=$(OCI_TERRAFORM_DIR) init -backend-config="bucket=$${TF_STATE_BUCKET}"

oci-plan: oci-init ## Plan the OCI services host.
	@test -n "$${TF_VAR_tailscale_auth_key:-}" || { printf '%s\n' "TF_VAR_tailscale_auth_key is required."; exit 1; }
	terraform -chdir=$(OCI_TERRAFORM_DIR) plan

oci-apply: oci-init ## Apply the reviewed OCI services-host plan.
	@test -n "$${TF_VAR_tailscale_auth_key:-}" || { printf '%s\n' "TF_VAR_tailscale_auth_key is required."; exit 1; }
	terraform -chdir=$(OCI_TERRAFORM_DIR) apply

oci-destroy: oci-init ## Destroy the OCI services host.
	terraform -chdir=$(OCI_TERRAFORM_DIR) destroy

terraform-validate: ## Initialize without remote state and validate every Terraform root.
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/state \
		terraform -chdir=$(AWS_TERRAFORM_STATE_DIR) init -backend=false -lockfile=readonly
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/state \
		terraform -chdir=$(AWS_TERRAFORM_STATE_DIR) validate
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/aws \
		terraform -chdir=$(AWS_TERRAFORM_DIR) init -backend=false -lockfile=readonly
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/aws \
		terraform -chdir=$(AWS_TERRAFORM_DIR) validate
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/tailscale \
		terraform -chdir=$(TAILSCALE_TERRAFORM_DIR) init -backend=false -lockfile=readonly
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/tailscale \
		terraform -chdir=$(TAILSCALE_TERRAFORM_DIR) validate
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/oci \
		terraform -chdir=$(OCI_TERRAFORM_DIR) init -backend=false -lockfile=readonly
	TF_DATA_DIR=$(TERRAFORM_VALIDATE_DATA_DIR)/oci \
		terraform -chdir=$(OCI_TERRAFORM_DIR) validate

workload-pki-init: ## Create the local workload CA outside the repository.
	infra/runtime/workload-identities init "$(AWS_IDENTITY_DIR)"

workload-identities-render: ## Issue certificates and render configs from applied AWS outputs.
	infra/runtime/workload-identities render "$(AWS_IDENTITY_DIR)" "$(AWS_TERRAFORM_DIR)"

workload-identities-install: ## Install leaf workload identities on the private services host.
	@command -v tailscale >/dev/null
	@for WORKLOAD in airflow arxiv-inspector dbt-transformer ocr-worker services-deployer; do \
		test -r "$(AWS_IDENTITY_DIR)/$${WORKLOAD}/config" || { \
			printf '%s\n' "Missing rendered identity for $${WORKLOAD}."; exit 1; \
		}; \
	done
	@tar -C "$(AWS_IDENTITY_DIR)" -cf - \
		airflow arxiv-inspector dbt-transformer ocr-worker services-deployer \
		| tailscale ssh "$(SERVICES_HOST_USER)@$(SERVICES_HOST)" \
			'set -eu; target="$$HOME/.config/lakehouse/$(LAKEHOUSE_ENVIRONMENT)/aws"; \
			install -d -m 0700 "$$target"; tar -C "$$target" -xf -; \
			chmod 0700 "$$target"/*; chmod 0600 "$$target"/*/private-key.pem "$$target"/*/config; \
			rm -f "$$target"/*/host-config; \
			sed "s#/run/aws/#$$target/services-deployer/#g" \
				"$$target/services-deployer/config" >"$$target/services-deployer/host-config"; \
			chmod 0600 "$$target/services-deployer/host-config"'
