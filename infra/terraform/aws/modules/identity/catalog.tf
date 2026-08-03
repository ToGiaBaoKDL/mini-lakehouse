resource "aws_iam_role" "catalog_admin" {
  name               = "${var.name_prefix}-catalog-admin"
  assume_role_policy = data.aws_iam_policy_document.catalog_admin_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "catalog_admin" {
  statement {
    sid = "ApplyContractManagedCatalog"
    actions = [
      "glue:CreateDatabase",
      "glue:CreateTable",
      "glue:UpdateDatabase",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.landing_database_arns,
      local.curated_database_arns,
      local.analytics_database_arns,
      local.landing_table_arns,
      local.curated_table_arns,
    )
  }
  statement {
    sid = "ReadLakehouseCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.landing_database_arns,
      local.curated_database_arns,
      local.analytics_database_arns,
      local.landing_table_arns,
      local.curated_table_arns,
      local.analytics_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.catalog_admin
  }
  statement {
    sid       = "ListManagedDataBuckets"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = local.ingestion_bucket_arns
  }
  statement {
    sid       = "ManageIcebergMetadata"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = local.ingestion_object_arns
  }
  statement {
    sid       = "ListAnalyticsMetadata"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [var.bucket_arns.analytics]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.analytics_metadata_prefixes
    }
  }
  statement {
    sid       = "ReadAnalyticsMetadata"
    actions   = ["s3:GetObject"]
    resources = local.analytics_metadata_object_arns
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "catalog_admin" {
  role   = aws_iam_role.catalog_admin.id
  policy = data.aws_iam_policy_document.catalog_admin.json
}
