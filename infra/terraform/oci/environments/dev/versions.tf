terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 8.23.0"
    }
  }
}

provider "oci" {
  region = var.region
}
