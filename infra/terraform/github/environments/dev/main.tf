locals {
  aws_region     = "ap-southeast-1"
  environment    = "dev"
  github_owner   = "ToGiaBaoKDL"
  repository     = "mini-lakehouse"
  aws_state_key  = "lakehouse/aws/dev/terraform.tfstate"
  tail_state_key = "lakehouse/tailscale/dev/terraform.tfstate"
}

data "terraform_remote_state" "aws" {
  backend = "s3"
  config = {
    bucket  = var.state_bucket
    key     = local.aws_state_key
    region  = local.aws_region
    encrypt = true
  }
}

data "terraform_remote_state" "tailscale" {
  backend = "s3"
  config = {
    bucket  = var.state_bucket
    key     = local.tail_state_key
    region  = local.aws_region
    encrypt = true
  }
}

data "github_repository" "this" {
  full_name = "${local.github_owner}/${local.repository}"
}

data "github_user" "owner" {
  username = local.github_owner
}

resource "github_repository_environment" "dev" {
  repository          = data.github_repository.this.name
  environment         = local.environment
  can_admins_bypass   = false
  prevent_self_review = false

  reviewers {
    users = [data.github_user.owner.id]
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "main" {
  repository     = data.github_repository.this.name
  environment    = github_repository_environment.dev.environment
  branch_pattern = "main"
}

locals {
  environment_variables = {
    AWS_LIGHTDASH_DEPLOYER_ROLE_ARN = data.terraform_remote_state.aws.outputs.github_ci_role_arns.lightdash_deployer
    LIGHTDASH_CI_SECRET_ID          = data.terraform_remote_state.aws.outputs.lightdash_ci_secret_id
    LIGHTDASH_URL                   = "http://tgbao-dev-services:8081"
    TAILSCALE_AUDIENCE              = data.terraform_remote_state.tailscale.outputs.github_deployer_audience
    TAILSCALE_CLIENT_ID             = data.terraform_remote_state.tailscale.outputs.github_deployer_client_id
  }
  repository_variables = {
    AWS_EMR_PUBLISHER_ROLE_ARN   = data.terraform_remote_state.aws.outputs.github_ci_role_arns.emr_publisher
    AWS_IMAGE_PUBLISHER_ROLE_ARN = data.terraform_remote_state.aws.outputs.github_ci_role_arns.image_publisher
    EMR_ARTIFACTS_URI            = data.terraform_remote_state.aws.outputs.emr_artifacts_uri
    EMR_CODE_PARAMETER_NAME      = data.terraform_remote_state.aws.outputs.emr_code_parameter_name
  }
}

resource "github_actions_environment_variable" "deploy" {
  for_each = local.environment_variables

  repository    = data.github_repository.this.name
  environment   = github_repository_environment.dev.environment
  variable_name = each.key
  value         = each.value
}

resource "github_actions_variable" "publish" {
  for_each = local.repository_variables

  repository    = data.github_repository.this.name
  variable_name = each.key
  value         = each.value
}

moved {
  from = github_actions_environment_variable.release
  to   = github_actions_environment_variable.deploy
}
