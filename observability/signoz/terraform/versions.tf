terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    signoz = {
      source  = "signoz/signoz"
      version = "~> 0.1.4"
    }
  }
}
