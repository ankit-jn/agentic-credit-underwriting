# Durable human-approval workflow: AWS Step Functions (Standard) owns the *waiting* (timers, reminders,
# escalation ladder, expiry); Amazon EventBridge carries the lifecycle events to people and systems.
# LangGraph still owns the reasoning and the paused case; AgentCore Memory still holds its checkpoint.
variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "functions_path" { type = string }
variable "reviews_table" { type = string }
variable "reviews_arn" { type = string }
variable "runtime_arn" { type = string }
variable "token_url" { type = string }
variable "workflow_secret_arn" { type = string }
variable "alarm_topic" { type = string }
variable "time_scale" {
  type        = number
  default     = 1
  description = "Multiplier applied to every wait (1 = real time; 0.01 compresses 24h to ~15 min for dev smoke tests)"
}
variable "expiry_grace_hours" {
  type    = number
  default = 24
}
variable "notify_email" {
  type    = string
  default = ""
}
variable "escalation_email" {
  type    = string
  default = ""
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
  fns = {
    approval_actions = {
      timeout = 30
      env     = { REVIEWS_TABLE = var.reviews_table, EVENT_BUS_NAME = aws_cloudwatch_event_bus.this.name, WORKFLOW_TIME_SCALE = tostring(var.time_scale), EXPIRY_GRACE_HOURS = tostring(var.expiry_grace_hours) }
    }
    workflow_resume = {
      timeout = 60
      env = {
        REVIEWS_TABLE    = var.reviews_table
        RUNTIME_ARN      = var.runtime_arn
        TOKEN_URL        = var.token_url
        CLIENT_SECRET_ID = var.workflow_secret_arn
        TOKEN_SCOPE      = "underwriting/system.resume"
      }
    }
    event_recorder = {
      timeout = 15
      env     = { REVIEWS_TABLE = var.reviews_table }
    }
  }
}

############################ Event bus ############################
resource "aws_cloudwatch_event_bus" "this" {
  name = "${var.name}-events"
}
resource "aws_cloudwatch_event_archive" "this" {
  name             = "${var.name}-events-archive"
  event_source_arn = aws_cloudwatch_event_bus.this.arn
  retention_days   = 90
  event_pattern    = jsonencode({ source = ["uw.underwriting"] })
}

############################ Notification topics ############################
locals { topics = { reviewers = var.notify_email, escalations = var.escalation_email } }

