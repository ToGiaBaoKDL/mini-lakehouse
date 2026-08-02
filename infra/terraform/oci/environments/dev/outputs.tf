output "instance_id" {
  description = "OCID of the self-hosted dev services instance."
  value       = oci_core_instance.services.id
}

output "hostname" {
  description = "Tailscale and OCI hostname of the dev services instance."
  value       = local.name
}
