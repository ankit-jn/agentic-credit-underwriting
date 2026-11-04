# Dashboards + alarms over the custom UWAgent namespace (CloudWatch EMF) and AWS service metrics.
variable "name" { type = string }
variable "region" { type = string }
variable "alarm_topic" { type = string }
variable "bff_api_id" { type = string }
variable "tool_lambda_names" { type = list(string) }
variable "state_machine_arn" { type = string }

locals {
  ns = "UWAgent"
  widgets = [
    { type = "text", x = 0, y = 0, width = 24, height = 1, properties = { markdown = "## UW-Agent: outcomes and HITL  |  traces: CloudWatch > GenAI Observability / X-Ray  |  logs: Logs Insights (JSON, filter by application_id)" } },
    { type = "metric", x = 0, y = 1, width = 8, height = 6, properties = { title = "Routing tier mix", region = var.region, stat = "Sum", period = 3600, view = "timeSeries", stacked = true,
      metrics = [for t in ["AUTO", "STANDARD", "SENIOR", "COMMITTEE"] : [local.ns, "RoutingTier", "Service", "agent", "Tier", t]] } },
    { type = "metric", x = 8, y = 1, width = 8, height = 6, properties = { title = "Time to recommendation (p50 / p95, ms)", region = var.region, period = 300, view = "timeSeries",
      metrics = [[local.ns, "TimeToRecommendationMs", "Service", "agent", { stat = "p50" }], [local.ns, "TimeToRecommendationMs", "Service", "agent", { stat = "p95" }]] } },
    { type = "metric", x = 16, y = 1, width = 8, height = 6, properties = { title = "Confidence (avg) by tier", region = var.region, stat = "Average", period = 3600, view = "timeSeries",
      metrics = [for t in ["AUTO", "STANDARD", "SENIOR"] : [local.ns, "Confidence", "Service", "agent", "Tier", t]] } },
    { type = "metric", x = 0, y = 7, width = 8, height = 6, properties = { title = "HITL queue depth", region = var.region, stat = "Maximum", period = 300, view = "timeSeries", stacked = true,
      metrics = [for t in ["STANDARD", "SENIOR", "COMMITTEE", "QA"] : [local.ns, "ReviewQueueDepth", "Service", "review", "Tier", t]] } },
    { type = "metric", x = 8, y = 7, width = 8, height = 6, properties = { title = "Oldest pending review (min) / SLA breaches", region = var.region, stat = "Maximum", period = 300, view = "timeSeries",
      metrics = [[local.ns, "ReviewOldestAgeMinutes", "Service", "review"], [local.ns, "ReviewSlaBreaches", "Service", "review", { yAxis = "right" }]] } },
    { type = "metric", x = 16, y = 7, width = 8, height = 6, properties = { title = "Human agreement / override rate (7d window)", region = var.region, stat = "Average", period = 3600, view = "timeSeries",
      metrics = [[local.ns, "AgreementRate", "Service", "review", "ConfidenceBucket", "all"], [local.ns, "OverrideRate", "Service", "review", "ConfidenceBucket", "all"],
      [local.ns, "AgreementRate", "Service", "review", "ConfidenceBucket", "low"]] } },
    { type = "metric", x = 0, y = 13, width = 8, height = 6, properties = { title = "Gateway tool latency (avg ms)", region = var.region, stat = "Average", period = 300, view = "timeSeries",
      metrics = [for t in ["screen_entity", "analyze_cashflow", "getCreditScore", "record_decision", "record_review_event"] : [local.ns, "GatewayToolLatencyMs", "Service", "agent", "Tool", t]] } },
    { type = "metric", x = 8, y = 13, width = 8, height = 6, properties = { title = "Errors: tools, nodes, four-eyes blocks", region = var.region, stat = "Sum", period = 300, view = "timeSeries",
      metrics = [[local.ns, "GatewayToolErrors", "Service", "agent"], [local.ns, "NodeErrors", "Service", "agent"], [local.ns, "FourEyesBlocked", "Service", "agent"], [local.ns, "ToolErrors", "Service", "tool"], [local.ns, "ComplianceReferrals", "Service", "agent"]] } },
    { type = "metric", x = 16, y = 13, width = 8, height = 6, properties = { title = "LLM tokens and guardrail interventions", region = var.region, stat = "Sum", period = 3600, view = "timeSeries",
      metrics = [[local.ns, "LlmInputTokens", "Service", "agent"], [local.ns, "LlmOutputTokens", "Service", "agent"], [local.ns, "GuardrailInterventions", "Service", "agent", { yAxis = "right" }]] } },
    { type = "metric", x = 0, y = 19, width = 12, height = 6, properties = { title = "Decisions by outcome", region = var.region, stat = "Sum", period = 3600, view = "timeSeries", stacked = true,
      metrics = [for d in ["APPROVED", "DECLINED", "REFERRED"] : [local.ns, "Decisions", "Service", "agent", "Decision", d]] } },
    { type = "text", x = 0, y = 25, width = 24, height = 1, properties = { markdown = "## Durable approval workflow (Step Functions + EventBridge): reminders, escalations, expiry" } },
    { type = "metric", x = 0, y = 26, width = 8, height = 6, properties = { title = "Approval workflow executions", region = var.region, stat = "Sum", period = 300, view = "timeSeries",
      metrics = [for m in ["ExecutionsStarted", "ExecutionsSucceeded", "ExecutionsFailed", "ExecutionsTimedOut", "ExecutionsAborted"] : ["AWS/States", m, "StateMachineArn", var.state_machine_arn]] } },
    { type = "metric", x = 8, y = 26, width = 8, height = 6, properties = { title = "Reminders, escalations (L1/L2), expirations", region = var.region, stat = "Sum", period = 900, view = "timeSeries",
      metrics = [[local.ns, "ReviewRemindersSent", "Service", "review", { stat = "Sum" }], [local.ns, "ReviewEscalations", "Service", "review", "Level", "1"], [local.ns, "ReviewEscalations", "Service", "review", "Level", "2"],
      [local.ns, "ReviewExpirations", "Service", "review", { yAxis = "right" }]] } },
    { type = "metric", x = 16, y = 26, width = 8, height = 6, properties = { title = "Escalated open / tasks without workflow / late decisions", region = var.region, stat = "Maximum", period = 300, view = "timeSeries",
      metrics = [[local.ns, "ReviewEscalatedOpen", "Service", "review"], [local.ns, "ReviewTasksWithoutWorkflow", "Service", "review"], [local.ns, "ReviewLateDecisions", "Service", "review", { stat = "Sum" }]] } },
    { type = "metric", x = 0, y = 32, width = 12, height = 6, properties = { title = "Time to human decision (avg / p95, min)", region = var.region, period = 3600, view = "timeSeries",
      metrics = [for t in ["STANDARD", "SENIOR", "COMMITTEE"] : [local.ns, "ReviewTimeToDecisionMinutes", "Service", "review", "Tier", t, { stat = "Average" }]] } },
    { type = "metric", x = 12, y = 19, width = 12, height = 6, properties = { title = "BFF API: 4xx / 5xx / p95 latency", region = var.region, stat = "Sum", period = 300, view = "timeSeries",
      metrics = [["AWS/ApiGateway", "4xx", "ApiId", var.bff_api_id], ["AWS/ApiGateway", "5xx", "ApiId", var.bff_api_id], ["AWS/ApiGateway", "Latency", "ApiId", var.bff_api_id, { stat = "p95", yAxis = "right" }]] } },
  ]

  # name => { metric, dims, stat, op, threshold, period, evals, description }
  alarms = {
    gateway-tool-errors = { metric = "GatewayToolErrors", dims = { Service = "agent" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 3, period = 300, evals = 1, desc = "Agent cannot reach tools after retries" }
    node-errors         = { metric = "NodeErrors", dims = { Service = "agent" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 3, period = 300, evals = 1, desc = "LangGraph node failures" }
    review-sla-breach   = { metric = "ReviewSlaBreaches", dims = { Service = "review" }, stat = "Maximum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "A human review task passed its SLA" }
    review-oldest-age   = { metric = "ReviewOldestAgeMinutes", dims = { Service = "review" }, stat = "Maximum", op = "GreaterThanThreshold", threshold = 480, period = 300, evals = 3, desc = "Oldest pending review exceeds 8h" }
    override-rate-high  = { metric = "OverrideRate", dims = { Service = "review", ConfidenceBucket = "all" }, stat = "Average", op = "GreaterThanThreshold", threshold = 0.25, period = 3600, evals = 3, desc = "Humans overriding the agent too often: model drift or policy change?" }
    low-conf-agreement  = { metric = "AgreementRate", dims = { Service = "review", ConfidenceBucket = "high" }, stat = "Average", op = "LessThanThreshold", threshold = 0.85, period = 3600, evals = 3, desc = "High-confidence cases are being corrected: confidence is miscalibrated" }
    guardrail-spike     = { metric = "GuardrailInterventions", dims = { Service = "agent" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 5, period = 900, evals = 1, desc = "Bedrock Guardrail blocking repeatedly: possible prompt attack or data issue" }
    compliance-referral = { metric = "ComplianceReferrals", dims = { Service = "agent" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "Case stopped and referred to Compliance (sanctions match or screening unavailable)" }
    review-escalation-l2  = { metric = "ReviewEscalations", dims = { Service = "review", Level = "2" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "A review reached escalation level 2 (head of credit): nobody answered two tiers" }
    review-expired        = { metric = "ReviewExpirations", dims = { Service = "review" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "A review expired unanswered and was referred: staffing or routing problem" }
    review-without-workflow = { metric = "ReviewTasksWithoutWorkflow", dims = { Service = "review" }, stat = "Maximum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "Pending review has no approval workflow (start failed): only the monitor fallback is watching it" }
    review-workflow-start-errors = { metric = "WorkflowStartErrors", dims = { Service = "review" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "Could not start the approval workflow for a new review task" }
    review-callback-errors = { metric = "WorkflowCallbackErrors", dims = { Service = "review" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "A reviewer decision could not complete the workflow wait" }
    event-publish-errors  = { metric = "EventPublishErrors", dims = { Service = "review" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 1, period = 300, evals = 1, desc = "Workflow events were rejected by EventBridge: notifications and timeline may be incomplete" }
    four-eyes-blocks    = { metric = "FourEyesBlocked", dims = { Service = "agent" }, stat = "Sum", op = "GreaterThanOrEqualToThreshold", threshold = 3, period = 3600, evals = 1, desc = "Repeated attempts to approve own cases" }
  }
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name}-overview"
  dashboard_body = jsonencode({ widgets = local.widgets })
}

resource "aws_cloudwatch_metric_alarm" "custom" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.desc
  namespace           = local.ns
  metric_name         = each.value.metric
  dimensions          = each.value.dims
  statistic           = each.value.stat
  comparison_operator = each.value.op
  threshold           = each.value.threshold
  period              = each.value.period
  evaluation_periods  = each.value.evals
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
  ok_actions          = [var.alarm_topic]
}

resource "aws_cloudwatch_metric_alarm" "latency_p95" {
  alarm_name          = "${var.name}-time-to-recommendation-p95"
  alarm_description   = "p95 time to recommendation above 2 minutes"
  namespace           = local.ns
  metric_name         = "TimeToRecommendationMs"
  dimensions          = { Service = "agent" }
  extended_statistic  = "p95"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 120000
  period              = 300
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
}

# Token spend anomaly (cost / prompt-loop / abuse detector): alarm when usage leaves the learned band
resource "aws_cloudwatch_metric_alarm" "token_anomaly" {
  alarm_name          = "${var.name}-llm-output-tokens-anomaly"
  alarm_description   = "LLM output tokens outside the expected band"
  comparison_operator = "GreaterThanUpperThreshold"
  evaluation_periods  = 2
  threshold_metric_id = "band"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
  metric_query {
    id          = "tokens"
    return_data = true
    metric {
      namespace   = local.ns
      metric_name = "LlmOutputTokens"
      dimensions  = { Service = "agent" }
      stat        = "Sum"
      period      = 3600
    }
  }
  metric_query {
    id         = "band"
    expression = "ANOMALY_DETECTION_BAND(tokens, 2)"
    label      = "expected"
  }
}

# Customer-facing symptom alarm on the BFF API
resource "aws_cloudwatch_metric_alarm" "bff_latency" {
  alarm_name          = "${var.name}-bff-latency-p95"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  dimensions          = { ApiId = var.bff_api_id }
  extended_statistic  = "p95"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 25000
  period              = 300
  evaluation_periods  = 3
  alarm_description   = "BFF nearing the 30s HTTP API limit: move to the async job pattern"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
}

# Log Insights saved queries for investigations
resource "aws_cloudwatch_query_definition" "case_timeline" {
  name            = "${var.name}/case-timeline-by-application"
  log_group_names = [for n in var.tool_lambda_names : "/aws/lambda/${n}"]
  query_string    = <<-EOQ
    fields @timestamp, service, tool, msg, level, application_id, request_id
    | filter application_id = "REPLACE_ME"
    | sort @timestamp asc
  EOQ
}

output "dashboard_name" { value = aws_cloudwatch_dashboard.this.dashboard_name }
