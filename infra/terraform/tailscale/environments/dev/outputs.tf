output "services_auth_key" {
  description = "Single-use key consumed by OCI cloud-init to enroll the services host."
  value       = tailscale_tailnet_key.services.key
  sensitive   = true
}
