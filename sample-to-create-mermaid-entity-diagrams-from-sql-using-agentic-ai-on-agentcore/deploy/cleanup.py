#!/usr/bin/env python3.13
"""
Cleanup script for the SQL to ER Diagram Generator project.
Deletes all AWS resources created by the deploy scripts, in reverse order.

Usage:
    python3.13 deploy/cleanup.py                          # Delete all resources except S3 bucket
    python3.13 deploy/cleanup.py --delete-bucket          # Also empty and delete the S3 bucket
    python3.13 deploy/cleanup.py --dry-run                # Preview what would be deleted
"""

import argparse
import boto3
import json
import sys
import time
from botocore.exceptions import ClientError

# Configuration
REGION = "us-west-2"
PROJECT_NAME = "erdiagfromsql"
SSM_BASE = f"/app/{PROJECT_NAME}/agentcore"

# Resource name constants (mirrors what deploy scripts create)
RUNTIME_ROLE_NAME = f"{PROJECT_NAME}-analysis-agentcore-runtime-role"
RUNTIME_ROLE_POLICY_NAME = f"{PROJECT_NAME}AnalysisAgentCorePolicy"
RUNTIME_ECR_POLICY_NAME = f"{PROJECT_NAME}-analysis-agent-ecr-policy"
ECR_REPO_NAME = f"{PROJECT_NAME}-analysis-agent"
LAMBDA_FUNCTION_NAME = f"{PROJECT_NAME}-trigger-lambda"
LAMBDA_ROLE_NAME = f"{PROJECT_NAME}-trigger-lambda-role"
LAMBDA_POLICY_NAME = f"{PROJECT_NAME}-trigger-lambda-policy"
OAUTH2_PROVIDER_NAME = "cognitoerdiag"

SSM_PARAMETER_NAMES = [
    f"{SSM_BASE}/machine_client_id",
    f"{SSM_BASE}/machine_client_secret",
    f"{SSM_BASE}/userpool_id",
    f"{SSM_BASE}/cognito_auth_scope",
    f"{SSM_BASE}/cognito_discovery_url",
    f"{SSM_BASE}/cognito_token_url",
    f"{SSM_BASE}/cognito_auth_url",
    f"{SSM_BASE}/cognito_domain",
    f"{SSM_BASE}/memory_id",
    f"{SSM_BASE}/memory_name",
    f"{SSM_BASE}/memory_strategy_config",
    f"{SSM_BASE}/analysis_image_uri",
    f"{SSM_BASE}/analysis_runtime_id",
    f"{SSM_BASE}/analysis_runtime_arn",
    f"{SSM_BASE}/analysis_runtime_url",
]

# Clients
ssm_client = boto3.client('ssm', region_name=REGION)
iam_client = boto3.client('iam', region_name=REGION)
lambda_client = boto3.client('lambda', region_name=REGION)
s3_client = boto3.client('s3', region_name=REGION)
ecr_client = boto3.client('ecr', region_name=REGION)
cognito_client = boto3.client('cognito-idp', region_name=REGION)
agentcore_client = boto3.client('bedrock-agentcore-control', region_name=REGION)
account_id = boto3.client('sts').get_caller_identity()['Account']


def get_ssm_parameter(name: str) -> str:
    try:
        return ssm_client.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']
    except Exception:
        return None


def ok(msg): print(f"  ✅ {msg}")
def skip(msg): print(f"  ⏭️  {msg}")
def fail(msg): print(f"  ❌ {msg}")
def info(msg): print(f"  ℹ️  {msg}")


