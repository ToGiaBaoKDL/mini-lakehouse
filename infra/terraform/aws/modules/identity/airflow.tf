resource "aws_iam_role" "airflow" {
  name               = "${var.name_prefix}-airflow"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["airflow"].json
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
    sid       = "ReadAirflowSecrets"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = var.airflow_secret_arns
  }
}

resource "aws_iam_role_policy" "airflow" {
  role   = aws_iam_role.airflow.id
  policy = data.aws_iam_policy_document.airflow.json
}
