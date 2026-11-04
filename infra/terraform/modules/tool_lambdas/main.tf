# Module tool_lambdas: Gateway tool Lambdas (kyc, cashflow, decision recorder, review queue) and the scheduled review monitor, with least-privilege roles and error alarms.
variable "name" { type = string }
variable "tools_path" { type = string }
variable "table_arns" { type = map(string) }
variable "table_names" { type = map(string) }
variable "kms_key_arn" { type = string }
variable "alarm_topic" { type = string }
variable "approval_state_machine_arn" {
  type        = string
  description = "ARN of the approval state machine. Passed as a computed string (not a module output) to avoid a dependency cycle through AgentCore."
}
variable "event_bus_name" { type = string }
variable "event_bus_arn" { type = string }

locals {
  tools = {
    kyc_screening = {
      tables  = ["watchlist", "applicants"]
      actions = ["dynamodb:GetItem", "dynamodb:Scan"]
      env     = { WATCHLIST_TABLE = var.table_names["watchlist"], APPLICANTS_TABLE = var.table_names["applicants"] }
    }
    cashflow_analyzer = {
      tables  = ["transactions"]
      actions = ["dynamodb:Query"]
      env     = { TRANSACTIONS_TABLE = var.table_names["transactions"] }
    }
    decision_recorder = {
      tables  = ["applications"]
      actions = ["dynamodb:PutItem"]
      env     = { APPLICATIONS_TABLE = var.table_names["applications"] }
    }
    # Gateway tool: creates review tasks and records structured reviewer feedback
    review_queue = {
      tables  = ["reviews", "feedback"]
      actions = ["dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:GetItem"]
      env = {
        REVIEWS_TABLE              = var.table_names["reviews"]
        FEEDBACK_TABLE             = var.table_names["feedback"]
        APPROVAL_STATE_MACHINE_ARN = var.approval_state_machine_arn
        EVENT_BUS_NAME             = var.event_bus_name
      }
    }
    # NOT a Gateway tool: scheduled queue-health, SLA escalation and feedback-quality metrics
    review_monitor = {
      tables  = ["reviews", "feedback"]
      actions = ["dynamodb:Query", "dynamodb:Scan", "dynamodb:UpdateItem"]
      env = {
        REVIEWS_TABLE   = var.table_names["reviews"]
        FEEDBACK_TABLE  = var.table_names["feedback"]
        ALARM_TOPIC_ARN = var.alarm_topic
      }
    }
  }
}

data "archive_file" "zip" {
  for_each    = local.tools
  type        = "zip"
  source_dir  = "${var.tools_path}/${each.key}"
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
  for_each           = local.tools
  name               = "${var.name}-${replace(each.key, "_", "-")}"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "fn" {
  for_each = local.tools
  statement {
    actions   = each.value.actions
    resources = flatten([for t in each.value.tables : [var.table_arns[t], "${var.table_arns[t]}/index/*"]])
  }
  dynamic "statement" {
    for_each = each.key == "review_queue" ? [1] : []
    content {
      actions   = ["states:StartExecution"]
      resources = [var.approval_state_machine_arn]
    }
  }
  dynamic "statement" {
    for_each = each.key == "review_queue" ? [1] : []
    content {
      # SendTaskSuccess has no resource-level permissions; the task token is the capability and is never exposed to clients.
      actions   = ["states:SendTaskSuccess"]
      resources = ["*"]
    }
  }
  dynamic "statement" {
    for_each = each.key == "review_queue" ? [1] : []
    content {
      actions   = ["events:PutEvents"]
      resources = [var.event_bus_arn]
    }
  }
  dynamic "statement" {
    for_each = each.key == "review_monitor" ? [1] : []
    content {
      actions   = ["sns:Publish"]
      resources = [var.alarm_topic]
    }
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}
resource "aws_iam_role_policy" "fn" {
  for_each = local.tools
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn[each.key].json
}
resource "aws_iam_role_policy_attachment" "logs" {
  for_each   = local.tools
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy_attachment" "xray" {
  for_each   = local.tools
  role       = aws_iam_role.fn[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/AWSXrayWriteOnlyAccess"
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.tools
  name              = "/aws/lambda/${var.name}-${replace(each.key, "_", "-")}"
  retention_in_days = 90
  kms_key_id        = var.kms_key_arn
}

resource "aws_lambda_function" "fn" {
  for_each         = local.tools
  function_name    = "${var.name}-${replace(each.key, "_", "-")}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.zip[each.key].output_path
  source_code_hash = data.archive_file.zip[each.key].output_base64sha256
  timeout          = each.key == "review_monitor" ? 60 : 15
  memory_size      = 256
  kms_key_arn      = var.kms_key_arn
  environment { variables = each.value.env }
  tracing_config { mode = "Active" }
  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each            = local.tools
  alarm_name          = "${var.name}-${each.key}-errors"
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

# every function except the scheduled monitor is exposed to AgentCore Gateway
output "function_arns" { value = { for k, f in aws_lambda_function.fn : k => f.arn if k != "review_monitor" } }
output "monitor_function_name" { value = aws_lambda_function.fn["review_monitor"].function_name }

resource "aws_cloudwatch_event_rule" "monitor" {
  name                = "${var.name}-review-monitor"
  schedule_expression = "rate(5 minutes)"
}
resource "aws_cloudwatch_event_target" "monitor" {
  rule = aws_cloudwatch_event_rule.monitor.name
  arn  = aws_lambda_function.fn["review_monitor"].arn
}
resource "aws_lambda_permission" "monitor" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["review_monitor"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.monitor.arn
}
