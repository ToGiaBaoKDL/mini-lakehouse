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

variable "catalog_admin_principal_arns" {
  type        = set(string)
  description = "Existing IAM principal ARNs allowed to assume the catalog administrator role."

  validation {
    condition = length(var.catalog_admin_principal_arns) > 0 && alltrue([
      for arn in var.catalog_admin_principal_arns : startswith(arn, "arn:")
    ])
    error_message = "At least one valid catalog administrator principal ARN is required."
  }
}

variable "github_oidc_provider_arn" {
  type        = string
  description = "GitHub Actions OIDC provider trusted by release publisher roles."
}

variable "github_environment_subject" {
  type        = string
  description = "Exact immutable GitHub OIDC subject for protected rollback and deployment jobs."

  validation {
    condition     = startswith(var.github_environment_subject, "repo:") && strcontains(var.github_environment_subject, ":environment:")
    error_message = "github_environment_subject must identify one repository environment."
  }
}

variable "github_main_subject" {
  type        = string
  description = "Exact immutable GitHub OIDC subject allowed to publish releases from main."

  validation {
    condition     = startswith(var.github_main_subject, "repo:") && endswith(var.github_main_subject, ":ref:refs/heads/main")
    error_message = "github_main_subject must identify the repository main branch."
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
    backups       = string
    lightdash     = string
    logs          = string
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

variable "athena_data_catalog_arn" {
  type        = string
  description = "Athena Data Catalog available to metadata-reading workloads."
}

variable "athena_workgroup_arn" {
  type        = string
  description = "Athena workgroup available to query workloads."
}

variable "athena_result_prefixes" {
  type        = map(string)
  description = "Isolated S3 query-result prefixes for each Athena workload."

  validation {
    condition = (
      contains(keys(var.athena_result_prefixes), "arxiv_inspector") &&
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

variable "metadata_postgres_secret_arns" {
  type        = set(string)
  description = "Bootstrap and application database secrets readable by metadata PostgreSQL."

  validation {
    condition     = length(var.metadata_postgres_secret_arns) > 0
    error_message = "Metadata PostgreSQL requires at least one managed secret."
  }
}

variable "lightdash_secret_arns" {
  type        = set(string)
  description = "Runtime and database secrets readable by Lightdash."

  validation {
    condition     = length(var.lightdash_secret_arns) == 2
    error_message = "Lightdash requires its runtime and database secrets."
  }
}

variable "lightdash_ci_secret_arn" {
  type        = string
  description = "Exact Secrets Manager resource readable by protected Lightdash deployment jobs."

  validation {
    condition     = startswith(var.lightdash_ci_secret_arn, "arn:")
    error_message = "lightdash_ci_secret_arn must be an ARN."
  }
}

variable "ocr_secret_arns" {
  type        = set(string)
  description = "Remote-provider credentials readable by the OCR worker."
}

variable "services_deployer_secret_arns" {
  type        = set(string)
  description = "Infrastructure connector secrets readable by the services deployer."

  validation {
    condition     = length(var.services_deployer_secret_arns) > 0
    error_message = "The services deployer requires at least one infrastructure connector secret."
  }
}

variable "container_repository_arns" {
  type        = set(string)
  description = "Service image repositories writable by the image publisher."

  validation {
    condition     = length(var.container_repository_arns) > 0
    error_message = "The image publisher requires at least one ECR repository."
  }
}

variable "workload_data_access" {
  type = object({
    curated = map(object({
      databases = set(string)
      prefixes  = set(string)
    }))
    analytics = map(object({
      databases = set(string)
      prefixes  = set(string)
    }))
  })
  description = "Reviewed Glue database and S3 prefix entitlements for data-consuming workloads."

  validation {
    condition = (
      alltrue([
        for workload in ["arxiv_inspector", "ocr_worker"] :
        contains(keys(var.workload_data_access.curated), workload)
      ]) &&
      length([
        for workload in keys(var.workload_data_access.analytics) : workload
        if startswith(workload, "dbt_")
      ]) > 0 &&
      alltrue([
        for workload in keys(var.workload_data_access.analytics) :
        contains(keys(var.workload_data_access.curated), workload)
        if startswith(workload, "dbt_")
      ]) &&
      alltrue([
        for access in concat(
          values(var.workload_data_access.curated),
          values(var.workload_data_access.analytics),
        ) : length(access.databases) > 0 && length(access.prefixes) > 0
      ]) &&
      alltrue(flatten([
        for access in concat(
          values(var.workload_data_access.curated),
          values(var.workload_data_access.analytics),
          ) : [for database in access.databases :
          can(regex("^[a-z_][a-z0-9_]*$", database))
        ]
      ])) &&
      alltrue(flatten([
        for access in concat(
          values(var.workload_data_access.curated),
          values(var.workload_data_access.analytics),
          ) : [for prefix in access.prefixes :
          length(prefix) > 0 && trim(prefix, "/") == prefix
        ]
      ]))
    )
    error_message = "Workload data access must cover each consumer with non-empty normalized databases and prefixes."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to workload IAM roles."
  default     = {}
}
