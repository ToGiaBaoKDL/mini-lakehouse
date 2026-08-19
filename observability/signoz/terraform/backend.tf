terraform {
  backend "s3" {
    key          = "lakehouse/signoz/dev/terraform.tfstate"
    region       = "ap-southeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
