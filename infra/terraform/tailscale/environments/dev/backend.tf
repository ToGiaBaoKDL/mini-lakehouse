terraform {
  backend "s3" {
    key          = "lakehouse/tailscale/dev/terraform.tfstate"
    region       = "ap-southeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
