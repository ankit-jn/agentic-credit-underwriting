# Module bff: HTTP API (JWT authorizer) + Lambda that serves the SPA: agent actions, application / review lists, review timeline.
variable "name" { type = string }
variable "bff_path" { type = string }
variable "runtime_arn" { type = string }
variable "applications_table" { type = string }
variable "applications_arn" { type = string }
variable "reviews_table" { type = string }
variable "reviews_arn" { type = string }
variable "issuer" { type = string }
variable "spa_client_id" { type = string }
variable "allowed_origins" { type = list(string) }
variable "alarm_topic" { type = string }
variable "kms_key_arn" { type = string }
data "aws_region" "current" {}

data "archive_file" "zip" {
  type        = "zip"
  source_dir  = var.bff_path
  output_path = "${path.module}/.build/bff.zip"
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
  name               = "${var.name}-bff"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "fn" {
  role = aws_iam_role.fn.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:Scan"], Resource = var.applications_arn },
    { Effect = "Allow", Action = ["dynamodb:Query"], Resource = [var.reviews_arn, "${var.reviews_arn}/index/status-sla"] },
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.kms_key_arn }
  ] })
}
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.fn.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_lambda_function" "bff" {
  function_name    = "${var.name}-bff"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.zip.output_path
  source_code_hash = data.archive_file.zip.output_base64sha256
  timeout          = 29
  memory_size      = 256
  environment {
    variables = { RUNTIME_ARN = var.runtime_arn, APPLICATIONS_TABLE = var.applications_table, REVIEWS_TABLE = var.reviews_table }
  }
  tracing_config { mode = "Active" }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name}-bff"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type", "x-session-id"]
    max_age       = 600
  }
}
resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    issuer   = var.issuer
    audience = [var.spa_client_id]
  }
}
resource "aws_apigatewayv2_integration" "bff" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.bff.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}
resource "aws_apigatewayv2_route" "routes" {
  for_each             = toset(["POST /agent", "GET /applications", "GET /reviews", "GET /reviews/{application_id}/timeline"])
  api_id               = aws_apigatewayv2_api.http.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.bff.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt.id
  authorization_scopes = ["underwriting/agent.invoke"]
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigw/${var.name}-bff"
  retention_in_days = 90
}
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 25
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format          = jsonencode({ requestId = "$context.requestId", route = "$context.routeKey", status = "$context.status", latency = "$context.responseLatency", sub = "$context.authorizer.claims.sub" })
  }
}
resource "aws_lambda_permission" "apigw" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bff.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name}-bff-5xx"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.http.id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic]
}
output "api_url" { value = aws_apigatewayv2_api.http.api_endpoint }
output "api_id" { value = aws_apigatewayv2_api.http.id }
