output "application_urls" {
  description = "Cloudflare Access-protected application URLs."
  value = {
    for name, application in local.applications : name => "https://${application.hostname}"
  }
}

output "tunnel_id" {
  description = "Cloudflare Tunnel ID used when enrolling the services-host connector."
  value       = cloudflare_zero_trust_tunnel_cloudflared.services.id
}
