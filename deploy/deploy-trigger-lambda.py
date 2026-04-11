#!/usr/bin/env python3.13
"""
Deploy S3 Trigger Lambda for SQL ER Diagram Generation
Creates Lambda function and S3 trigger for SQL file uploads
"""

import boto3
import json
import os
import sys
import zipfile
import tempfile
from pathlib import Path

# Configuration
PROJECT_NAME = os.environ.get('PROJECT_NAME', 'erdiagfromsql')
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-west-2')

def get_account_id():
    """Get AWS account ID"""
    try:
        sts_client = boto3.client('sts', region_name=REGION)
        return sts_client.get_caller_identity()['Account']
    except Exception as e:
        print(f"❌ Error getting account ID: {e}")
        return None

def create_lambda_zip():
    """Create deployment package for Lambda"""
    try:
        print("📦 Creating Lambda deployment package...")
        
        # Create temporary zip file
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_file:
            with zipfile.ZipFile(tmp_file.name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Add the Lambda function code
                lambda_code_path = Path(__file__).parent.parent / 'src' / 'trigger-lambda.py'
                if lambda_code_path.exists():
                    zip_file.write(lambda_code_path, 'lambda_function.py')
                    print(f"✅ Added {lambda_code_path} as lambda_function.py")
                else:
                    print(f"❌ Lambda code not found: {lambda_code_path}")
                    return None
            
            print(f"✅ Created deployment package: {tmp_file.name}")
            return tmp_file.name
            
    except Exception as e:
        print(f"❌ Error creating deployment package: {e}")
        return None

def create_lambda_role(account_id):
    """Create IAM role for Lambda function"""
    try:
        iam_client = boto3.client('iam', region_name=REGION)
        role_name = f"{PROJECT_NAME}-trigger-lambda-role"
        
        # Check if role exists
        try:
            iam_client.get_role(RoleName=role_name)
            print(f"✅ IAM role already exists: {role_name}")
            return f"arn:aws:iam::{account_id}:role/{role_name}"
        except iam_client.exceptions.NoSuchEntityException:
            pass
        
        print(f"🔧 Creating IAM role: {role_name}")
        
        # Trust policy for Lambda
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "lambda.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        # Create role
        iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"IAM role for {PROJECT_NAME} trigger Lambda function"
        )
        
        # Attach basic Lambda execution policy
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        )
        
        # Create custom policy for Lambda permissions
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:ListBucket",
                        "s3:ListBucketVersions",
                        "s3:GetBucketLocation"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ssm:GetParameter"
                    ],
                    "Resource": [
                        f"arn:aws:ssm:{REGION}:{account_id}:parameter/app/{PROJECT_NAME}/*"
                    ]
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "cognito-idp:DescribeUserPoolClient"
                    ],
                    "Resource": [
                        f"arn:aws:cognito-idp:{REGION}:{account_id}:userpool/*"
                    ]
                }
            ]
        }
        
        policy_name = f"{PROJECT_NAME}-trigger-lambda-policy"
        
        # Create and attach custom policy
        iam_client.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
            Description=f"Custom policy for {PROJECT_NAME} trigger Lambda"
        )
        
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn=f"arn:aws:iam::{account_id}:policy/{policy_name}"
        )
        
        print(f"✅ Created IAM role: {role_name}")
        return f"arn:aws:iam::{account_id}:role/{role_name}"
        
    except Exception as e:
        print(f"❌ Error creating IAM role: {e}")
        return None

def create_lambda_function(role_arn, zip_file_path):
    """Create Lambda function"""
    try:
        lambda_client = boto3.client('lambda', region_name=REGION)
        function_name = f"{PROJECT_NAME}-trigger-lambda"
        
        # Check if function exists
        try:
            lambda_client.get_function(FunctionName=function_name)
            print(f"🔄 Updating existing Lambda function: {function_name}")
            
            # Update function code
            with open(zip_file_path, 'rb') as zip_file:
                lambda_client.update_function_code(
                    FunctionName=function_name,
                    ZipFile=zip_file.read()
                )
            
            # Update configuration
            lambda_client.update_function_configuration(
                FunctionName=function_name,
                Timeout=900,  # 15 minutes
                Environment={
                    'Variables': {
                        'PROJECT_NAME': PROJECT_NAME,
                        'AWS_DEFAULT_REGION': REGION
                    }
                }
            )
            
            print(f"✅ Updated Lambda function: {function_name}")
            return function_name
            
        except lambda_client.exceptions.ResourceNotFoundException:
            pass
        
        print(f"🔧 Creating Lambda function: {function_name}")
        
        # Create function
        with open(zip_file_path, 'rb') as zip_file:
            response = lambda_client.create_function(
                FunctionName=function_name,
                Runtime='python3.13',
                Role=role_arn,
                Handler='lambda_function.lambda_handler',
                Code={'ZipFile': zip_file.read()},
                Description=f'S3 trigger for {PROJECT_NAME} SQL ER diagram generation',
                Timeout=900,  # 15 minutes
                MemorySize=512,
                Environment={
                    'Variables': {
                        'PROJECT_NAME': PROJECT_NAME
                    }
                }
            )
        
        print(f"✅ Created Lambda function: {function_name}")
        return function_name
        
    except Exception as e:
        print(f"❌ Error creating Lambda function: {e}")
        return None