resource "aws_sns_topic" "this" {
  for_each          = local.topics
  name              = "${var.name}-review-${each.key}"
  kms_master_key_id = var.kms_key_arn
}
data "aws_iam_policy_document" "topic" {
  for_each = local.topics
  statement {
    sid       = "AccountOwner"
    actions   = ["sns:*"]
    resources = [aws_sns_topic.this[each.key].arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account}:root"]
    }
  }
  statement {
    sid       = "EventBridgePublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.this[each.key].arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}
resource "aws_sns_topic_policy" "this" {
  for_each = local.topics
  arn      = aws_sns_topic.this[each.key].arn
  policy   = data.aws_iam_policy_document.topic[each.key].json
}
resource "aws_sns_topic_subscription" "email" {
  for_each  = { for k, v in local.topics : k => v if v != "" }
  topic_arn = aws_sns_topic.this[each.key].arn
  protocol  = "email"
  endpoint  = each.value
}

############################ Lambdas ############################
data "archive_file" "zip" {
  for_each    = local.fns
  type        = "zip"
  source_dir  = "${var.functions_path}/${each.key}"
  output_path = "${path.module}/.build/${each.key}.zip"
}
data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "fn" {
  for_each           = local.fns
  name               = "${var.name}-${replace(each.key, "_", "-")}"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
data "aws_iam_policy_document" "fn" {
  for_each = local.fns
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
  statement {
    actions   = each.key == "approval_actions" ? ["dynamodb:GetItem", "dynamodb:UpdateItem"] : each.key == "workflow_resume" ? ["dynamodb:GetItem", "dynamodb:UpdateItem"] : ["dynamodb:PutItem"]
    resources = [var.reviews_arn]
  }
  dynamic "statement" {
    for_each = each.key == "approval_actions" ? [1] : []
    content {
      actions   = ["events:PutEvents"]
      resources = [aws_cloudwatch_event_bus.this.arn]
    }
  }
  dynamic "statement" {
    for_each = each.key == "approval_actions" ? [1] : []
    content {
      # SendTaskSuccess does not support resource-level permissions; the token itself is the capability.
      actions   = ["states:SendTaskSuccess"]
      resources = ["*"]
    }
  }
  dynamic "statement" {
    for_each = each.key == "workflow_resume" ? [1] : []
    content {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [var.workflow_secret_arn]
    }
  }
}
resource "aws_iam_role_policy" "fn" {
  for_each = local.fns
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn[each.key].json
}
resource "aws_iam_role_policy_attachment" "logs" {
  for_each   = local.fns
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy_attachment" "xray" {
  for_each   = local.fns
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/AWSXrayWriteOnlyAccess"
}
resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.fns
  name              = "/aws/lambda/${var.name}-${replace(each.key, "_", "-")}"
  retention_in_days = 90
  kms_key_id        = var.kms_key_arn
}
resource "aws_lambda_function" "fn" {
  for_each         = local.fns
  function_name    = "${var.name}-${replace(each.key, "_", "-")}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.zip[each.key].output_path
  source_code_hash = data.archive_file.zip[each.key].output_base64sha256
  timeout          = each.value.timeout
  memory_size      = 256
  kms_key_arn      = var.kms_key_arn
  environment { variables = each.value.env }
  tracing_config { mode = "Active" }
  depends_on = [aws_cloudwatch_log_group.fn]
}
resource "aws_cloudwatch_metric_alarm" "fn_errors" {
  for_each            = local.fns
  alarm_name          = "${var.name}-${replace(each.key, "_", "-")}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
}

############################ State machine ############################
resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${var.name}-approval"
  retention_in_days = 90
  kms_key_id        = var.kms_key_arn
}
data "aws_iam_policy_document" "sfn_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}
resource "aws_iam_role" "sfn" {
  name               = "${var.name}-approval-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_trust.json
}
resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "Workers", Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [aws_lambda_function.fn["approval_actions"].arn, aws_lambda_function.fn["workflow_resume"].arn] },
    { Sid = "Events", Effect = "Allow", Action = ["events:PutEvents"], Resource = aws_cloudwatch_event_bus.this.arn },
    { Sid = "LogDelivery", Effect = "Allow", Action = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"], Resource = "*" },
    { Sid = "Xray", Effect = "Allow", Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], Resource = "*" },
    { Sid = "Kms", Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = var.kms_key_arn }
  ] })
}
resource "aws_sfn_state_machine" "approval" {
  name     = "${var.name}-approval"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD" # waits can last days; Express workflows are limited to 5 minutes
  definition = templatefile("${path.module}/asl.json.tftpl", {
    actions_arn = aws_lambda_function.fn["approval_actions"].arn
    resume_arn  = aws_lambda_function.fn["workflow_resume"].arn
    bus_name    = aws_cloudwatch_event_bus.this.name
  })
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false # execution data contains task tokens (capabilities): never log them
    level                  = "ERROR"
  }
  tracing_configuration { enabled = true }
  encryption_configuration {
    type                              = "CUSTOMER_MANAGED_KMS_KEY"
    kms_key_id                        = var.kms_key_arn
    kms_data_key_reuse_period_seconds = 300
  }
  depends_on = [aws_iam_role_policy.sfn]
}

resource "aws_cloudwatch_metric_alarm" "sfn" {
  for_each            = toset(["ExecutionsFailed", "ExecutionsTimedOut", "ExecutionsAborted"])
  alarm_name          = "${var.name}-approval-${lower(each.key)}"
  alarm_description   = "Approval workflow ${each.key}: a review task may be waiting with no timers - see runbook"
  namespace           = "AWS/States"
  metric_name         = each.key
  dimensions          = { StateMachineArn = aws_sfn_state_machine.approval.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
}

############################ Event routing ############################
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-events-dlq"
  sqs_managed_sse_enabled   = true
  message_retention_seconds = 1209600
}
data "aws_iam_policy_document" "dlq" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}
resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}
resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${var.name}-events-dlq-not-empty"
  alarm_description   = "EventBridge could not deliver a workflow event to a target"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
}

