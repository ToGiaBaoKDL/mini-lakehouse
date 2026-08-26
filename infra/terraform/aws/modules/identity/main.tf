data "aws_partition" "current" {}

data "aws_iam_policy_document" "catalog_admin_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.catalog_admin_principal_arns
    }
  }
}

locals {
  dbt_workloads = toset([
    for workload in keys(var.workload_data_access.analytics) : workload
    if startswith(workload, "dbt_")
  ])
  external_runtime_workloads = setunion(toset([
    "airflow",
    "arxiv_lens",
    "lightdash",
    "metadata_postgres",
    "services_deployer",
  ]), local.dbt_workloads)
}

check "dbt_workload_configuration" {
  assert {
    condition = alltrue([
      for workload in local.dbt_workloads :
      contains(keys(var.parameter_arns), workload) &&
      contains(keys(var.athena_result_prefixes), workload)
    ])
    error_message = "Every dbt workload needs exact Parameter Store and Athena result-prefix grants."
  }
}

data "aws_iam_policy_document" "external_runtime_trust" {
  for_each = local.external_runtime_workloads

  statement {
    sid = "AssumeWithWorkloadCertificate"
    actions = [
      "sts:AssumeRole",
      "sts:SetSourceIdentity",
      "sts:TagSession",
    ]
    principals {
      type        = "Service"
      identifiers = ["rolesanywhere.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [var.roles_anywhere_trust_anchor_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/x509Subject/CN"
      values   = ["${var.name_prefix}-${replace(each.key, "_", "-")}"]
    }
  }
}

data "aws_iam_policy_document" "emr_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["emr-serverless.amazonaws.com"]
    }
  }
}

locals {
  glue_catalog_arn = "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:catalog"
  landing_database_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/landing_*",
  ]
  curated_database_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/curated_*",
  ]
  analytics_database_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/analytics_*",
  ]
  landing_table_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/landing_*/*",
  ]
  curated_table_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/curated_*/*",
  ]
  analytics_table_arns = [
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/analytics_*/*",
  ]
  curated_database_arns_by_workload = {
    for workload, access in var.workload_data_access.curated : workload => [
      for database in access.databases :
      "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/${database}"
    ]
  }
  curated_table_arns_by_workload = {
    for workload, access in var.workload_data_access.curated : workload => [
      for database in access.databases :
      "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/${database}/*"
    ]
  }
  curated_prefixes_by_workload = {
    for workload, access in var.workload_data_access.curated : workload => flatten([
      for prefix in access.prefixes : [prefix, "${prefix}/*"]
    ])
  }
  curated_object_arns_by_workload = {
    for workload, access in var.workload_data_access.curated : workload => [
      for prefix in access.prefixes : "${var.bucket_arns.curated}/${prefix}/*"
    ]
  }
  analytics_database_arns_by_workload = {
    for workload, access in var.workload_data_access.analytics : workload => [
      for database in access.databases :
      "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/${database}"
    ]
  }
  analytics_table_arns_by_workload = {
    for workload, access in var.workload_data_access.analytics : workload => [
      for database in access.databases :
      "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/${database}/*"
    ]
  }
  analytics_prefixes_by_workload = {
    for workload, access in var.workload_data_access.analytics : workload => flatten([
      for prefix in access.prefixes : [prefix, "${prefix}/*"]
    ])
  }
  analytics_object_arns_by_workload = {
    for workload, access in var.workload_data_access.analytics : workload => [
      for prefix in access.prefixes : "${var.bucket_arns.analytics}/${prefix}/*"
    ]
  }
  ingestion_bucket_arns = [
    var.bucket_arns.landing,
    var.bucket_arns.curated,
  ]
  ingestion_object_arns = [for arn in local.ingestion_bucket_arns : "${arn}/*"]
  ingestion_metadata_object_arns = [
    "${var.bucket_arns.landing}/*/*/tables/*/metadata/*",
    "${var.bucket_arns.curated}/*/tables/*/metadata/*",
  ]
  analytics_metadata_prefixes = [
    "*/*/*/metadata",
    "*/*/*/metadata/*",
    "*/tables",
    "*/tables/*",
  ]
  analytics_metadata_object_arns = [
    "${var.bucket_arns.analytics}/*/*/*/metadata/*",
    "${var.bucket_arns.analytics}/*/tables/*/metadata/*",
  ]
}
