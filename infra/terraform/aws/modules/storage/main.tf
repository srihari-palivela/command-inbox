variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "audit_kms_key_arn" { type = string }
variable "audit_retention_years" {
  type    = number
  default = 7
}

resource "aws_s3_bucket" "attachments" {
  bucket = "${var.name}-attachments"
}

resource "aws_s3_bucket" "audit" {
  bucket              = "${var.name}-audit-exports"
  object_lock_enabled = true
}

locals {
  buckets = { attachments = aws_s3_bucket.attachments, audit = aws_s3_bucket.audit }
  keys    = { attachments = var.kms_key_arn, audit = var.audit_kms_key_arn }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = local.buckets
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.keys[each.key]
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = local.buckets
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "tls_only" {
  for_each = local.buckets
  statement {
    sid       = "TLSOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = local.buckets
  bucket   = each.value.id
  policy   = data.aws_iam_policy_document.tls_only[each.key].json
}

# Audit exports are write-once: nobody, including the root account, can delete or overwrite them
# before the retention period ends.
resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      mode  = "COMPLIANCE"
      years = var.audit_retention_years
    }
  }
}

output "attachments_bucket" { value = aws_s3_bucket.attachments.bucket }
output "audit_bucket" { value = aws_s3_bucket.audit.bucket }
