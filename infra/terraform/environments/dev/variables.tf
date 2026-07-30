variable "aws_profile" {
  type    = string
  default = null
}

variable "trusted_principal_arns" {
  type        = list(string)
  description = "IAM principals allowed to assume local service roles."

  validation {
    condition     = length(var.trusted_principal_arns) > 0
    error_message = "At least one explicit trusted principal ARN is required."
  }
}

variable "document_inspector_access" {
  type = object({
    databases        = set(string)
    curated_prefixes = set(string)
  })
  description = "Glue databases and curated S3 prefixes readable by Document Inspector."

  validation {
    condition = alltrue(
      [for name in var.document_inspector_access.databases : can(regex("^[a-z_][a-z0-9_]*$", name))]
      ) && alltrue(
      [
        for prefix in var.document_inspector_access.curated_prefixes :
        length(prefix) > 0 && trim(prefix, "/") == prefix
      ]
    )
    error_message = "Document Inspector databases and curated prefixes must be normalized identifiers."
  }
}

variable "alert_email" {
  type        = string
  description = "Non-secret Airflow notification destination."
}

variable "slack_channel" {
  type        = string
  description = "Non-secret Airflow notification channel."
}
