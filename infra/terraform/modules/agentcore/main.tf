# NOTE: AgentCore resources are new in the AWS provider. Block/argument names follow provider >= 6.x
# at time of writing; run `terraform validate` and adjust to your provider version if needed.
data "aws_caller_identity" "current" {}
locals {
  account = data.aws_caller_identity.current.account_id
  # AgentCore resource names: letters, digits, underscore
  id = replace(var.name, "-", "_")
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

############################ Memory ############################
resource "aws_iam_role" "memory" {
  name               = "${var.name}-agentcore-memory"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "memory" {
  role = aws_iam_role.memory.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], Resource = "*" }
  ] })
}
resource "aws_bedrockagentcore_memory" "this" {
  name                      = "${local.id}_memory"
  event_expiry_duration     = 90
  memory_execution_role_arn = aws_iam_role.memory.arn
  encryption_key_arn        = var.kms_key_arn
}
resource "aws_bedrockagentcore_memory_strategy" "semantic" {
  name       = "applicant_facts"
  memory_id  = aws_bedrockagentcore_memory.this.id
  type       = "SEMANTIC"
  namespaces = ["/applicants/{actorId}/facts/"]
}
resource "aws_bedrockagentcore_memory_strategy" "summary" {
  name       = "review_summaries"
  memory_id  = aws_bedrockagentcore_memory.this.id
  type       = "SUMMARIZATION"
  namespaces = ["/applicants/{actorId}/{sessionId}/summary/"]
}

############################ Identity ############################
# Outbound: OAuth2 client-credentials provider (Cognito M2M) held in AgentCore's token vault.
resource "aws_bedrockagentcore_oauth2_credential_provider" "bureau" {
  name                       = "${var.name}-bureau-oauth"
  credential_provider_vendor = "CustomOauth2"
  oauth2_provider_config {
    custom_oauth2_provider_config {
      client_id     = var.m2m_client_id
      client_secret = var.m2m_client_secret
      oauth_discovery { discovery_url = var.cognito_discovery_url }
    }
  }
}
# Workload identity for the agent (lets Runtime/Gateway mint workload access tokens)
resource "aws_bedrockagentcore_workload_identity" "agent" {
  name = "${local.id}_agent"
}

############################ Policy ############################
resource "aws_bedrockagentcore_policy_engine" "this" {
  name        = "${local.id}_policy"
  description = "Cedar guardrails for underwriting tools (policies applied by scripts/apply_policies.py)"
}

