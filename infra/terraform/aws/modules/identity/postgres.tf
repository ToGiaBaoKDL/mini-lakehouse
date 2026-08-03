resource "aws_iam_role" "metadata_postgres" {
  name               = "${var.name_prefix}-metadata-postgres"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["metadata_postgres"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "metadata_postgres" {
  statement {
    sid       = "ReadMetadataDatabaseSecrets"
    actions   = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = var.metadata_postgres_secret_arns
  }
}

resource "aws_iam_role_policy" "metadata_postgres" {
  role   = aws_iam_role.metadata_postgres.id
  policy = data.aws_iam_policy_document.metadata_postgres.json
}
