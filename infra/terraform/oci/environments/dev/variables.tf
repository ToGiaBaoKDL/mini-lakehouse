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

variable "state_bucket" {
  type        = string
  description = "Existing S3 bucket containing the upstream Tailscale Terraform state."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket))
    error_message = "state_bucket must be a valid S3 bucket name."
  }
}
