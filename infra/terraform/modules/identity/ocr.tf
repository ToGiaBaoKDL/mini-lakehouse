resource "aws_iam_role" "ocr_worker" {
  name               = "${var.name_prefix}-ocr-worker"
  assume_role_policy = data.aws_iam_policy_document.operator_trust["ocr_worker"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "ocr_worker" {
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.ocr_worker
  }
  statement {
    sid       = "ReadProviderCredentials"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = var.ocr_secret_arns
  }
  statement {
    sid = "UpdateCuratedIceberg"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.glue_catalog_arn],
      local.curated_database_arns,
      local.curated_table_arns,
    )
  }
  statement {
    sid       = "ListCuratedStorage"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [var.bucket_arns.curated]
  }
  statement {
    sid = "UpdateCuratedStorage"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.curated}/*"]
  }
  statement {
    sid = "UseStorageKey"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey",
    ]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "ocr_worker" {
  role   = aws_iam_role.ocr_worker.id
  policy = data.aws_iam_policy_document.ocr_worker.json
}