def create_s3_trigger(function_name, bucket_name, bucket_region):
    """Create S3 trigger for Lambda function"""
    try:
        lambda_client = boto3.client('lambda', region_name=REGION)
        s3_client = boto3.client('s3', region_name=bucket_region)
        
        account_id = get_account_id()
        if not account_id:
            return False
        
        lambda_arn = f"arn:aws:lambda:{REGION}:{account_id}:function:{function_name}"
        
        # Verify Lambda function exists
        try:
            lambda_client.get_function(FunctionName=function_name)
            print(f"✅ Verified Lambda function exists: {function_name}")
        except Exception as e:
            print(f"❌ Lambda function not found: {e}")
            return False
        
        # Add permission for S3 to invoke Lambda
        statement_id = f"{PROJECT_NAME}-s3-trigger-permission"
        
        try:
            lambda_client.remove_permission(
                FunctionName=function_name,
                StatementId=statement_id
            )
            print(f"🔄 Removed existing permission")
        except:
            pass
        
        # Add fresh permission
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action='lambda:InvokeFunction',
            Principal='s3.amazonaws.com',
            SourceArn=f'arn:aws:s3:::{bucket_name}'
        )
        print(f"✅ Added S3 invoke permission for bucket: {bucket_name}")
        
        # Wait for permission to propagate
        print("⏳ Waiting for permissions to propagate...")
        import time
        time.sleep(15)
        
        # Test S3 can access Lambda
        try:
            lambda_client.get_policy(FunctionName=function_name)
            print("✅ Lambda policy verified")
        except Exception as e:
            print(f"⚠️ Could not verify Lambda policy: {e}")
        
        # Configure S3 bucket notification
        try:
            existing_config = s3_client.get_bucket_notification_configuration(Bucket=bucket_name)
        except s3_client.exceptions.NoSuchBucket:
            print(f"❌ S3 bucket not found: {bucket_name}")
            return False
        except Exception:
            existing_config = {}
        
        # Add Lambda configuration
        lambda_configurations = existing_config.get('LambdaFunctionConfigurations', [])
        
        # Remove existing configuration for this Lambda if it exists
        lambda_configurations = [
            config for config in lambda_configurations 
            if config.get('LambdaFunctionArn') != lambda_arn
        ]
        
        # Add new configuration
        lambda_configurations.append({
            'Id': f'{PROJECT_NAME}-sql-file-trigger',
            'LambdaFunctionArn': lambda_arn,
            'Events': ['s3:ObjectCreated:*'],
            'Filter': {
                'Key': {
                    'FilterRules': [
                        {
                            'Name': 'suffix',
                            'Value': '.sql'
                        }
                    ]
                }
            }
        })
        
        # Update notification configuration
        notification_config = {
            'LambdaFunctionConfigurations': lambda_configurations
        }
        
        # Preserve existing configurations
        if 'TopicConfigurations' in existing_config:
            notification_config['TopicConfigurations'] = existing_config['TopicConfigurations']
        if 'QueueConfigurations' in existing_config:
            notification_config['QueueConfigurations'] = existing_config['QueueConfigurations']
        
        print(f"🔧 Configuring S3 notification for bucket: {bucket_name}")
        s3_client.put_bucket_notification_configuration(
            Bucket=bucket_name,
            NotificationConfiguration=notification_config
        )
        
        print(f"✅ Created S3 trigger for SQL files in bucket: {bucket_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error creating S3 trigger: {e}")
        return False

def main():
    """Main deployment function"""
    import argparse
    parser = argparse.ArgumentParser(description="Deploy S3 Trigger Lambda for ER Diagram generation")
    parser.add_argument("--s3-bucket", required=True, help="S3 bucket name to watch for .sql file uploads")
    parser.add_argument("--bucket-region", required=True, help="AWS region of the S3 bucket (e.g. us-west-2)")
    args = parser.parse_args()

    bucket_name = args.s3_bucket
    bucket_region = args.bucket_region
    
    print(f"🚀 Deploying S3 Trigger Lambda for {PROJECT_NAME}")
    print(f"📍 Lambda Region: {REGION}")
    print(f"📍 S3 Bucket: {bucket_name} (region: {bucket_region})")
    print("=" * 60)
    
    # Get account ID
    account_id = get_account_id()
    if not account_id:
        print("❌ Failed to get AWS account ID")
        sys.exit(1)
    
    print(f"📋 AWS Account: {account_id}")
    
    # Create deployment package
    zip_file_path = create_lambda_zip()
    if not zip_file_path:
        print("❌ Failed to create deployment package")
        sys.exit(1)
    
    try:
        # Create IAM role
        role_arn = create_lambda_role(account_id)
        if not role_arn:
            print("❌ Failed to create IAM role")
            sys.exit(1)
        
        # Wait for role to propagate
        print("⏳ Waiting for IAM role to propagate...")
        import time
        time.sleep(10)
        
        # Create Lambda function
        function_name = create_lambda_function(role_arn, zip_file_path)
        if not function_name:
            print("❌ Failed to create Lambda function")
            sys.exit(1)
        
        # Create S3 trigger
        if not create_s3_trigger(function_name, bucket_name, bucket_region):
            print("❌ Failed to create S3 trigger")
            sys.exit(1)
        
        print("\n" + "=" * 60)
        print("🎉 S3 Trigger Lambda Deployment Complete!")
        print(f"📋 Lambda Function: {function_name}")
        print(f"📋 S3 Bucket: {bucket_name}")
        print(f"📋 Trigger: SQL files (.sql) uploaded to S3")
        print("\n💡 Test by uploading a SQL file to the S3 bucket!")
        print("📊 Check CloudWatch logs for ER diagram generation results")
        
    finally:
        # Clean up temporary zip file
        if zip_file_path and os.path.exists(zip_file_path):
            os.unlink(zip_file_path)
            print(f"🧹 Cleaned up temporary file: {zip_file_path}")

if __name__ == "__main__":
    main()
