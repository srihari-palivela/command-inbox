terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "name" {
  type        = string
  description = "Stack name, e.g. ci-prod-apexbank."
}
variable "region" { type = string }
variable "azs" { type = list(string) }
variable "cluster_node_security_group_id" {
  type        = string
  description = "The EKS node security group that runs the API and worker."
}
variable "admin_role_arns" { type = list(string) }
variable "app_role_arns" { type = list(string) }

provider "aws" {
  region = var.region
  default_tags {
    tags = { Stack = var.name, ManagedBy = "terraform", Product = "command-inbox" }
  }
}

module "keys" {
  source          = "../modules/keys"
  name            = var.name
  admin_role_arns = var.admin_role_arns
  app_role_arns   = var.app_role_arns
}

module "network" {
  source = "../modules/network"
  name   = var.name
  azs    = var.azs
}

module "database" {
  source                    = "../modules/database"
  name                      = var.name
  vpc_id                    = module.network.vpc_id
  subnet_ids                = module.network.private_subnet_ids
  client_security_group_ids = [var.cluster_node_security_group_id]
  kms_key_arn               = module.keys.key_arns["database"]
}

module "storage" {
  source            = "../modules/storage"
  name              = var.name
  kms_key_arn       = module.keys.key_arns["storage"]
  audit_kms_key_arn = module.keys.key_arns["audit"]
}

# The application's settings the Helm chart's existingSecret is filled from (External Secrets Operator).
# Values are set out of band (never in Terraform state); this only creates the container and its key.
resource "aws_secretsmanager_secret" "app" {
  name       = "${var.name}/app-env"
  kms_key_id = module.keys.key_arns["data"]
}

output "database_endpoint" { value = module.database.endpoint }
output "app_secret_arn" { value = aws_secretsmanager_secret.app.arn }
output "kms_data_key_arn" { value = module.keys.key_arns["data"] }
output "audit_bucket" { value = module.storage.audit_bucket }
