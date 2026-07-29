variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

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

variable "document_inspector_database_names" {
  type        = set(string)
  description = "Glue databases readable by the self-hosted Document Inspector."

  validation {
    condition = alltrue([
      for name in var.document_inspector_database_names :
      can(regex("^[a-z_][a-z0-9_]*$", name))
    ])
    error_message = "Document Inspector database names must be valid Glue identifiers."
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
