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

variable "image_ocid" {
  type        = string
  description = "Pinned Canonical Ubuntu 24.04 AArch64 image OCID for the services host."

  validation {
    condition     = startswith(var.image_ocid, "ocid1.image.")
    error_message = "image_ocid must be an OCI image OCID."
  }
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
