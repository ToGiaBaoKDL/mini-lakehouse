data "aws_partition" "current" {}

data "aws_iam_policy_document" "operator_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.operator_principal_arns
    }
  }
}

locals {
  external_runtime_workloads = toset([
    "airflow",
    "arxiv_inspector",
    "metadata_postgres",
    "services_deployer",
    "dbt_transformer",
    "ocr_worker",
  ])
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
}