# ---------------------------------------------------------------------------
# Step 1 — Remove S3 bucket notification (Lambda trigger)
# ---------------------------------------------------------------------------
def remove_s3_notification(bucket_name: str, dry_run: bool):
    print(f"\n📋 Step 1: Remove S3 bucket notification on '{bucket_name}'")
    if dry_run:
        info(f"DRY RUN: would remove Lambda trigger from s3://{bucket_name}")
        return

    try:
        existing = s3_client.get_bucket_notification_configuration(Bucket=bucket_name)
        configs = existing.get('LambdaFunctionConfigurations', [])
        filtered = [c for c in configs if LAMBDA_FUNCTION_NAME not in c.get('LambdaFunctionArn', '')]

        new_config = {}
        if filtered:
            new_config['LambdaFunctionConfigurations'] = filtered
        if 'TopicConfigurations' in existing:
            new_config['TopicConfigurations'] = existing['TopicConfigurations']
        if 'QueueConfigurations' in existing:
            new_config['QueueConfigurations'] = existing['QueueConfigurations']

        s3_client.put_bucket_notification_configuration(
            Bucket=bucket_name,
            NotificationConfiguration=new_config
        )
        ok(f"Removed Lambda trigger from bucket '{bucket_name}'")
    except s3_client.exceptions.NoSuchBucket:
        skip(f"Bucket '{bucket_name}' does not exist — skipping notification removal")
    except Exception as e:
        fail(f"Could not remove S3 notification: {e}")


# ---------------------------------------------------------------------------
# Step 2 — Delete Lambda function
# ---------------------------------------------------------------------------
def delete_lambda_function(dry_run: bool):
    print(f"\n📋 Step 2: Delete Lambda function '{LAMBDA_FUNCTION_NAME}'")
    if dry_run:
        info(f"DRY RUN: would delete Lambda function {LAMBDA_FUNCTION_NAME}")
        return

    try:
        lambda_client.delete_function(FunctionName=LAMBDA_FUNCTION_NAME)
        ok(f"Deleted Lambda function: {LAMBDA_FUNCTION_NAME}")
    except lambda_client.exceptions.ResourceNotFoundException:
        skip(f"Lambda function '{LAMBDA_FUNCTION_NAME}' not found")
    except Exception as e:
        fail(f"Could not delete Lambda function: {e}")


# ---------------------------------------------------------------------------
# Step 3 — Delete Lambda IAM role and policies
# ---------------------------------------------------------------------------
def delete_lambda_iam_role(dry_run: bool):
    print(f"\n📋 Step 3: Delete Lambda IAM role '{LAMBDA_ROLE_NAME}'")
    if dry_run:
        info(f"DRY RUN: would delete role {LAMBDA_ROLE_NAME} and policy {LAMBDA_POLICY_NAME}")
        return

    # Detach managed policies
    try:
        attached = iam_client.list_attached_role_policies(RoleName=LAMBDA_ROLE_NAME)['AttachedPolicies']
        for policy in attached:
            iam_client.detach_role_policy(RoleName=LAMBDA_ROLE_NAME, PolicyArn=policy['PolicyArn'])
            ok(f"Detached policy: {policy['PolicyName']}")
    except iam_client.exceptions.NoSuchEntityException:
        skip(f"Role '{LAMBDA_ROLE_NAME}' not found — skipping policy detach")
        return
    except Exception as e:
        fail(f"Could not detach Lambda role policies: {e}")

    # Delete customer-managed policy
    policy_arn = f"arn:aws:iam::{account_id}:policy/{LAMBDA_POLICY_NAME}"
    try:
        iam_client.delete_policy(PolicyArn=policy_arn)
        ok(f"Deleted customer-managed policy: {LAMBDA_POLICY_NAME}")
    except iam_client.exceptions.NoSuchEntityException:
        skip(f"Policy '{LAMBDA_POLICY_NAME}' not found")
    except Exception as e:
        fail(f"Could not delete Lambda policy: {e}")

    # Delete role
    try:
        iam_client.delete_role(RoleName=LAMBDA_ROLE_NAME)
        ok(f"Deleted IAM role: {LAMBDA_ROLE_NAME}")
    except iam_client.exceptions.NoSuchEntityException:
        skip(f"Role '{LAMBDA_ROLE_NAME}' not found")
    except Exception as e:
        fail(f"Could not delete Lambda IAM role: {e}")


