# Inputs of the agentcore module.
variable "name" { type = string }
variable "region" { type = string }
variable "kms_key_arn" { type = string }
variable "container_uri" { type = string }
variable "ecr_repository_arn" { type = string }
variable "bedrock_model_id" { type = string }
variable "hitl_amount_threshold" { type = number }
variable "knowledge_base_id" { type = string }
variable "knowledge_base_arn" { type = string }
variable "guardrail_id" { type = string }
variable "guardrail_arn" { type = string }
variable "guardrail_version" { type = string }
variable "cognito_discovery_url" { type = string }
variable "spa_client_id" { type = string }
variable "m2m_client_id" { type = string }
variable "m2m_client_secret" {
  type      = string
  sensitive = true
}
variable "bureau_scope" { type = string }
variable "bureau_openapi_s3_uri" { type = string }
variable "bureau_openapi_bucket" { type = string }
variable "lambda_tool_arns" { type = map(string) }
variable "environment" { type = string }
variable "confidence_auto_min" {
  type    = number
  default = 0.85
}
variable "confidence_review_min" {
  type    = number
  default = 0.60
}
variable "qa_sample_rate" {
  type    = number
  default = 0.10
}
variable "enforce_four_eyes" {
  type    = bool
  default = true
}
variable "prompt_version" {
  type    = string
  default = "2026-11-v1"
}
variable "policy_version" {
  type    = string
  default = "credit-policy-v3.2"
}
variable "workflow_client_id" {
  type        = string
  description = "Cognito client of the approval workflow (system principal; may only EXPIRE pending reviews)"
}
