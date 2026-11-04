# Module guardrail: Bedrock Guardrail (PII redaction, protected-attribute and prompt-attack filters) attached to the model.
variable "name" { type = string }
resource "aws_bedrock_guardrail" "this" {
  name                      = "${var.name}-guardrail"
  blocked_input_messaging   = "Request blocked by policy."
  blocked_outputs_messaging = "Response blocked by policy."
  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
  }
  sensitive_information_policy_config {
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"
    }
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
  }
  topic_policy_config {
    topics_config {
      name       = "protected-attribute-inference"
      type       = "DENY"
      definition = "Inferring or using religion, caste, ethnicity, gender or marital status of an individual to assess creditworthiness."
      examples   = ["Is this applicant likely to repay because of their religion?"]
    }
  }
}
resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
}
output "guardrail_id" { value = aws_bedrock_guardrail.this.guardrail_id }
output "guardrail_arn" { value = aws_bedrock_guardrail.this.guardrail_arn }
output "guardrail_version" { value = aws_bedrock_guardrail_version.this.version }
