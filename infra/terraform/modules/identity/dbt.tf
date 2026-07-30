resource "aws_iam_role" "dbt_transformer" {
  name               = "${var.name_prefix}-dbt-transformer"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "dbt_transformer" {
  statement {
    sid = "RunAthenaQueries"
    actions = [
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
    ]
    resources = [var.athena_workgroup_arn]
  }
  statement {
    sid = "ReadCuratedCatalog"
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
      local.curated_database_arns,
      local.curated_table_arns,
    )
  }
  statement {
    sid = "ManageAnalyticsCatalog"
    actions = [
      "glue:CreateTable",
      "glue:DeleteTable",
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.analytics_database_arns,
      local.analytics_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = var.parameter_arns.dbt_transformer
  }
  statement {
    sid     = "GetTransformerBucketLocations"
    actions = ["s3:GetBucketLocation"]
    resources = [
      var.bucket_arns.curated,
      var.bucket_arns.analytics,
      var.bucket_arns["query-results"],
    ]
  }
  statement {
    sid     = "ListTransformerDataBuckets"
    actions = ["s3:ListBucket"]
    resources = [
      var.bucket_arns.curated,
      var.bucket_arns.analytics,
    ]
  }
  statement {
    sid       = "ListAthenaQueryResults"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns["query-results"]]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.athena_result_prefixes.dbt_transformer,
        "${var.athena_result_prefixes.dbt_transformer}/*",
      ]
    }
  }
  statement {
    sid     = "ReadTransformerObjects"
    actions = ["s3:GetObject"]
    resources = [
      "${var.bucket_arns.curated}/*",
      "${var.bucket_arns.analytics}/*",
      "${var.bucket_arns["query-results"]}/${var.athena_result_prefixes.dbt_transformer}/*",
    ]
  }
  statement {
    sid = "ManageAnalyticsObjects"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.analytics}/*"]
  }
  statement {
    sid       = "WriteQueryResults"
    actions   = ["s3:AbortMultipartUpload", "s3:PutObject"]
    resources = ["${var.bucket_arns["query-results"]}/${var.athena_result_prefixes.dbt_transformer}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "dbt_transformer" {
  role   = aws_iam_role.dbt_transformer.id
  policy = data.aws_iam_policy_document.dbt_transformer.json
}