# ---------------------------------------------------------------------------
# Step 4 — Delete AgentCore Runtime
# ---------------------------------------------------------------------------
def delete_agentcore_runtime(dry_run: bool):
    print(f"\n📋 Step 4: Delete AgentCore Runtime")
    runtime_id = get_ssm_parameter(f"{SSM_BASE}/analysis_runtime_id")

    if not runtime_id:
        skip("No runtime ID found in SSM — skipping")
        return

    info(f"Runtime ID: {runtime_id}")
    if dry_run:
        info(f"DRY RUN: would delete AgentCore Runtime {runtime_id}")
        return

    try:
        agentcore_client.delete_agent_runtime(agentRuntimeId=runtime_id)
        ok(f"Deleted AgentCore Runtime: {runtime_id}")
    except agentcore_client.exceptions.ResourceNotFoundException:
        skip(f"AgentCore Runtime '{runtime_id}' not found")
    except Exception as e:
        fail(f"Could not delete AgentCore Runtime: {e}")


# ---------------------------------------------------------------------------
# Step 5 — Delete ECR repository
# ---------------------------------------------------------------------------
def delete_ecr_repository(dry_run: bool):
    print(f"\n📋 Step 5: Delete ECR repository '{ECR_REPO_NAME}'")
    if dry_run:
        info(f"DRY RUN: would force-delete ECR repo {ECR_REPO_NAME} (all images)")
        return

    try:
        ecr_client.delete_repository(repositoryName=ECR_REPO_NAME, force=True)
        ok(f"Deleted ECR repository: {ECR_REPO_NAME}")
    except ecr_client.exceptions.RepositoryNotFoundException:
        skip(f"ECR repository '{ECR_REPO_NAME}' not found")
    except Exception as e:
        fail(f"Could not delete ECR repository: {e}")


# ---------------------------------------------------------------------------
# Step 6 — Delete AgentCore Runtime IAM role
# ---------------------------------------------------------------------------
def delete_runtime_iam_role(dry_run: bool):
    print(f"\n📋 Step 6: Delete AgentCore Runtime IAM role '{RUNTIME_ROLE_NAME}'")
    if dry_run:
        info(f"DRY RUN: would delete role {RUNTIME_ROLE_NAME} and its inline/attached policies")
        return

    # Delete inline policies
    for policy_name in [RUNTIME_ROLE_POLICY_NAME, RUNTIME_ECR_POLICY_NAME]:
        try:
            iam_client.delete_role_policy(RoleName=RUNTIME_ROLE_NAME, PolicyName=policy_name)
            ok(f"Deleted inline policy: {policy_name}")
        except iam_client.exceptions.NoSuchEntityException:
            skip(f"Inline policy '{policy_name}' not found")
        except Exception as e:
            fail(f"Could not delete inline policy '{policy_name}': {e}")

    # Detach managed policies
    try:
        attached = iam_client.list_attached_role_policies(RoleName=RUNTIME_ROLE_NAME)['AttachedPolicies']
        for policy in attached:
            iam_client.detach_role_policy(RoleName=RUNTIME_ROLE_NAME, PolicyArn=policy['PolicyArn'])
            ok(f"Detached policy: {policy['PolicyName']}")
    except iam_client.exceptions.NoSuchEntityException:
        skip(f"Role '{RUNTIME_ROLE_NAME}' not found — skipping managed policy detach")
    except Exception as e:
        fail(f"Could not detach Runtime role managed policies: {e}")

    # Delete role
    try:
        iam_client.delete_role(RoleName=RUNTIME_ROLE_NAME)
        ok(f"Deleted IAM role: {RUNTIME_ROLE_NAME}")
    except iam_client.exceptions.NoSuchEntityException:
        skip(f"Role '{RUNTIME_ROLE_NAME}' not found")
    except Exception as e:
        fail(f"Could not delete Runtime IAM role: {e}")


