# Module ecr: container repository for the agent image.
variable "name" { type = string }
variable "kms_key_arn" { type = string }
resource "aws_ecr_repository" "agent" {
  name                 = "${var.name}-agent"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}
resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "keep last 20",
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 },
    action       = { type = "expire" }
  }] })
}
output "repository_url" { value = aws_ecr_repository.agent.repository_url }
output "repository_arn" { value = aws_ecr_repository.agent.arn }