############################ Gateway ############################
resource "aws_iam_role" "gateway" {
  name               = "${var.name}-agentcore-gateway"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "gateway" {
  role = aws_iam_role.gateway.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = values(var.lambda_tool_arns) },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${var.bureau_openapi_bucket}/*" },
    { Effect = "Allow", Action = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetResourceOauth2Token"], Resource = "*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = "arn:aws:secretsmanager:${var.region}:${local.account}:secret:bedrock-agentcore-identity!*" },
    { Effect = "Allow", Action = ["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions", "bedrock-agentcore:GetPolicyEngine"], Resource = "*" },
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.kms_key_arn }
  ] })
}

resource "aws_bedrockagentcore_gateway" "this" {
  name            = "${replace(var.name, "_", "-")}-gw"
  role_arn        = aws_iam_role.gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = var.cognito_discovery_url
      allowed_clients = [var.spa_client_id, var.workflow_client_id]
    }
  }
  protocol_configuration {
    mcp { search_type = "SEMANTIC" }
  }
  policy_engine_configuration {
    arn  = aws_bedrockagentcore_policy_engine.this.policy_engine_arn
    mode = "ENFORCE"
  }
}

# --- Lambda targets (tool schemas) ---
locals {
  lambda_targets = {
    "kyc-screening" = {
      lambda = "kyc_screening"
      tool   = "screen_entity"
      desc   = "Check KYC status and screen the legal entity against sanctions/watchlists."
      props  = { applicant_id = "string", legal_name = "string" }
      req    = ["applicant_id", "legal_name"]
    }
    "cashflow-analyzer" = {
      lambda = "cashflow_analyzer"
      tool   = "analyze_cashflow"
      desc   = "Analyse bank transactions: monthly inflow/outflow, volatility and DSCR proxy for the proposed loan."
      props  = { applicant_id = "string", requested_amount = "number", tenor_months = "integer" }
      req    = ["applicant_id", "requested_amount", "tenor_months"]
    }
    "decision-recorder" = {
      lambda = "decision_recorder"
      tool   = "record_decision"
      desc   = "Persist the final credit decision and audit record."
      props = {
        application_id = "string", applicant_id = "string", decision = "string", amount = "number",
        risk_band = "string", rationale = "string", decided_by = "string", model_id = "string",
        tier = "string", confidence = "number", trace_id = "string", prompt_version = "string", policy_version = "string"
      }
      req = ["application_id", "applicant_id", "decision", "amount"]
    }
    "review-queue" = {
      lambda = "review_queue"
      tool   = "record_review_event"
      desc   = "Create a human-review task (TASK_CREATED, starts the durable approval workflow), record structured reviewer feedback (FEEDBACK), mark an expired task (EXPIRED) or publish a compliance referral event (COMPLIANCE_REFERRAL)."
      props = {
        application_id = "string", event_type = "string", tier = "string", priority = "integer", sla_hours = "integer",
        confidence = "number", session_id = "string", round = "integer", reasons = "string", summary = "string",
        reviewer = "string", agent_recommendation = "string", human_decision = "string", reason_codes = "string",
        comment = "string", risk_band = "string"
      }
      req = ["application_id", "event_type"]
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "lambda" {
  for_each           = local.lambda_targets
  name               = each.key
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  description        = each.value.desc

  credential_provider_configuration {
    gateway_iam_role {}
  }
  target_configuration {
    mcp {
      lambda {
        lambda_arn = var.lambda_tool_arns[each.value.lambda]
        tool_schema {
          inline_payload {
            name        = each.value.tool
            description = each.value.desc
            input_schema {
              type = "object"
              dynamic "property" {
                for_each = each.value.props
                content {
                  name     = property.key
                  type     = property.value
                  required = contains(each.value.req, property.key)
                }
              }
            }
          }
        }
      }
    }
  }
}

# --- REST API target (OpenAPI) with outbound OAuth via AgentCore Identity ---
resource "aws_bedrockagentcore_gateway_target" "bureau" {
  name               = "credit-bureau-api"
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  description        = "External credit bureau REST API"
  credential_provider_configuration {
    oauth {
      provider_arn = aws_bedrockagentcore_oauth2_credential_provider.bureau.credential_provider_arn
      scopes       = [var.bureau_scope]
    }
  }
  target_configuration {
    mcp {
      open_api_schema {
        s3 { uri = var.bureau_openapi_s3_uri }
      }
    }
  }
}

############################ Runtime ############################
resource "aws_iam_role" "runtime" {
  name               = "${var.name}-agentcore-runtime"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "runtime" {
  role = aws_iam_role.runtime.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "Ecr", Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource = var.ecr_repository_arn },
    { Sid = "EcrAuth", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Sid = "Logs", Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"], Resource = "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/bedrock-agentcore/runtimes/*" },
    { Sid = "Xray", Effect = "Allow", Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets", "cloudwatch:PutMetricData"], Resource = "*" },
    { Sid = "Model", Effect = "Allow", Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail"], Resource = "*" },
    { Sid = "Kb", Effect = "Allow", Action = ["bedrock:Retrieve"], Resource = var.knowledge_base_arn },
    { Sid = "Memory", Effect = "Allow", Action = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents", "bedrock-agentcore:ListSessions", "bedrock-agentcore:DeleteEvent", "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:GetMemory"], Resource = aws_bedrockagentcore_memory.this.arn },
    { Sid = "Identity", Effect = "Allow", Action = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT"], Resource = "*" },
    { Sid = "Kms", Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = var.kms_key_arn }
  ] })
}

resource "aws_bedrockagentcore_agent_runtime" "agent" {
  agent_runtime_name = "${local.id}_agent"
  description        = "LangGraph SME credit underwriting agent"
  role_arn           = aws_iam_role.runtime.arn

  agent_runtime_artifact {
    container_configuration { container_uri = var.container_uri }
  }
  network_configuration { network_mode = "PUBLIC" }
  protocol_configuration { server_protocol = "HTTP" }

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = var.cognito_discovery_url
      allowed_clients = [var.spa_client_id, var.workflow_client_id]
    }
  }
  # propagate the end-user's JWT into the container so it can call Gateway as the user
  request_header_configuration { request_header_allowlist = ["Authorization"] }

  environment_variables = {
    AWS_REGION            = var.region
    BEDROCK_MODEL_ID      = var.bedrock_model_id
    KNOWLEDGE_BASE_ID     = var.knowledge_base_id
    MEMORY_ID             = aws_bedrockagentcore_memory.this.id
    GATEWAY_URL           = aws_bedrockagentcore_gateway.this.gateway_url
    GUARDRAIL_ID          = var.guardrail_id
    GUARDRAIL_VERSION     = var.guardrail_version
    HITL_AMOUNT_THRESHOLD = tostring(var.hitl_amount_threshold)
    CONFIDENCE_AUTO_MIN   = tostring(var.confidence_auto_min)
    CONFIDENCE_REVIEW_MIN = tostring(var.confidence_review_min)
    QA_SAMPLE_RATE        = tostring(var.qa_sample_rate)
    ENFORCE_FOUR_EYES     = tostring(var.enforce_four_eyes)
    WORKFLOW_CLIENT_ID    = var.workflow_client_id
    PROMPT_VERSION        = var.prompt_version
    POLICY_VERSION        = var.policy_version
    APP_ENV               = var.environment
    OTEL_PYTHON_DISTRO    = "aws_distro"
    OTEL_PYTHON_CONFIGURATOR = "aws_configurator"
    AGENT_OBSERVABILITY_ENABLED = "true"
  }
  depends_on = [aws_iam_role_policy.runtime, aws_bedrockagentcore_gateway_target.lambda, aws_bedrockagentcore_gateway_target.bureau]
}

resource "aws_bedrockagentcore_agent_runtime_endpoint" "live" {
  name             = "live"
  agent_runtime_id = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_id
  description      = "Stable endpoint; promote versions by updating the runtime"
}
