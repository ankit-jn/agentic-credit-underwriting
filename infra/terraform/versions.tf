# Terraform and provider version pins and remote state backend.
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.0" } # use the latest 6.x: AgentCore resources are recent
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {} # configured via -backend-config (see deploy workflow)
}

provider "aws" {
  region = var.region
  default_tags { tags = local.tags }
}
