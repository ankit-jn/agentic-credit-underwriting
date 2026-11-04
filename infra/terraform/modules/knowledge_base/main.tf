# Module knowledge_base: S3 policy documents, vector store and Bedrock Knowledge Base used for policy retrieval with citations.
variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "docs_path" { type = string }
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

locals { embedding_model_arn = "arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/amazon.titan-embed-text-v2:0" }

resource "aws_s3_bucket" "docs" { bucket = "${var.name}-kb-docs-${data.aws_caller_identity.current.account_id}" }
resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}
resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_object" "docs" {
  for_each = fileset(var.docs_path, "*.md")
  bucket   = aws_s3_bucket.docs.id
  key      = "policies/${each.value}"
  source   = "${var.docs_path}/${each.value}"
  etag     = filemd5("${var.docs_path}/${each.value}")
}

# Vector store: S3 Vectors
resource "aws_s3vectors_vector_bucket" "this" { vector_bucket_name = "${var.name}-vectors" }
resource "aws_s3vectors_index" "this" {
  index_name         = "policy-chunks"
  vector_bucket_name = aws_s3vectors_vector_bucket.this.vector_bucket_name
  data_type          = "float32"
  dimension          = 1024
  distance_metric    = "cosine"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}
data "aws_iam_policy_document" "kb" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.docs.arn, "${aws_s3_bucket.docs.arn}/*"]
  }
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_model_arn]
  }
  statement {
    actions   = ["s3vectors:GetIndex", "s3vectors:QueryVectors", "s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:ListVectors", "s3vectors:DeleteVectors"]
    resources = [aws_s3vectors_index.this.index_arn]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}
resource "aws_iam_role" "kb" {
  name               = "${var.name}-kb"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "kb" {
  role   = aws_iam_role.kb.id
  policy = data.aws_iam_policy_document.kb.json
}

resource "aws_bedrockagent_knowledge_base" "this" {
  name     = "${var.name}-policies"
  role_arn = aws_iam_role.kb.arn
  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration { embedding_model_arn = local.embedding_model_arn }
  }
  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration { index_arn = aws_s3vectors_index.this.index_arn }
  }
  depends_on = [aws_iam_role_policy.kb]
}
resource "aws_bedrockagent_data_source" "s3" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.this.id
  name              = "credit-policy-docs"
  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.docs.arn
      inclusion_prefixes = ["policies/"]
    }
  }
  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 15
      }
    }
  }
}
output "knowledge_base_id" { value = aws_bedrockagent_knowledge_base.this.id }
output "knowledge_base_arn" { value = aws_bedrockagent_knowledge_base.this.arn }
output "data_source_id" { value = aws_bedrockagent_data_source.s3.data_source_id }
output "docs_bucket" { value = aws_s3_bucket.docs.id }
