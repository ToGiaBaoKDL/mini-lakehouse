variable "oci_profile" {
  type        = string
  description = "Profile name in the OCI CLI configuration file used by Terraform."
  default     = "DEFAULT"
}

variable "tenancy_ocid" {
  type        = string
  description = "OCI tenancy OCID used to discover availability domains."
}

variable "compartment_ocid" {
  type        = string
  description = "OCI compartment OCID that owns the dev services host."
}

variable "region" {
  type        = string
  description = "OCI region where the dev services host runs."
}

variable "ssh_authorized_key" {
  type        = string
  description = "OpenSSH public key used as break-glass access over the Tailscale network."
  sensitive   = true
}

variable "tailscale_auth_key" {
  type        = string
  description = "Single-use, tagged Tailscale enrollment key for cloud-init."
  sensitive   = true
}
