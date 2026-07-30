resource "aws_iam_role" "document_inspector" {
  name               = "${var.name_prefix}-document-inspector"
  assume_role_policy = data.aws_iam_policy_document.operator_trust["document_inspector"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "document_inspector" {
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
      local.document_inspector_database_arns,
      local.document_inspector_table_arns,
    )
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.document_inspector
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
      values   = local.document_inspector_curated_prefixes
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
        var.athena_result_prefixes.document_inspector,
        "${var.athena_result_prefixes.document_inspector}/*",
      ]
    }
  }
  statement {
    sid       = "ReadDocumentObjects"
    actions   = ["s3:GetObject"]
    resources = local.document_inspector_curated_object_arns
  }
  statement {
    sid = "ManageQueryResults"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.query_results}/${var.athena_result_prefixes.document_inspector}/*"]
  }
  statement {
    sid       = "UseLakehouseKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "document_inspector" {
  role   = aws_iam_role.document_inspector.id
  policy = data.aws_iam_policy_document.document_inspector.json
}
