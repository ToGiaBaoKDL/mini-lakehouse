terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13.0"
    }
  }
}

provider "github" {
  owner = local.github_owner
}
