resource "aws_iam_role" "services_deployer" {
  name               = "${var.name_prefix}-services-deployer"
  assume_role_policy = data.aws_iam_policy_document.external_runtime_trust["services_deployer"].json
  tags               = var.tags
}

data "aws_iam_policy_document" "services_deployer" {
  statement {
    sid       = "AuthenticateToEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PullServiceImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = var.container_repository_arns
  }
}

resource "aws_iam_role_policy" "services_deployer" {
  role   = aws_iam_role.services_deployer.id
  policy = data.aws_iam_policy_document.services_deployer.json
}
