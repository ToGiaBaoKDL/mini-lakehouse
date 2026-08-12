AWS_TERRAFORM_STATE_DIR := infra/terraform/aws/bootstrap/state
AWS_TERRAFORM_DIR := infra/terraform/aws/environments/$(LAKEHOUSE_ENVIRONMENT)
OCI_TERRAFORM_DIR := infra/terraform/oci/environments/$(LAKEHOUSE_ENVIRONMENT)
TAILSCALE_TERRAFORM_DIR := infra/terraform/tailscale/environments/$(LAKEHOUSE_ENVIRONMENT)
GITHUB_TERRAFORM_DIR := infra/terraform/github/environments/$(LAKEHOUSE_ENVIRONMENT)
CLOUDFLARE_TERRAFORM_DIR := infra/terraform/cloudflare/environments/$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_CACHE_DIR ?= $(HOME)/.cache/lakehouse/terraform
TF_PLUGIN_CACHE_DIR ?= $(TERRAFORM_CACHE_DIR)/plugins
TF_REGISTRY_CLIENT_TIMEOUT ?= 30
TF_REGISTRY_DISCOVERY_RETRY ?= 3
AWS_STATE_FILE := $(TERRAFORM_CACHE_DIR)/state/aws-bootstrap.tfstate
AWS_STATE_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/aws-state
AWS_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/aws-$(LAKEHOUSE_ENVIRONMENT)
OCI_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/oci-$(LAKEHOUSE_ENVIRONMENT)
TAILSCALE_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/tailscale-$(LAKEHOUSE_ENVIRONMENT)
GITHUB_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/github-$(LAKEHOUSE_ENVIRONMENT)
CLOUDFLARE_TERRAFORM_DATA_DIR := $(TERRAFORM_CACHE_DIR)/data/cloudflare-$(LAKEHOUSE_ENVIRONMENT)
TERRAFORM_VALIDATE_DATA_DIR := /tmp/lakehouse-terraform-validate-$(LOCAL_UID)
AWS_STATE_TERRAFORM := TF_DATA_DIR="$(AWS_STATE_TERRAFORM_DATA_DIR)" terraform -chdir="$(AWS_TERRAFORM_STATE_DIR)"
AWS_TERRAFORM := TF_DATA_DIR="$(AWS_TERRAFORM_DATA_DIR)" terraform -chdir="$(AWS_TERRAFORM_DIR)"
OCI_TERRAFORM := TF_DATA_DIR="$(OCI_TERRAFORM_DATA_DIR)" terraform -chdir="$(OCI_TERRAFORM_DIR)"
TAILSCALE_TERRAFORM := TF_DATA_DIR="$(TAILSCALE_TERRAFORM_DATA_DIR)" terraform -chdir="$(TAILSCALE_TERRAFORM_DIR)"
GITHUB_TERRAFORM := TF_DATA_DIR="$(GITHUB_TERRAFORM_DATA_DIR)" terraform -chdir="$(GITHUB_TERRAFORM_DIR)"
CLOUDFLARE_TERRAFORM := TF_DATA_DIR="$(CLOUDFLARE_TERRAFORM_DATA_DIR)" terraform -chdir="$(CLOUDFLARE_TERRAFORM_DIR)"
SERVICES_HOST ?= tgbao-dev-services
SERVICES_HOST_USER ?= ubuntu

.PHONY: terraform-cache terraform-fmt terraform-validate \
	aws-state-init aws-state-plan aws-state-apply \
	aws-init aws-plan aws-apply \
	tailscale-init tailscale-plan tailscale-apply tailscale-policy-import \
	github-init github-plan github-apply \
	cloudflare-init cloudflare-plan cloudflare-apply cloudflare-secret-sync \
	oci-init oci-plan oci-apply \
	workload-pki-init workload-identities-render \
	workload-identities-install

export TF_PLUGIN_CACHE_DIR
export TF_REGISTRY_CLIENT_TIMEOUT
export TF_REGISTRY_DISCOVERY_RETRY

terraform-cache:
	@install -d -m 0700 "$(TF_PLUGIN_CACHE_DIR)" "$(dir $(AWS_STATE_FILE))"

terraform-fmt: ## Check Terraform formatting.
	terraform -chdir=infra/terraform fmt -check -recursive

aws-state-init: terraform-cache
	$(AWS_STATE_TERRAFORM) init -backend-config="path=$(AWS_STATE_FILE)"

aws-state-plan: aws-state-init ## Plan the AWS remote-state bootstrap stack.
	$(AWS_STATE_TERRAFORM) plan

aws-state-apply: aws-state-init ## Create the versioned AWS remote-state bucket.
	$(AWS_STATE_TERRAFORM) apply

