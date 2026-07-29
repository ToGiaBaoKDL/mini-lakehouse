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

variable "athena_query_results_prefix" {
  type = string
}

variable "airflow_connection_secret_arns" {
  type = list(string)
}

variable "document_inspector_database_names" {
  type = set(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
