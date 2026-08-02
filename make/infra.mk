AWS_TERRAFORM_STATE_DIR := infra/terraform/aws/bootstrap/state
AWS_TERRAFORM_DIR := infra/terraform/aws/environments/$(LAKEHOUSE_ENVIRONMENT)
OCI_TERRAFORM_DIR := infra/terraform/oci/environments/$(LAKEHOUSE_ENVIRONMENT)
TAILSCALE_TERRAFORM_DIR := infra/terraform/tailscale/environments/$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_CACHE_DIR ?= $(HOME)/.cache/lakehouse/terraform
TF_PLUGIN_CACHE_DIR ?= $(TERRAFORM_CACHE_DIR)/plugins
AWS_STATE_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/aws-state
AWS_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/aws-$(LAKEHOUSE_ENVIRONMENT)
OCI_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/oci-$(LAKEHOUSE_ENVIRONMENT)
TAILSCALE_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/tailscale-$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_VALIDATE_DATA_DIR := /tmp/lakehouse-terraform-validate-$(LOCAL_UID)
AWS_STATE_TERRAFORM := TF_DATA_DIR="$(AWS_STATE_TERRAFORM_DATA_DIR)" terraform -chdir="$(AWS_TERRAFORM_STATE_DIR)"
AWS_TERRAFORM := TF_DATA_DIR="$(AWS_TERRAFORM_DATA_DIR)" terraform -chdir="$(AWS_TERRAFORM_DIR)"
OCI_TERRAFORM := TF_DATA_DIR="$(OCI_TERRAFORM_DATA_DIR)" terraform -chdir="$(OCI_TERRAFORM_DIR)"
TAILSCALE_TERRAFORM := TF_DATA_DIR="$(TAILSCALE_TERRAFORM_DATA_DIR)" terraform -chdir="$(TAILSCALE_TERRAFORM_DIR)"
SERVICES_HOST ?= tgbao-dev-services
SERVICES_HOST_USER ?= ubuntu

.PHONY: terraform-cache terraform-fmt terraform-validate \
	aws-state-init aws-state-plan aws-state-apply \
	aws-init aws-plan aws-apply aws-destroy \
	tailscale-init tailscale-plan tailscale-apply tailscale-policy-import tailscale-auth-key \
	oci-init oci-plan oci-apply oci-destroy \
	workload-pki-init workload-identities-render \
	workload-identities-install

export TF_PLUGIN_CACHE_DIR

terraform-cache: ## Prepare the shared Terraform provider cache outside the repository.
	@install -d -m 0700 "$(TF_PLUGIN_CACHE_DIR)"

terraform-fmt: ## Check Terraform formatting.
	terraform -chdir=infra/terraform fmt -check -recursive

aws-state-init: terraform-cache ## Initialize the one-time AWS remote-state bootstrap stack.
	$(AWS_STATE_TERRAFORM) init

aws-state-plan: aws-state-init ## Plan the AWS remote-state bootstrap stack.
	$(AWS_STATE_TERRAFORM) plan

aws-state-apply: aws-state-init ## Create the versioned AWS remote-state bucket.
	$(AWS_STATE_TERRAFORM) apply

aws-init: terraform-cache ## Initialize AWS from the bootstrap state output.
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(AWS_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

aws-plan: aws-init ## Plan the AWS data platform.
	$(AWS_TERRAFORM) plan

aws-apply: aws-init ## Apply the reviewed AWS data-platform plan.
	$(AWS_TERRAFORM) apply

aws-destroy: aws-init ## Destroy the AWS data platform.
	$(AWS_TERRAFORM) destroy

tailscale-init: terraform-cache ## Initialize Tailscale from the bootstrap state output.
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(TAILSCALE_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

tailscale-plan: tailscale-init ## Plan private access to the services host.
	$(TAILSCALE_TERRAFORM) plan

tailscale-apply: tailscale-init ## Apply private access to the services host.
	$(TAILSCALE_TERRAFORM) apply

tailscale-policy-import: tailscale-init ## Import an existing tailnet ACL once.
	$(TAILSCALE_TERRAFORM) import tailscale_acl.policy acl

tailscale-auth-key: ## Print the current one-time OCI enrollment key.
	@$(TAILSCALE_TERRAFORM) output -raw services_auth_key

oci-init: terraform-cache ## Initialize OCI from the bootstrap state output.
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(OCI_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

oci-plan: oci-init ## Plan the OCI services host.
	@test -n "$${TF_VAR_tailscale_auth_key:-}" || { printf '%s\n' "TF_VAR_tailscale_auth_key is required."; exit 1; }
	$(OCI_TERRAFORM) plan

oci-apply: oci-init ## Apply the reviewed OCI services-host plan.
	@test -n "$${TF_VAR_tailscale_auth_key:-}" || { printf '%s\n' "TF_VAR_tailscale_auth_key is required."; exit 1; }
	$(OCI_TERRAFORM) apply

oci-destroy: oci-init ## Destroy the OCI services host.
	$(OCI_TERRAFORM) destroy

terraform-validate: terraform-cache ## Initialize without remote state and validate every Terraform root.
	@set -eu; \
		validate_root() { \
			DATA_DIR="$$1"; ROOT_DIR="$$2"; \
			TF_DATA_DIR="$${DATA_DIR}" terraform -chdir="$${ROOT_DIR}" \
				init -backend=false -lockfile=readonly; \
			TF_DATA_DIR="$${DATA_DIR}" terraform -chdir="$${ROOT_DIR}" validate; \
		}; \
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/state" "$(AWS_TERRAFORM_STATE_DIR)"; \
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/aws" "$(AWS_TERRAFORM_DIR)"; \
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/tailscale" "$(TAILSCALE_TERRAFORM_DIR)"; \
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/oci" "$(OCI_TERRAFORM_DIR)"

workload-pki-init: ## Create the local workload CA outside the repository.
	infra/runtime/workload-identities init "$(AWS_IDENTITY_DIR)"

workload-identities-render: ## Issue certificates and render configs from applied AWS outputs.
	TF_DATA_DIR="$(AWS_TERRAFORM_DATA_DIR)" \
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
