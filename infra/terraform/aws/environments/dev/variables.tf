variable "aws_profile" {
  type        = string
  description = "Optional local AWS profile used for Terraform operations."
  default     = null
}

variable "operator_principals" {
  type        = set(string)
  description = "IAM principals allowed to assume human or CI operator roles."

  validation {
    condition     = length(var.operator_principals) > 0
    error_message = "At least one operator principal is required."
  }
}

variable "roles_anywhere_ca_certificate_path" {
  type        = string
  description = "Path to the public PEM CA certificate trusted for external workload identities."

  validation {
    condition = (
      startswith(var.roles_anywhere_ca_certificate_path, "/") ||
      startswith(var.roles_anywhere_ca_certificate_path, "~/")
    )
    error_message = "roles_anywhere_ca_certificate_path must be absolute or start with ~/."
  }
}

variable "arxiv_inspector_access" {
  type = object({
    databases        = set(string)
    curated_prefixes = set(string)
  })
  description = "Glue databases and curated S3 prefixes readable by ArXiv Inspector."
}

variable "alert_email" {
  type        = string
  description = "Non-secret Airflow notification destination."
}

variable "slack_channel" {
  type        = string
  description = "Non-secret Airflow notification channel."
}
