variable "state_bucket" {
  type        = string
  description = "Existing S3 bucket containing the upstream AWS and Tailscale Terraform states."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket))
    error_message = "state_bucket must be a valid S3 bucket name."
  }
}
