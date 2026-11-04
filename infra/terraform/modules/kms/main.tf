# Module kms: customer-managed key used by tables, logs, secrets, topics and the state machine.
variable "name" { type = string }
data "aws_caller_identity" "current" {}
data "aws_iam_policy_document" "key" {
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "AllowAwsServices"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey*", "kms:Encrypt"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com", "cloudwatch.amazonaws.com", "logs.amazonaws.com"]
    }
  }
}
data "aws_iam_policy_document" "key_events" {
  # EventBridge -> CMK-encrypted SNS topics (review notifications). Restricted to this account.
  statement {
    sid       = "AllowEventBridgeToEncryptedTopics"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}
data "aws_iam_policy_document" "combined" {
  source_policy_documents = [data.aws_iam_policy_document.key.json, data.aws_iam_policy_document.key_events.json]
}
resource "aws_kms_key" "this" {
  description             = "${var.name} data key"
  enable_key_rotation     = true
  deletion_window_in_days = 14
  policy                  = data.aws_iam_policy_document.combined.json
}
resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}
output "key_arn" { value = aws_kms_key.this.arn }
output "key_id" { value = aws_kms_key.this.key_id }
