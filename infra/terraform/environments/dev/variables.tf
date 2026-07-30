variable "aws_profile" {
  type        = string
  description = "Optional local AWS profile used for Terraform operations."
  default     = null
}

variable "trusted_principals" {
  type = object({
    airflow            = set(string)
    catalog_admin      = set(string)
    dbt_transformer    = set(string)
    document_inspector = set(string)
    emr_deployer       = set(string)
  })
  description = "Explicit IAM principals trusted by each self-hosted or operator workload."
}

variable "document_inspector_access" {
  type = object({
    databases        = set(string)
    curated_prefixes = set(string)
  })
  description = "Glue databases and curated S3 prefixes readable by Document Inspector."
}

variable "alert_email" {
  type        = string
  description = "Non-secret Airflow notification destination."
}

variable "slack_channel" {
  type        = string
  description = "Non-secret Airflow notification channel."
}
