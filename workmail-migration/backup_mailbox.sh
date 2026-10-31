#!/usr/bin/env bash
# Inventory the WorkMail organization and export every active mailbox to S3.
# Read-only on WorkMail: it does not change, reset or delete anything there.
# Needs: aws CLI v2, credentials with iam-policy.json, AWS_DEFAULT_REGION set
# to the WorkMail region.
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:?Set AWS_DEFAULT_REGION to your WorkMail region}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="workmail-backup-${ACCOUNT}-${REGION}"
ROLE_NAME="WorkMailExportRole"
KEY_ALIAS="alias/workmail-export"
OUT="inventory"
mkdir -p "$OUT"

ORG_ID=$(aws workmail list-organizations \
  --query "OrganizationSummaries[?State=='Active'].OrganizationId | [0]" --output text)
[[ "$ORG_ID" == "None" || -z "$ORG_ID" ]] && { echo "No active WorkMail org in $REGION"; exit 1; }
echo "Organization: $ORG_ID"

# --- Inventory -------------------------------------------------------------
aws workmail describe-organization --organization-id "$ORG_ID" > "$OUT/organization.json"
aws workmail list-mail-domains     --organization-id "$ORG_ID" > "$OUT/domains.json"
aws workmail list-users            --organization-id "$ORG_ID" > "$OUT/users.json"
aws workmail list-groups           --organization-id "$ORG_ID" > "$OUT/groups.json"
aws workmail list-resources        --organization-id "$ORG_ID" > "$OUT/resources.json"

USERS=$(aws workmail list-users --organization-id "$ORG_ID" \
  --query "Users[?State=='ENABLED' && UserRole=='USER'].Id" --output text)

for U in $USERS; do
  aws workmail list-aliases --organization-id "$ORG_ID" --entity-id "$U" > "$OUT/aliases-$U.json"
  aws workmail get-mailbox-details --organization-id "$ORG_ID" --user-id "$U" > "$OUT/mailbox-$U.json"
done
echo "Inventory written to $OUT/"

# --- Backup bucket ---------------------------------------------------------
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET"
  else
    aws s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

# --- KMS key (required by mailbox export) ----------------------------------
KEY_ID=$(aws kms list-aliases --query "Aliases[?AliasName=='$KEY_ALIAS'].TargetKeyId | [0]" --output text)
if [[ "$KEY_ID" == "None" || -z "$KEY_ID" ]]; then
  KEY_ID=$(aws kms create-key --description "WorkMail mailbox export" --query KeyMetadata.KeyId --output text)
  aws kms create-alias --alias-name "$KEY_ALIAS" --target-key-id "$KEY_ID"
fi
KEY_ARN=$(aws kms describe-key --key-id "$KEY_ID" --query KeyMetadata.Arn --output text)

# --- Role WorkMail assumes to write the export -----------------------------
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Principal\": {\"Service\": \"export.workmail.amazonaws.com\"},
      \"Action\": \"sts:AssumeRole\",
      \"Condition\": {\"StringEquals\": {\"sts:ExternalId\": \"$ORG_ID\"}}
    }]}" >/dev/null
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name export --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {\"Effect\": \"Allow\",
       \"Action\": [\"s3:AbortMultipartUpload\", \"s3:PutObject\", \"s3:GetBucketPolicyStatus\"],
       \"Resource\": [\"arn:aws:s3:::$BUCKET\", \"arn:aws:s3:::$BUCKET/*\"]},
      {\"Effect\": \"Allow\",
       \"Action\": [\"kms:Decrypt\", \"kms:GenerateDataKey\"],
       \"Resource\": \"$KEY_ARN\"}
    ]}"
  echo "Created $ROLE_NAME; waiting for IAM to propagate..."
  sleep 15
fi
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)

# --- Export each mailbox ---------------------------------------------------
for U in $USERS; do
  JOB=$(aws workmail start-mailbox-export-job \
    --organization-id "$ORG_ID" --entity-id "$U" \
    --role-arn "$ROLE_ARN" --kms-key-arn "$KEY_ARN" \
    --s3-bucket-name "$BUCKET" --s3-prefix "exports/$U" \
    --client-token "$(date +%s)-$U" --query JobId --output text)
  echo "Export job $JOB started for $U"
  while :; do
    STATE=$(aws workmail describe-mailbox-export-job --organization-id "$ORG_ID" \
      --job-id "$JOB" --query "[State, EstimatedProgress]" --output text)
    echo "  $STATE"
    [[ "$STATE" == COMPLETED* ]] && break
    [[ "$STATE" == FAILED* || "$STATE" == CANCELLED* ]] && { echo "Export failed for $U"; exit 1; }
    sleep 30
  done
done

echo "Done. Backups are in s3://$BUCKET/exports/"
