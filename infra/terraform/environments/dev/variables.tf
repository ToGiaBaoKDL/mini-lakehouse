variable "aws_profile" {
  type        = string
  description = "Optional local AWS profile used for Terraform operations."
  default     = null
}

variable "role_trust" {
  type        = map(set(string))
  description = "Explicit IAM principals trusted by each workload role."
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