aws-init: aws-state-init
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(AWS_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

aws-plan: aws-init ## Plan the AWS data platform.
	$(AWS_TERRAFORM) plan

aws-apply: aws-init ## Apply the reviewed AWS data-platform plan.
	$(AWS_TERRAFORM) apply

tailscale-init: aws-state-init
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(TAILSCALE_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

tailscale-plan: tailscale-init ## Plan private access to the services host.
	$(TAILSCALE_TERRAFORM) plan

tailscale-apply: tailscale-init ## Apply private access to the services host.
	$(TAILSCALE_TERRAFORM) apply

tailscale-policy-import: tailscale-init ## Import an existing tailnet ACL once.
	$(TAILSCALE_TERRAFORM) import tailscale_acl.policy acl

github-init: aws-state-init
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(GITHUB_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

github-plan: github-init ## Plan the GitHub repository environment and delivery variables.
	@STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		TF_VAR_state_bucket="$${STATE_BUCKET}" $(GITHUB_TERRAFORM) plan

github-apply: github-init ## Apply the reviewed GitHub repository configuration.
	@STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		TF_VAR_state_bucket="$${STATE_BUCKET}" $(GITHUB_TERRAFORM) apply

cloudflare-init: aws-state-init
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(CLOUDFLARE_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

cloudflare-plan: cloudflare-init ## Plan Cloudflare Tunnel, DNS, and Access.
	$(CLOUDFLARE_TERRAFORM) plan

cloudflare-apply: cloudflare-init ## Apply the reviewed Cloudflare edge configuration.
	$(CLOUDFLARE_TERRAFORM) apply

cloudflare-secret-sync: cloudflare-init aws-init ## Synchronize the connector token into AWS Secrets Manager.
	@set -eu; \
		ACCOUNT_ID="$$($(CLOUDFLARE_TERRAFORM) output -raw account_id)"; \
		TUNNEL_ID="$$($(CLOUDFLARE_TERRAFORM) output -raw tunnel_id)"; \
		SECRET_ID="$$($(AWS_TERRAFORM) output -raw cloudflare_tunnel_secret_id)"; \
		infra/runtime/cloudflare/sync-secret "$${ACCOUNT_ID}" "$${TUNNEL_ID}" "$${SECRET_ID}"

oci-init: aws-state-init
	@set -eu; \
		STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		$(OCI_TERRAFORM) init -backend-config="bucket=$${STATE_BUCKET}"

oci-plan: oci-init ## Plan the OCI services host.
	@STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		TF_VAR_state_bucket="$${STATE_BUCKET}" $(OCI_TERRAFORM) plan

oci-apply: oci-init ## Apply the reviewed OCI services-host plan.
	@STATE_BUCKET="$$($(AWS_STATE_TERRAFORM) output -raw bucket_name)"; \
		TF_VAR_state_bucket="$${STATE_BUCKET}" $(OCI_TERRAFORM) apply

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
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/github" "$(GITHUB_TERRAFORM_DIR)"; \
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/cloudflare" "$(CLOUDFLARE_TERRAFORM_DIR)"; \
		validate_root "$(TERRAFORM_VALIDATE_DATA_DIR)/oci" "$(OCI_TERRAFORM_DIR)"

workload-pki-init: ## Create the local workload CA outside the repository.
	infra/runtime/identity/workload-identities init "$(AWS_IDENTITY_DIR)"

workload-identities-render: aws-init ## Issue certificates and render configs from applied AWS outputs.
	TF_DATA_DIR="$(AWS_TERRAFORM_DATA_DIR)" \
		infra/runtime/identity/workload-identities render "$(AWS_IDENTITY_DIR)" "$(AWS_TERRAFORM_DIR)"

workload-identities-install: aws-init ## Install leaf workload identities on the private services host.
	@command -v tailscale >/dev/null
	@set -eu; \
		WORKLOADS="$$(TF_DATA_DIR="$(AWS_TERRAFORM_DATA_DIR)" \
			terraform -chdir="$(AWS_TERRAFORM_DIR)" output -json roles_anywhere_workloads \
			| jq -r 'keys[] | gsub("_"; "-")')"; \
		test -n "$${WORKLOADS}"; \
		for WORKLOAD in $${WORKLOADS}; do \
			for FILE in certificate.pem private-key.pem config; do \
				test -r "$(AWS_IDENTITY_DIR)/$${WORKLOAD}/$${FILE}" || { \
					printf '%s\n' "Missing $${FILE} for $${WORKLOAD}."; exit 1; \
				}; \
			done; \
		done; \
		tar -C "$(AWS_IDENTITY_DIR)" --exclude='*/host-config' -cf - $${WORKLOADS} \
		| tailscale ssh "$(SERVICES_HOST_USER)@$(SERVICES_HOST)" \
			'set -eu; target="$$HOME/.config/lakehouse/$(LAKEHOUSE_ENVIRONMENT)/aws"; \
			install -d -m 0700 "$$target"; tar -C "$$target" -xf -; \
			chmod 0700 "$$target"/*; chmod 0600 "$$target"/*/private-key.pem "$$target"/*/config; \
			for config in "$$target"/*/config; do \
				workload="$$(basename "$$(dirname "$$config")")"; \
				sed "s#/run/aws/#$$target/$$workload/#g" "$$config" >"$${config%/config}/host-config"; \
			done; \
			chmod 0600 "$$target"/*/host-config'
