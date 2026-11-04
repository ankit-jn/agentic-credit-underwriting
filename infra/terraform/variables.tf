# Root input variables (environment, region, model, thresholds, workflow timing, notification emails).
variable "project" {
  type    = string
  default = "uw-agent"
}
variable "environment" { type = string }
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "image_tag" {
  type        = string
  description = "Agent container tag in ECR"
  default     = "bootstrap"
}
variable "bedrock_model_id" {
  type        = string
  description = "Inference profile / model id for the reasoning LLM (must be enabled in your account)"
}
variable "hitl_amount_threshold" {
  type    = number
  default = 250000
}
variable "alarm_email" {
  type    = string
  default = ""
}
variable "enforce_four_eyes" {
  type        = bool
  default     = true
  description = "Submitter cannot decide their own case (set false only in dev with a single test user)"
}
variable "qa_sample_rate" {
  type        = number
  default     = 0.10
  description = "Share of auto-approved cases routed to post-hoc QA review"
}
variable "confidence_auto_min" {
  type    = number
  default = 0.85
}
variable "confidence_review_min" {
  type    = number
  default = 0.60
}
variable "workflow_time_scale" {
  type        = number
  default     = 1
  description = "Multiplier on every approval-workflow wait. 1 = real time; 0.01 compresses the 24h STANDARD SLA to ~15 minutes for dev smoke tests"
}
variable "workflow_expiry_grace_hours" {
  type        = number
  default     = 24
  description = "Final wait after the last escalation before an unanswered review is expired (referred, never decided)"
}
variable "reviewer_notify_email" {
  type        = string
  default     = ""
  description = "Distribution list for ReviewRequested / ReviewReminder notifications (escalations go to alarm_email)"
}
