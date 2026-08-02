locals {
  services_tag = "tag:tgbao-dev-services"
  services_ports = [
    "tcp:22",
    "tcp:8080",
    "tcp:8501",
  ]
}

resource "tailscale_acl" "policy" {
  acl = jsonencode({
    tagOwners = {
      (local.services_tag) = [var.owner]
    }
    grants = [{
      src = [var.owner]
      dst = [local.services_tag]
      ip  = local.services_ports
    }]
    ssh = [{
      action = "check"
      src    = [var.owner]
      dst    = [local.services_tag]
      users  = ["autogroup:nonroot"]
    }]
    tests = [{
      src = var.owner
      accept = [
        "${local.services_tag}:22",
        "${local.services_tag}:8080",
        "${local.services_tag}:8501",
      ]
    }]
  })
}

resource "tailscale_tailnet_key" "services" {
  reusable            = false
  ephemeral           = false
  preauthorized       = true
  expiry              = 3600
  recreate_if_invalid = "always"
  tags                = [local.services_tag]
}