# ---------------------------------------------------------------------------
# Step 7 — Delete AgentCore Memory
# ---------------------------------------------------------------------------
def delete_agentcore_memory(dry_run: bool):
    print(f"\n📋 Step 7: Delete AgentCore Memory")
    memory_id = get_ssm_parameter(f"{SSM_BASE}/memory_id")

    if not memory_id:
        skip("No memory ID found in SSM — skipping")
        return

    info(f"Memory ID: {memory_id}")
    if dry_run:
        info(f"DRY RUN: would delete AgentCore Memory {memory_id}")
        return

    try:
        from bedrock_agentcore.memory import MemoryClient
        memory_client = MemoryClient(region_name=REGION)
        memory_client.delete_memory(memory_id=memory_id)
        ok(f"Deleted AgentCore Memory: {memory_id}")
    except Exception as e:
        # Fallback to boto3 if SDK unavailable
        try:
            agentcore_client.delete_memory(memoryId=memory_id)
            ok(f"Deleted AgentCore Memory: {memory_id}")
        except Exception as e2:
            fail(f"Could not delete AgentCore Memory: {e2}")


# ---------------------------------------------------------------------------
# Step 8 — Delete AgentCore OAuth2 credential provider
# ---------------------------------------------------------------------------
def delete_oauth2_provider(dry_run: bool):
    print(f"\n📋 Step 8: Delete AgentCore OAuth2 credential provider '{OAUTH2_PROVIDER_NAME}'")
    if dry_run:
        info(f"DRY RUN: would delete OAuth2 credential provider {OAUTH2_PROVIDER_NAME}")
        return

    try:
        agentcore_client.delete_oauth2_credential_provider(name=OAUTH2_PROVIDER_NAME)
        ok(f"Deleted OAuth2 credential provider: {OAUTH2_PROVIDER_NAME}")
    except agentcore_client.exceptions.ResourceNotFoundException:
        skip(f"OAuth2 provider '{OAUTH2_PROVIDER_NAME}' not found")
    except Exception as e:
        fail(f"Could not delete OAuth2 provider: {e}")


# ---------------------------------------------------------------------------
# Step 9 — Delete Cognito User Pool (domain → pool)
# ---------------------------------------------------------------------------
def delete_cognito(dry_run: bool):
    print(f"\n📋 Step 9: Delete Cognito User Pool")
    user_pool_id = get_ssm_parameter(f"{SSM_BASE}/userpool_id")
    cognito_domain_url = get_ssm_parameter(f"{SSM_BASE}/cognito_domain")

    if not user_pool_id:
        skip("No User Pool ID found in SSM — skipping")
        return

    info(f"User Pool ID: {user_pool_id}")
    if dry_run:
        info(f"DRY RUN: would delete Cognito domain and User Pool {user_pool_id}")
        return

    # Delete domain first (required before deleting pool)
    if cognito_domain_url:
        # Extract just the subdomain from the full URL
        # e.g. https://erdiagfromsqluswest2abcd1234.auth.us-west-2.amazoncognito.com
        domain_name = cognito_domain_url.replace("https://", "").split(".")[0]
        try:
            cognito_client.delete_user_pool_domain(Domain=domain_name, UserPoolId=user_pool_id)
            ok(f"Deleted Cognito domain: {domain_name}")
            info("Waiting 10s for domain deletion to propagate...")
            time.sleep(10)
        except cognito_client.exceptions.ResourceNotFoundException:
            skip(f"Cognito domain '{domain_name}' not found")
        except Exception as e:
            fail(f"Could not delete Cognito domain: {e}")

    # Delete User Pool (clients and resource servers are deleted with it)
    try:
        cognito_client.delete_user_pool(UserPoolId=user_pool_id)
        ok(f"Deleted Cognito User Pool: {user_pool_id}")
    except cognito_client.exceptions.ResourceNotFoundException:
        skip(f"User Pool '{user_pool_id}' not found")
    except Exception as e:
        fail(f"Could not delete Cognito User Pool: {e}")


