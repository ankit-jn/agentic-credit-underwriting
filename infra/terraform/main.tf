# Root module: wires every module together (KMS, Cognito, DynamoDB, tool Lambdas, bureau API, Knowledge Base, Guardrail, AgentCore,
# approval workflow, BFF, frontend, observability). Computed ARNs for the approval workflow avoid a module dependency cycle.
data "aws_caller_identity" "current" {}

locals {
  name = "${var.project}-${var.environment}"
  tags = { Project = var.project, Environment = var.environment, ManagedBy = "terraform" }
}

module "kms" {
  source = "./modules/kms"
  name   = local.name
}

module "ecr" {
  source      = "./modules/ecr"
  name        = local.name
  kms_key_arn = module.kms.key_arn
}

module "frontend" {
  source      = "./modules/frontend"
  name        = local.name
  kms_key_arn = module.kms.key_arn
}

module "cognito" {
  source        = "./modules/cognito"
  name          = local.name
  kms_key_arn   = module.kms.key_arn
  callback_urls = ["http://localhost:5173/callback", "https://${module.frontend.domain_name}/callback"]
  logout_urls   = ["http://localhost:5173/", "https://${module.frontend.domain_name}/"]
}

module "dynamodb" {
  source      = "./modules/dynamodb"
  name        = local.name
  kms_key_arn = module.kms.key_arn
}

module "knowledge_base" {
  source      = "./modules/knowledge_base"
  name        = local.name
  kms_key_arn = module.kms.key_arn
  docs_path   = "${path.root}/../../kb_docs"
}

locals {
  # Computed from names, not from the approval module, to avoid a cycle: tool_lambdas -> agentcore -> approval_workflow -> tool_lambdas.
  approval_state_machine_arn = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.name}-approval"
  event_bus_name             = "${local.name}-events"
  event_bus_arn              = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:event-bus/${local.event_bus_name}"
}

module "tool_lambdas" {
  source                     = "./modules/tool_lambdas"
  name                       = local.name
  tools_path                 = "${path.root}/../../backend/tools"
  table_arns                 = module.dynamodb.table_arns
  table_names                = module.dynamodb.table_names
  kms_key_arn                = module.kms.key_arn
  alarm_topic                = aws_sns_topic.alarms.arn
  approval_state_machine_arn = local.approval_state_machine_arn
  event_bus_name             = local.event_bus_name
  event_bus_arn              = local.event_bus_arn
}

module "credit_bureau_api" {
  source        = "./modules/credit_bureau_api"
  name          = local.name
  tools_path    = "${path.root}/../../backend/tools"
  user_pool_arn = module.cognito.user_pool_arn
  kms_key_arn   = module.kms.key_arn
}

module "guardrail" {
  source = "./modules/guardrail"
  name   = local.name
}

module "agentcore" {
  source                 = "./modules/agentcore"
  name                   = local.name
  region                 = var.region
  kms_key_arn            = module.kms.key_arn
  container_uri          = "${module.ecr.repository_url}:${var.image_tag}"
  ecr_repository_arn     = module.ecr.repository_arn
  bedrock_model_id       = var.bedrock_model_id
  hitl_amount_threshold  = var.hitl_amount_threshold
  knowledge_base_id      = module.knowledge_base.knowledge_base_id
  knowledge_base_arn     = module.knowledge_base.knowledge_base_arn
  guardrail_id           = module.guardrail.guardrail_id
  guardrail_arn          = module.guardrail.guardrail_arn
  guardrail_version      = module.guardrail.guardrail_version
  environment            = var.environment
  enforce_four_eyes      = var.enforce_four_eyes
  workflow_client_id     = module.cognito.workflow_client_id
  qa_sample_rate         = var.qa_sample_rate
  confidence_auto_min    = var.confidence_auto_min
  confidence_review_min  = var.confidence_review_min
  cognito_discovery_url  = module.cognito.discovery_url
  spa_client_id          = module.cognito.spa_client_id
  m2m_client_id          = module.cognito.m2m_client_id
  m2m_client_secret      = module.cognito.m2m_client_secret
  bureau_scope           = module.cognito.bureau_scope
  bureau_openapi_s3_uri  = module.credit_bureau_api.openapi_s3_uri
  bureau_openapi_bucket  = module.credit_bureau_api.openapi_bucket_arn
  lambda_tool_arns       = module.tool_lambdas.function_arns
}

module "approval_workflow" {
  source              = "./modules/approval_workflow"
  name                = local.name
  kms_key_arn         = module.kms.key_arn
  functions_path      = "${path.root}/../../backend/tools"
  reviews_table       = module.dynamodb.table_names["reviews"]
  reviews_arn         = module.dynamodb.table_arns["reviews"]
  runtime_arn         = module.agentcore.runtime_arn
  token_url           = module.cognito.token_url
  workflow_secret_arn = module.cognito.workflow_secret_arn
  alarm_topic         = aws_sns_topic.alarms.arn
  time_scale          = var.workflow_time_scale
  expiry_grace_hours  = var.workflow_expiry_grace_hours
  notify_email        = var.reviewer_notify_email
  escalation_email    = var.alarm_email
}

module "bff" {
  source              = "./modules/bff"
  name                = local.name
  bff_path            = "${path.root}/../../backend/bff"
  runtime_arn         = module.agentcore.runtime_arn
  applications_table  = module.dynamodb.table_names["applications"]
  applications_arn    = module.dynamodb.table_arns["applications"]
  reviews_table       = module.dynamodb.table_names["reviews"]
  reviews_arn         = module.dynamodb.table_arns["reviews"]
  issuer              = module.cognito.issuer
  spa_client_id       = module.cognito.spa_client_id
  allowed_origins     = ["http://localhost:5173", "https://${module.frontend.domain_name}"]
  alarm_topic         = aws_sns_topic.alarms.arn
  kms_key_arn         = module.kms.key_arn
}

module "observability" {
  source            = "./modules/observability"
  name              = local.name
  region            = var.region
  alarm_topic       = aws_sns_topic.alarms.arn
  bff_api_id        = module.bff.api_id
  tool_lambda_names = [for k, a in module.tool_lambdas.function_arns : "${local.name}-${replace(k, "_", "-")}"]
  state_machine_arn = module.approval_workflow.state_machine_arn
}

resource "aws_sns_topic" "alarms" {
  name              = "${local.name}-alarms"
  kms_master_key_id = module.kms.key_id
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}
