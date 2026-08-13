locals {
  services_tag = "tag:tgbao-dev-services"
  ci_tag       = "tag:tgbao-dev-ci"
  github_repository = {
    owner    = "ToGiaBaoKDL"
    owner_id = "136962009"
    name     = "mini-lakehouse"
    id       = "1313563456"
  }
  github_environment_subject = "repo:${local.github_repository.owner}@${local.github_repository.owner_id}/${local.github_repository.name}@${local.github_repository.id}:environment:dev"
  operator_services_ports = [
    "tcp:22",
    "tcp:8080",
    "tcp:8081",
    "tcp:8501",
  ]
  ci_services_ports = [
    "tcp:22",
    "tcp:8081",
  ]
}

resource "tailscale_acl" "policy" {
  acl = jsonencode({
    tagOwners = {
      (local.services_tag) = [var.owner]
      (local.ci_tag)       = [var.owner]
    }
    grants = [
      {
        src = [var.owner]
        dst = [local.services_tag]
        ip  = local.operator_services_ports
      },
      {
        src = [local.ci_tag]
        dst = [local.services_tag]
        ip  = local.ci_services_ports
      },
    ]
    ssh = [
      {
        action = "check"
        src    = [var.owner]
        dst    = [local.services_tag]
        users  = ["autogroup:nonroot"]
      },
      {
        action = "accept"
        src    = [local.ci_tag]
        dst    = [local.services_tag]
        users  = ["ubuntu"]
      },
    ]
    tests = [
      {
        src = var.owner
        accept = [
          "${local.services_tag}:22",
          "${local.services_tag}:8080",
          "${local.services_tag}:8081",
          "${local.services_tag}:8501",
        ]
      },
      {
        src = local.ci_tag
        accept = [
          "${local.services_tag}:22",
          "${local.services_tag}:8081",
        ]
        deny = [
          "${local.services_tag}:8080",
          "${local.services_tag}:8501",
        ]
      },
    ]
  })
}

resource "tailscale_federated_identity" "github_deployer" {
  depends_on = [tailscale_acl.policy]

  description = "GitHub Actions dev deployer"
  issuer      = "https://token.actions.githubusercontent.com"
  subject     = local.github_environment_subject
  scopes      = ["auth_keys", "devices:core"]
  tags        = [local.ci_tag]
  custom_claim_rules = {
    ref           = "refs/heads/main"
    repository_id = local.github_repository.id
  }
}

resource "tailscale_tailnet_key" "services" {
  depends_on = [tailscale_acl.policy]

  reusable            = false
  ephemeral           = false
  preauthorized       = true
  expiry              = 3600
  recreate_if_invalid = "always"
  tags                = [local.services_tag]
}
