AWS_TERRAFORM_STATE_DIR := infra/terraform/aws/bootstrap/state
OCI_TERRAFORM_DIR := infra/terraform/oci/environments/$(LAKEHOUSE_ENVIRONMENT)
TAILSCALE_TERRAFORM_DIR := infra/terraform/tailscale/environments/$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_VALIDATE_DATA_DIR := /tmp/lakehouse-terraform-validate-$(LOCAL_UID)

.PHONY: terraform-fmt terraform-validate \
	aws-state-init aws-state-plan aws-state-apply \
	aws-init aws-plan aws-apply aws-destroy \
	tailscale-init tailscale-plan tailscale-apply \
	oci-init oci-plan oci-apply oci-destroy \
	workload-pki-init workload-identities-render

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
