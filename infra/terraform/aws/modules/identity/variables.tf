variable "name_prefix" {
  type        = string
  description = "Environment-qualified prefix used for workload IAM role names."

  validation {
    condition     = length(trimspace(var.name_prefix)) > 0
    error_message = "name_prefix must not be empty."
  }
}

variable "account_id" {
  type        = string
  description = "AWS account identifier used to build regional Glue resource ARNs."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must contain exactly 12 digits."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS Region containing the workload resources."
}

variable "operator_principals" {
  type        = set(string)
  description = "IAM principals allowed to assume human or CI operator roles."

  validation {
    condition     = length(var.operator_principals) > 0
    error_message = "At least one operator principal is required."
  }
}

variable "roles_anywhere_trust_anchor_arn" {
  type        = string
  description = "IAM Roles Anywhere trust anchor allowed to assume external runtime roles."
}

variable "bucket_arns" {
  type = object({
    landing       = string
    curated       = string
    analytics     = string
    artifacts     = string
    query_results = string
  })
  description = "Lakehouse bucket ARNs exposed to workload IAM policies."
}

variable "parameter_arns" {
  type        = map(set(string))
  description = "Exact non-secret Parameter Store resources readable by each workload."

  validation {
    condition = alltrue([
      for arns in values(var.parameter_arns) : length(arns) > 0
    ])
    error_message = "Every workload must declare at least one runtime parameter ARN."
  }
}

variable "kms_key_arn" {
  type        = string
  description = "KMS key used to encrypt lakehouse S3 objects."
}

variable "emr_application_arn" {
  type        = string
  description = "EMR Serverless application operated by Airflow."
}

variable "athena_workgroup_arn" {
  type        = string
  description = "Athena workgroup available to query workloads."
}

variable "athena_result_prefixes" {
  type = object({
    dbt_transformer = string
    arxiv_inspector = string
  })
  description = "Isolated S3 query-result prefixes for each Athena workload."

  validation {
    condition = (
      alltrue([
        for prefix in values(var.athena_result_prefixes) :
        length(prefix) > 0 && trim(prefix, "/") == prefix
      ]) &&
      length(toset(values(var.athena_result_prefixes))) == length(values(var.athena_result_prefixes))
    )
    error_message = "Athena result prefixes must be normalized and unique per workload."
  }
}

variable "airflow_secret_arns" {
  type        = set(string)
  description = "Domain-scoped Secrets Manager resources readable by Airflow."

  validation {
    condition     = length(var.airflow_secret_arns) > 0
    error_message = "Airflow requires at least one managed secret."
  }
}

variable "airflow_bootstrap_secret_arn" {
  type        = string
  description = "Airflow bootstrap secret readable by the services deployer."
}

variable "ocr_secret_arns" {
  type        = set(string)
  description = "Remote-provider credentials readable by the OCR worker."
}

variable "container_repository_arns" {
  type        = set(string)
  description = "Service image repositories writable by the image publisher."

  validation {
    condition     = length(var.container_repository_arns) > 0
    error_message = "The image publisher requires at least one ECR repository."
  }
}

variable "arxiv_inspector_access" {
  type = object({
    databases        = set(string)
    curated_prefixes = set(string)
  })
  description = "Glue databases and curated S3 prefixes readable by ArXiv Inspector."

  validation {
    condition = (
      length(var.arxiv_inspector_access.databases) > 0 &&
      length(var.arxiv_inspector_access.curated_prefixes) > 0 &&
      alltrue([
        for database in var.arxiv_inspector_access.databases :
        can(regex("^[a-z_][a-z0-9_]*$", database))
      ]) &&
      alltrue([
        for prefix in var.arxiv_inspector_access.curated_prefixes :
        length(prefix) > 0 && trim(prefix, "/") == prefix
      ])
    )
    error_message = "ArXiv Inspector databases and curated prefixes must be non-empty and normalized."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to workload IAM roles."
  default     = {}
}
