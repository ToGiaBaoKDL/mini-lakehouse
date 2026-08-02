resource "aws_iam_role" "arxiv_inspector" {
  name               = "${var.name_prefix}-arxiv-inspector"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["arxiv_inspector"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "arxiv_inspector" {
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
    sid = "ReadDocumentCatalog"
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
      local.curated_database_arns_by_workload.arxiv_inspector,
      local.curated_table_arns_by_workload.arxiv_inspector,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.arxiv_inspector
  }
  statement {
    sid       = "GetDocumentBucketLocations"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.bucket_arns.curated, var.bucket_arns.query_results]
  }
  statement {
    sid       = "ListDocumentData"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.curated]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.curated_prefixes_by_workload.arxiv_inspector
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
        var.athena_result_prefixes.arxiv_inspector,
        "${var.athena_result_prefixes.arxiv_inspector}/*",
      ]
    }
  }
  statement {
    sid       = "ReadDocumentObjects"
    actions   = ["s3:GetObject"]
    resources = local.curated_object_arns_by_workload.arxiv_inspector
  }
  statement {
    sid = "ManageQueryResults"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.query_results}/${var.athena_result_prefixes.arxiv_inspector}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "arxiv_inspector" {
  role   = aws_iam_role.arxiv_inspector.id
  policy = data.aws_iam_policy_document.arxiv_inspector.json
}