# ---------------------------------------------------------------------------
# Step 10 — Delete SSM parameters
# ---------------------------------------------------------------------------
def delete_ssm_parameters(dry_run: bool):
    print(f"\n📋 Step 10: Delete SSM parameters under '{SSM_BASE}/'")
    if dry_run:
        for name in SSM_PARAMETER_NAMES:
            info(f"DRY RUN: would delete SSM parameter {name}")
        return

    for name in SSM_PARAMETER_NAMES:
        try:
            ssm_client.delete_parameter(Name=name)
            ok(f"Deleted SSM parameter: {name}")
        except ssm_client.exceptions.ParameterNotFound:
            skip(f"SSM parameter not found: {name}")
        except Exception as e:
            fail(f"Could not delete SSM parameter '{name}': {e}")


# ---------------------------------------------------------------------------
# Step 11 (optional) — Empty and delete S3 bucket
# ---------------------------------------------------------------------------
def delete_s3_bucket(bucket_name: str, dry_run: bool):
    print(f"\n📋 Step 11: Empty and delete S3 bucket '{bucket_name}'")
    if dry_run:
        info(f"DRY RUN: would empty and delete s3://{bucket_name}")
        return

    try:
        # Delete all object versions (handles versioned buckets)
        paginator = s3_client.get_paginator('list_object_versions')
        for page in paginator.paginate(Bucket=bucket_name):
            objects = []
            for v in page.get('Versions', []):
                objects.append({'Key': v['Key'], 'VersionId': v['VersionId']})
            for m in page.get('DeleteMarkers', []):
                objects.append({'Key': m['Key'], 'VersionId': m['VersionId']})
            if objects:
                s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': objects})

        # Delete all regular objects (non-versioned)
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name):
            objects = [{'Key': o['Key']} for o in page.get('Contents', [])]
            if objects:
                s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': objects})

        ok(f"Emptied bucket: {bucket_name}")

        s3_client.delete_bucket(Bucket=bucket_name)
        ok(f"Deleted S3 bucket: {bucket_name}")

    except s3_client.exceptions.NoSuchBucket:
        skip(f"S3 bucket '{bucket_name}' not found")
    except Exception as e:
        fail(f"Could not delete S3 bucket: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Clean up all AWS resources for the erdiagfromsql project")
    parser.add_argument("--s3-bucket", metavar="BUCKET", help="S3 bucket name to remove the trigger from (required)")
    parser.add_argument("--delete-bucket", action="store_true", help="Also empty and delete the S3 bucket itself")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be deleted without making changes")
    args = parser.parse_args()

    if not args.s3_bucket:
        print("❌ --s3-bucket is required (needed to remove the Lambda trigger notification).")
        print("   Usage: python3.13 deploy/cleanup.py --s3-bucket <bucket-name>")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print("=" * 60)
    print(f"🧹 erdiagfromsql Cleanup — {mode}")
    print(f"📍 Region:  {REGION}")
    print(f"📍 Account: {account_id}")
    print(f"📍 Bucket:  {args.s3_bucket}")
    if args.delete_bucket:
        print("⚠️  --delete-bucket: S3 bucket and all its contents will be deleted")
    print("=" * 60)

    remove_s3_notification(args.s3_bucket, args.dry_run)
    delete_lambda_function(args.dry_run)
    delete_lambda_iam_role(args.dry_run)
    delete_agentcore_runtime(args.dry_run)
    delete_ecr_repository(args.dry_run)
    delete_runtime_iam_role(args.dry_run)
    delete_agentcore_memory(args.dry_run)
    delete_oauth2_provider(args.dry_run)
    delete_cognito(args.dry_run)
    delete_ssm_parameters(args.dry_run)

    if args.delete_bucket:
        delete_s3_bucket(args.s3_bucket, args.dry_run)
    else:
        print(f"\n⏭️  Skipping S3 bucket deletion (pass --delete-bucket to also remove it)")

    print("\n" + "=" * 60)
    if args.dry_run:
        print("✅ Dry run complete — no resources were modified.")
    else:
        print("✅ Cleanup complete!")
    print("=" * 60)

    print("\n💡 Don't forget to deactivate your virtual environment:")
    print("   deactivate")


if __name__ == "__main__":
    main()
