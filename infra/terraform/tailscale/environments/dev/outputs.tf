output "services_auth_key" {
  description = "Single-use key consumed by OCI cloud-init to enroll the services host."
  value       = tailscale_tailnet_key.services.key
  sensitive   = true
}

output "github_deployer_client_id" {
  description = "Public Tailscale workload identity client ID for the protected GitHub environment."
  value       = tailscale_federated_identity.github_deployer.id
}

output "github_deployer_audience" {
  description = "OIDC audience requested by GitHub Actions for Tailscale federation."
  value       = "api.tailscale.com/${tailscale_federated_identity.github_deployer.id}"
}
