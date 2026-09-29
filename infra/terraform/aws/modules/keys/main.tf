variable "name" { type = string }
variable "admin_role_arns" {
  type        = list(string)
  description = "Roles that administer the keys (not use them)."
}
variable "app_role_arns" {
  type        = list(string)
  description = "Roles the API and worker run as (IRSA): may encrypt and decrypt with the data key."
}

data "aws_caller_identity" "current" {}

locals {
  purposes = ["data", "database", "storage", "audit", "logs"]
}

data "aws_iam_policy_document" "key" {
  for_each = toset(local.purposes)
  statement {
    sid       = "Account"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid = "Administer"
    actions = [
      "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*",
      "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource", "kms:UntagResource",
      "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = var.admin_role_arns
    }
  }
  dynamic "statement" {
    for_each = each.key == "data" ? [1] : []
    content {
      sid       = "Use"
      actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      resources = ["*"]
      principals {
        type        = "AWS"
        identifiers = var.app_role_arns
      }
    }
  }
}

resource "aws_kms_key" "this" {
  for_each                = toset(local.purposes)
  description             = "${var.name} ${each.key}"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.key[each.key].json
}

resource "aws_kms_alias" "this" {
  for_each      = aws_kms_key.this
  name          = "alias/${var.name}-${each.key}"
  target_key_id = each.value.key_id
}

output "key_arns" {
  value = { for k, v in aws_kms_key.this : k => v.arn }
}
