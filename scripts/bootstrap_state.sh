#!/usr/bin/env bash
# One-time: create Terraform remote-state bucket (native S3 locking, TF >= 1.10).
set -euo pipefail
REGION="${1:-us-east-1}"; ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="uw-agent-tfstate-${ACCOUNT}-${REGION}"
aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" $( [ "$REGION" != us-east-1 ] && echo --create-bucket-configuration LocationConstraint="$REGION" )
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'
echo "State bucket: $BUCKET"
