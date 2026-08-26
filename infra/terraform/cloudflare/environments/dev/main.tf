locals {
  tunnel_name = "tgbao-dev-services"
  applications = {
    airflow = {
      hostname = "airflow.tgblab.io.vn"
      origin   = "http://127.0.0.1:8080"
    }
    arxiv_lens = {
      hostname = "arxiv.tgblab.io.vn"
      origin   = "http://127.0.0.1:8501"
    }
    lightdash = {
      hostname = "analytics.tgblab.io.vn"
      origin   = "http://127.0.0.1:8081"
    }
    signoz = {
      hostname = "observe.tgblab.io.vn"
      origin   = "http://127.0.0.1:8082"
    }
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "services" {
  account_id = var.account_id
  name       = local.tunnel_name
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "services" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.services.id
  source     = "cloudflare"

  config = {
    ingress = concat(
      [for application in values(local.applications) : {
        hostname = application.hostname
        service  = application.origin
      }],
      [{ service = "http_status:404" }],
    )
  }
}

resource "cloudflare_dns_record" "application" {
  for_each = local.applications

  zone_id = var.zone_id
  name    = each.value.hostname
  content = "${cloudflare_zero_trust_tunnel_cloudflared.services.id}.cfargotunnel.com"
  type    = "CNAME"
  proxied = true
  ttl     = 1
  comment = "Managed by Terraform for ${each.key}."
}

resource "cloudflare_zero_trust_access_application" "application" {
  for_each = local.applications

  zone_id          = var.zone_id
  name             = "tgbao-dev-${replace(each.key, "_", "-")}"
  domain           = each.value.hostname
  type             = "self_hosted"
  session_duration = "24h"
  destinations = [{
    type = "public"
    uri  = each.value.hostname
  }]
  policies = [{
    name       = "Allow approved users"
    decision   = "allow"
    precedence = 1
    include = [for email in var.access_allowed_emails : {
      email = { email = email }
    }]
  }]
}
