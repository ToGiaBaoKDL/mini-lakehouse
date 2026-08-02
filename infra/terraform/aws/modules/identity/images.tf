resource "aws_iam_role" "image_publisher" {
  name               = "${var.name_prefix}-image-publisher"
  assume_role_policy = data.aws_iam_policy_document.operator_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "image_publisher" {
  statement {
    sid       = "AuthenticateToEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PublishAndPullServiceImages"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = var.container_repository_arns
  }
}

resource "aws_iam_role_policy" "image_publisher" {
  role   = aws_iam_role.image_publisher.id
  policy = data.aws_iam_policy_document.image_publisher.json
}
