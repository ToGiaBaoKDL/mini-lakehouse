data "aws_partition" "current" {}

data "aws_iam_policy_document" "operator_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.trusted_principal_arns
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
  document_inspector_database_arns = [
    for database in var.document_inspector_access.databases :
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:database/${database}"
  ]
  document_inspector_table_arns = [
    for database in var.document_inspector_access.databases :
    "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:table/${database}/*"
  ]
  document_inspector_curated_prefixes = flatten([
    for prefix in var.document_inspector_access.curated_prefixes : [
      prefix,
      "${prefix}/*",
    ]
  ])
  document_inspector_curated_object_arns = [
    for prefix in var.document_inspector_access.curated_prefixes :
    "${var.bucket_arns.curated}/${prefix}/*"
  ]
  ingestion_bucket_arns = [
    var.bucket_arns.landing,
    var.bucket_arns.curated,
  ]
  ingestion_object_arns = [for arn in local.ingestion_bucket_arns : "${arn}/*"]
}
