resource "aws_iam_role" "dbt_domain" {
  for_each = local.dbt_workloads

  name               = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust[each.key].json
  tags               = var.tags
}

data "aws_iam_policy_document" "dbt_domain" {
  for_each = local.dbt_workloads

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
      local.curated_database_arns_by_workload[each.key],
      local.curated_table_arns_by_workload[each.key],
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
      local.analytics_database_arns_by_workload[each.key],
      local.analytics_table_arns_by_workload[each.key],
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = var.parameter_arns[each.key]
  }
  statement {
    sid     = "GetTransformerBucketLocations"
    actions = ["s3:GetBucketLocation"]
    resources = [
      var.bucket_arns.curated,
      var.bucket_arns.analytics,
      var.bucket_arns.query_results,
    ]
  }
  statement {
    sid       = "ListCuratedData"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.curated]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.curated_prefixes_by_workload[each.key]
    }
  }
  statement {
    sid       = "ListAnalyticsData"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.analytics]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.analytics_prefixes_by_workload[each.key]
    }
  }
  statement {
    sid       = "ListAthenaQueryResults"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.query_results]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.athena_result_prefixes[each.key],
        "${var.athena_result_prefixes[each.key]}/*",
      ]
    }
  }
  statement {
    sid     = "ReadTransformerObjects"
    actions = ["s3:GetObject"]
    resources = concat(
      local.curated_object_arns_by_workload[each.key],
      local.analytics_object_arns_by_workload[each.key],
      ["${var.bucket_arns.query_results}/${var.athena_result_prefixes[each.key]}/*"],
    )
  }
  statement {
    sid = "ManageAnalyticsObjects"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:PutObject",
    ]
    resources = local.analytics_object_arns_by_workload[each.key]
  }
  statement {
    sid       = "WriteQueryResults"
    actions   = ["s3:AbortMultipartUpload", "s3:PutObject"]
    resources = ["${var.bucket_arns.query_results}/${var.athena_result_prefixes[each.key]}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "dbt_domain" {
  for_each = local.dbt_workloads

  role   = aws_iam_role.dbt_domain[each.key].id
  policy = data.aws_iam_policy_document.dbt_domain[each.key].json
}
