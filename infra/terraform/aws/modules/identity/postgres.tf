resource "aws_iam_role" "metadata_postgres" {
  name               = "${var.name_prefix}-metadata-postgres"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["metadata_postgres"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "metadata_postgres" {
  statement {
    sid       = "ReadMetadataDatabaseSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.metadata_postgres_secret_arns
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.metadata_postgres
  }
  statement {
    sid       = "GetBackupBucketLocation"
    actions   = ["s3:GetBucketLocation"]
    resources = [var.bucket_arns.backups]
  }
  statement {
    sid       = "ListBackupObjects"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arns.backups]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["metadata-postgres", "metadata-postgres/*"]
    }
  }
  statement {
    sid = "ManageMetadataBackupObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = ["${var.bucket_arns.backups}/metadata-postgres/*"]
  }
  statement {
    sid       = "UseBackupKey"
    actions   = ["kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "metadata_postgres" {
  role   = aws_iam_role.metadata_postgres.id
  policy = data.aws_iam_policy_document.metadata_postgres.json
}
