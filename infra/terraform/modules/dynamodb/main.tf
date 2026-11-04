# Module dynamodb: decisions, audit, reviews (+ status-sla index) and feedback tables (on-demand, KMS, point-in-time recovery).
variable "name" { type = string }
variable "kms_key_arn" { type = string }
locals {
  tables = {
    applicants   = { hash = "applicant_id", range = null, gsi = [] }
    transactions = { hash = "applicant_id", range = "txn_ts", gsi = [] }
    watchlist    = { hash = "name_key", range = null, gsi = [] }
    applications = { hash = "application_id", range = "sk", gsi = [] }
    # human review queue; GSI lets the UI/monitor read PENDING tasks soonest-SLA-first
    reviews = { hash = "application_id", range = "sk", gsi = [{ name = "status-sla", hash = "status", range = "sla_due" }] }
    # immutable reviewer feedback (labels for evals, calibration and audit)
    feedback = { hash = "application_id", range = "feedback_ts", gsi = [] }
  }
}
resource "aws_dynamodb_table" "this" {
  for_each     = local.tables
  name         = "${var.name}-${each.key}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value.hash
  range_key    = each.value.range
  dynamic "attribute" {
    for_each = toset(compact(concat([each.value.hash, each.value.range], [for g in each.value.gsi : g.hash], [for g in each.value.gsi : g.range])))
    content {
      name = attribute.value
      type = "S"
    }
  }
  dynamic "global_secondary_index" {
    for_each = each.value.gsi
    content {
      name            = global_secondary_index.value.name
      hash_key        = global_secondary_index.value.hash
      range_key       = global_secondary_index.value.range
      projection_type = "ALL"
    }
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
  deletion_protection_enabled = true
}
output "table_arns" { value = { for k, t in aws_dynamodb_table.this : k => t.arn } }
output "table_names" { value = { for k, t in aws_dynamodb_table.this : k => t.name } }
