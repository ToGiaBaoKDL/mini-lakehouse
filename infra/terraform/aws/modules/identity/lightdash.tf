resource "aws_iam_role" "lightdash" {
  name               = "${var.name_prefix}-lightdash"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["lightdash"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "lightdash" {
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
    sid = "ReadAnalyticsCatalog"
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
      local.analytics_database_arns_by_workload.lightdash,
      local.analytics_table_arns_by_workload.lightdash,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = var.parameter_arns.lightdash
  }
  statement {
    sid       = "ReadRuntimeSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.lightdash_secret_arns
  }
  statement {
    sid     = "GetOwnedBucketLocations"
    actions = ["s3:GetBucketLocation"]
    resources = [
      var.bucket_arns.analytics,
      var.bucket_arns.lightdash,
      var.bucket_arns.query_results,
    ]
  }
  statement {
    sid       = "ListAnalyticsData"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.analytics]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.analytics_prefixes_by_workload.lightdash
    }
  }
  statement {
    sid       = "ReadAnalyticsObjects"
    actions   = ["s3:GetObject"]
    resources = local.analytics_object_arns_by_workload.lightdash
  }
  statement {
    sid       = "ListLightdashStorage"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.lightdash]
  }
  statement {
    sid       = "ManageLightdashStorageObjects"
    actions   = ["s3:AbortMultipartUpload", "s3:DeleteObject", "s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arns.lightdash}/*"]
  }
  statement {
    sid       = "ListAthenaQueryResults"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.query_results]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.athena_result_prefixes.lightdash,
        "${var.athena_result_prefixes.lightdash}/*",
      ]
    }
  }
  statement {
    sid = "ManageAthenaQueryResults"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.query_results}/${var.athena_result_prefixes.lightdash}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "lightdash" {
  role   = aws_iam_role.lightdash.id
  policy = data.aws_iam_policy_document.lightdash.json
}