locals {
  rules = {
    reviewer-notify = {
      types = ["ReviewRequested", "ReviewReminder"]
      topic = "reviewers"
    }
    escalation-notify = {
      types = ["ReviewEscalated", "ReviewExpired", "ReviewExpireFailed", "ReviewWorkflowFailed", "ComplianceReferred"]
      topic = "escalations"
    }
  }
}
resource "aws_cloudwatch_event_rule" "notify" {
  for_each       = local.rules
  name           = "${var.name}-${each.key}"
  event_bus_name = aws_cloudwatch_event_bus.this.name
  event_pattern  = jsonencode({ source = ["uw.underwriting"], "detail-type" = each.value.types })
}
resource "aws_cloudwatch_event_target" "notify" {
  for_each       = local.rules
  rule           = aws_cloudwatch_event_rule.notify[each.key].name
  event_bus_name = aws_cloudwatch_event_bus.this.name
  arn            = aws_sns_topic.this[each.value.topic].arn
  dead_letter_config { arn = aws_sqs_queue.dlq.arn }
  input_transformer {
    input_paths    = { type = "$.detail-type", app = "$.detail.application_id", at = "$.time" }
    input_template = "\"[UW-Agent] <type>: application <app> at <at>. Open the review queue / timeline for details.\""
  }
}

resource "aws_cloudwatch_event_rule" "timeline" {
  name           = "${var.name}-timeline-recorder"
  event_bus_name = aws_cloudwatch_event_bus.this.name
  event_pattern  = jsonencode({ source = ["uw.underwriting"] })
}
resource "aws_cloudwatch_event_target" "timeline" {
  rule           = aws_cloudwatch_event_rule.timeline.name
  event_bus_name = aws_cloudwatch_event_bus.this.name
  arn            = aws_lambda_function.fn["event_recorder"].arn
  dead_letter_config { arn = aws_sqs_queue.dlq.arn }
  retry_policy {
    maximum_retry_attempts       = 10
    maximum_event_age_in_seconds = 3600
  }
}
resource "aws_lambda_permission" "timeline" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["event_recorder"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.timeline.arn
}

# Every event is also searchable in CloudWatch Logs (Logs Insights) in addition to the 90-day replayable archive
resource "aws_cloudwatch_log_group" "events" {
  name              = "/aws/events/${var.name}"
  retention_in_days = 90
}
data "aws_iam_policy_document" "events_logs" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.events.arn}:*"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "delivery.logs.amazonaws.com"]
    }
  }
}
resource "aws_cloudwatch_log_resource_policy" "events" {
  policy_name     = "${var.name}-events-to-logs"
  policy_document = data.aws_iam_policy_document.events_logs.json
}
resource "aws_cloudwatch_event_rule" "log_all" {
  name           = "${var.name}-log-all"
  event_bus_name = aws_cloudwatch_event_bus.this.name
  event_pattern  = jsonencode({ source = ["uw.underwriting"] })
}
resource "aws_cloudwatch_event_target" "log_all" {
  rule           = aws_cloudwatch_event_rule.log_all.name
  event_bus_name = aws_cloudwatch_event_bus.this.name
  arn            = aws_cloudwatch_log_group.events.arn
}

output "state_machine_arn" { value = aws_sfn_state_machine.approval.arn }
output "state_machine_name" { value = aws_sfn_state_machine.approval.name }
output "event_bus_name" { value = aws_cloudwatch_event_bus.this.name }
output "event_bus_arn" { value = aws_cloudwatch_event_bus.this.arn }
output "reviewer_topic_arn" { value = aws_sns_topic.this["reviewers"].arn }
output "escalation_topic_arn" { value = aws_sns_topic.this["escalations"].arn }
output "dlq_name" { value = aws_sqs_queue.dlq.name }
