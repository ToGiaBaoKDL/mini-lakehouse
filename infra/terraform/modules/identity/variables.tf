variable "name_prefix" {
  type = string
}

variable "account_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "trusted_principal_arns" {
  type = list(string)
}

variable "bucket_arns" {
  type = map(string)
}

variable "parameter_arns" {
  type        = map(set(string))
  description = "Exact non-secret Parameter Store resources readable by each workload."
}

variable "kms_key_arn" {
  type = string
}

variable "emr_application_arn" {
  type = string
}

variable "athena_workgroup_arn" {
  type = string
}

variable "athena_result_prefixes" {
  type = object({
    dbt_transformer    = string
    document_inspector = string
  })

  validation {
    condition = alltrue([
      for prefix in values(var.athena_result_prefixes) :
      length(prefix) > 0 && trim(prefix, "/") == prefix
    ])
    error_message = "Athena result prefixes must be non-empty and must not start or end with '/'."
  }
}

variable "airflow_connection_secret_arns" {
  type = list(string)
}

variable "document_inspector_access" {
  type = object({
    databases        = set(string)
    curated_prefixes = set(string)
  })
}

variable "tags" {
  type    = map(string)
  default = {}
}
