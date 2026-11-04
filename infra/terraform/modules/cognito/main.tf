# Module cognito: user pool, groups (underwriters), scopes, SPA client (PKCE), bureau machine client and the approval-workflow machine client.
variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "callback_urls" { type = list(string) }
variable "logout_urls" { type = list(string) }
data "aws_region" "current" {}

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name}-users"
  mfa_configuration        = "OPTIONAL"
  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]
  software_token_mfa_configuration { enabled = true }
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }
  admin_create_user_config { allow_admin_create_user_only = true }
}

resource "aws_cognito_user_group" "underwriters" {
  name         = "underwriters"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Can approve/decline agent recommendations"
}
resource "aws_cognito_user_group" "analysts" {
  name         = "analysts"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Relationship managers / analysts (submit only)"
}

resource "random_id" "domain" { byte_length = 3 }
resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.name}-${random_id.domain.hex}"
  user_pool_id = aws_cognito_user_pool.this.id
}

# Resource servers = OAuth scopes
resource "aws_cognito_resource_server" "underwriting" {
  identifier   = "underwriting"
  name         = "Underwriting agent"
  user_pool_id = aws_cognito_user_pool.this.id
  scope {
    scope_name        = "agent.invoke"
    scope_description = "Invoke the underwriting agent and read-only tools"
  }
  scope {
    scope_name        = "decision.write"
    scope_description = "Record credit decisions"
  }
  scope {
    scope_name        = "system.resume"
    scope_description = "Approval workflow only: expire a pending review (refer, never decide)"
  }
}
resource "aws_cognito_resource_server" "bureau" {
  identifier   = "bureau"
  name         = "Credit bureau API"
  user_pool_id = aws_cognito_user_pool.this.id
  scope {
    scope_name        = "read"
    scope_description = "Read bureau scores"
  }
}

# SPA client: authorization-code + PKCE, no secret
resource "aws_cognito_user_pool_client" "spa" {
  name                                 = "${var.name}-spa"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile", "underwriting/agent.invoke", "underwriting/decision.write"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 8
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
  depends_on = [aws_cognito_resource_server.underwriting]
}

# Machine-to-machine client used by AgentCore Identity for the outbound bureau call
resource "aws_cognito_user_pool_client" "m2m" {
  name                                 = "${var.name}-bureau-m2m"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = ["bureau/read"]
  depends_on                           = [aws_cognito_resource_server.bureau]
}

# Machine client of the Step Functions approval workflow. One scope only: it can expire (refer) a stalled review.
resource "aws_cognito_user_pool_client" "workflow" {
  name                                 = "${var.name}-approval-workflow"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_scopes                 = ["underwriting/system.resume"]
  depends_on                           = [aws_cognito_resource_server.underwriting]
}
resource "aws_secretsmanager_secret" "workflow" {
  name       = "${var.name}/approval-workflow-client"
  kms_key_id = var.kms_key_arn
}
resource "aws_secretsmanager_secret_version" "workflow" {
  secret_id = aws_secretsmanager_secret.workflow.id
  secret_string = jsonencode({
    client_id     = aws_cognito_user_pool_client.workflow.id
    client_secret = aws_cognito_user_pool_client.workflow.client_secret
  })
}

# Secret of record (rotation / audit) in Secrets Manager
resource "aws_secretsmanager_secret" "m2m" {
  name       = "${var.name}/bureau-m2m-client"
  kms_key_id = var.kms_key_arn
}
resource "aws_secretsmanager_secret_version" "m2m" {
  secret_id = aws_secretsmanager_secret.m2m.id
  secret_string = jsonencode({
    client_id = aws_cognito_user_pool_client.m2m.id
    client_secret = aws_cognito_user_pool_client.m2m.client_secret
  })
}

locals { issuer = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this.id}" }
output "user_pool_arn" { value = aws_cognito_user_pool.this.arn }
output "issuer" { value = local.issuer }
output "discovery_url" { value = "${local.issuer}/.well-known/openid-configuration" }
output "hosted_ui_domain" { value = "${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.region}.amazoncognito.com" }
output "spa_client_id" { value = aws_cognito_user_pool_client.spa.id }
output "m2m_client_id" { value = aws_cognito_user_pool_client.m2m.id }
output "m2m_client_secret" {
  value     = aws_cognito_user_pool_client.m2m.client_secret
  sensitive = true
}
output "bureau_scope" { value = "bureau/read" }
output "workflow_client_id" { value = aws_cognito_user_pool_client.workflow.id }
output "workflow_secret_arn" { value = aws_secretsmanager_secret.workflow.arn }
output "token_url" { value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${data.aws_region.current.region}.amazoncognito.com/oauth2/token" }
