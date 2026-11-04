# Module credit_bureau_api: REST API + mock Lambda standing in for the external credit bureau (OAuth-protected).
variable "name" { type = string }
variable "tools_path" { type = string }
variable "user_pool_arn" { type = string }
variable "kms_key_arn" { type = string }
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "archive_file" "zip" {
  type        = "zip"
  source_dir  = "${var.tools_path}/credit_bureau_mock"
  output_path = "${path.module}/.build/bureau.zip"
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
  name               = "${var.name}-bureau-mock"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.fn.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_lambda_function" "bureau" {
  function_name    = "${var.name}-bureau-mock"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.zip.output_path
  source_code_hash = data.archive_file.zip.output_base64sha256
  timeout          = 10
  tracing_config { mode = "Active" }
}

# ---- REST API protected by Cognito (OAuth2 client-credentials scope bureau/read) ----
resource "aws_api_gateway_rest_api" "this" {
  name = "${var.name}-credit-bureau"
  endpoint_configuration { types = ["REGIONAL"] }
}
resource "aws_api_gateway_authorizer" "cognito" {
  name          = "cognito"
  rest_api_id   = aws_api_gateway_rest_api.this.id
  type          = "COGNITO_USER_POOLS"
  provider_arns = [var.user_pool_arn]
}
resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "v1"
}
resource "aws_api_gateway_resource" "score" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "credit-score"
}
resource "aws_api_gateway_method" "get" {
  rest_api_id          = aws_api_gateway_rest_api.this.id
  resource_id          = aws_api_gateway_resource.score.id
  http_method          = "GET"
  authorization        = "COGNITO_USER_POOLS"
  authorizer_id        = aws_api_gateway_authorizer.cognito.id
  authorization_scopes = ["bureau/read"]
  request_parameters   = { "method.request.querystring.applicant_id" = true }
  request_validator_id = aws_api_gateway_request_validator.params.id
}
resource "aws_api_gateway_request_validator" "params" {
  name                        = "params"
  rest_api_id                 = aws_api_gateway_rest_api.this.id
  validate_request_parameters = true
}
resource "aws_api_gateway_integration" "lambda" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.score.id
  http_method             = aws_api_gateway_method.get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.bureau.invoke_arn
}
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowApiGw"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bureau.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/GET/v1/credit-score"
}
resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  triggers    = { redeploy = sha1(jsonencode([aws_api_gateway_method.get, aws_api_gateway_integration.lambda])) }
  lifecycle { create_before_destroy = true }
}
resource "aws_api_gateway_stage" "prod" {
  rest_api_id          = aws_api_gateway_rest_api.this.id
  deployment_id        = aws_api_gateway_deployment.this.id
  stage_name           = "prod"
  xray_tracing_enabled = true
}
resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  stage_name  = aws_api_gateway_stage.prod.stage_name
  method_path = "*/*"
  settings {
    metrics_enabled        = true
    throttling_burst_limit = 50
    throttling_rate_limit  = 25
  }
}

# ---- OpenAPI document consumed by the AgentCore Gateway target ----
resource "aws_s3_bucket" "openapi" { bucket = "${var.name}-openapi-${data.aws_caller_identity.current.account_id}" }
resource "aws_s3_bucket_public_access_block" "openapi" {
  bucket                  = aws_s3_bucket.openapi.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "openapi" {
  bucket = aws_s3_bucket.openapi.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}
resource "aws_s3_object" "openapi" {
  bucket       = aws_s3_bucket.openapi.id
  key          = "credit-bureau-openapi.json"
  content_type = "application/json"
  content = jsonencode({
    openapi = "3.0.1"
    info    = { title = "Credit Bureau API", version = "1.0" }
    servers = [{ url = aws_api_gateway_stage.prod.invoke_url }]
    paths = { "/v1/credit-score" = { get = {
      operationId = "getCreditScore"
      description = "Retrieve the bureau credit score, delinquencies, utilisation and enquiries for an applicant."
      parameters  = [{ name = "applicant_id", in = "query", required = true, schema = { type = "string" } }]
      responses = { "200" = { description = "OK", content = { "application/json" = { schema = {
        type = "object"
        properties = {
          applicant_id           = { type = "string" }
          bureau_score           = { type = "integer" }
          delinquencies_24m      = { type = "integer" }
          credit_utilisation_pct = { type = "integer" }
          enquiries_6m           = { type = "integer" }
          bureau_reference       = { type = "string" }
        }
      } } } } }
    } } }
  })
}
output "openapi_s3_uri" { value = "s3://${aws_s3_bucket.openapi.id}/${aws_s3_object.openapi.key}" }
output "openapi_bucket_arn" { value = aws_s3_bucket.openapi.arn }
output "invoke_url" { value = aws_api_gateway_stage.prod.invoke_url }
