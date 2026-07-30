resource "aws_iam_role" "airflow" {
  name               = "${var.name_prefix}-airflow"
  assume_role_policy = data.aws_iam_policy_document.operator_trust["airflow"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "airflow" {
  statement {
    sid = "OperateEmrApplication"
    actions = [
      "emr-serverless:CancelJobRun",
      "emr-serverless:GetApplication",
      "emr-serverless:GetDashboardForJobRun",
      "emr-serverless:GetJobRun",
      "emr-serverless:ListJobRuns",
      "emr-serverless:StartJobRun",
    ]
    resources = [var.emr_application_arn, "${var.emr_application_arn}/jobruns/*"]
  }
  statement {
    sid       = "PassEmrRuntimeRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.emr_runtime.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["emr-serverless.amazonaws.com"]
    }
  }
  statement {
    sid       = "ReadRuntimeParameters"
    actions   = ["ssm:GetParameter"]
    resources = var.parameter_arns.airflow
  }
  statement {
    sid       = "ReadAirflowConnections"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = var.airflow_connection_secret_arns
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

resource "aws_iam_role_policy" "airflow" {
  role   = aws_iam_role.airflow.id
  policy = data.aws_iam_policy_document.airflow.json
}
