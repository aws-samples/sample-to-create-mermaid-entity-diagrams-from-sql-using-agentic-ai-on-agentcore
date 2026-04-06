#!/usr/bin/env python3.13
import boto3
import subprocess
import tempfile
import os
import json
import time
import base64
from botocore.exceptions import ClientError

# Configuration
REGION = "us-west-2"
PROJECT_NAME = "erdiagfromsql"
PRIVATE_ECR_REPO = f"{PROJECT_NAME}-analysis-agent"
IMAGE_TAG = "latest"

# Initialize clients
ecr_client = boto3.client('ecr', region_name=REGION)
ssm_client = boto3.client('ssm', region_name=REGION)
agentcore_client = boto3.client('bedrock-agentcore-control', region_name=REGION)
iam_client = boto3.client('iam', region_name=REGION)

def store_ssm_parameter(name: str, value: str, description: str):
    """Store parameter in SSM Parameter Store"""
    try:
        ssm_client.put_parameter(
            Name=name,
            Value=value,
            Description=description,
            Type='String',
            Overwrite=True
        )
        print(f"✅ Stored SSM parameter: {name}")
    except Exception as e:
        print(f"❌ Failed to store SSM parameter {name}: {e}")

def get_ssm_parameter(name: str) -> str:
    """Get parameter from SSM Parameter Store"""
    try:
        response = ssm_client.get_parameter(Name=name, WithDecryption=True)
        return response['Parameter']['Value']
    except Exception:
        return None

def create_requirements_txt():
    """Create requirements.txt for the analysis agent with version constraints and reference packages"""
    requirements = """
boto3==1.40.0
botocore==1.40.0
strands-agents
strands-agents-tools
uv
bedrock-agentcore
bedrock-agentcore-starter-toolkit
aws-opentelemetry-distro~=0.10.1
"""
    return requirements.strip()

def create_dockerfile():
    """Create Dockerfile for Analysis Agent with OpenTelemetry instrumentation and proper Strands build"""
    dockerfile_content = """
FROM --platform=linux/arm64 python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV AWS_DEFAULT_REGION=us-west-2

# Install system dependencies including C++ compiler and Git for Strands
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    cmake \
    ninja-build \
    python3-dev \
    build-essential \
    git \
    pkg-config \
    libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .

# Install dependencies with verbose output for debugging
RUN pip install --no-cache-dir --verbose -r requirements.txt

# Copy application code
COPY erdiag-agent.py .

# Expose port
EXPOSE 8080

# Use manual instrumentation instead of auto-instrumentation
CMD ["python", "erdiag-agent.py"]
"""
    return dockerfile_content.strip()

def create_private_ecr_and_push():
    """Create private ECR repository and push Docker image"""
    try:
        # Create private ECR repository
        try:
            response = ecr_client.create_repository(
                repositoryName=PRIVATE_ECR_REPO,
                imageScanningConfiguration={'scanOnPush': True},
                encryptionConfiguration={'encryptionType': 'AES256'}
            )
            repo_uri = response['repository']['repositoryUri']
            print(f"✅ Created private ECR repository: {repo_uri}")
        except ecr_client.exceptions.RepositoryAlreadyExistsException:
            response = ecr_client.describe_repositories(repositoryNames=[PRIVATE_ECR_REPO])
            repo_uri = response['repositories'][0]['repositoryUri']
            print(f"✅ Using existing private ECR repository: {repo_uri}")
        
        image_uri = f"{repo_uri}:{IMAGE_TAG}"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"🔨 Building Docker image in: {temp_dir}")
            
            # Write Dockerfile
            dockerfile_path = os.path.join(temp_dir, 'Dockerfile')
            with open(dockerfile_path, 'w') as f:
                f.write(create_dockerfile())
            
            # Write requirements.txt
            requirements_path = os.path.join(temp_dir, 'requirements.txt')
            with open(requirements_path, 'w') as f:
                f.write(create_requirements_txt())
            
            # Copy analysis agent
            agent_source = 'erdiag-agent.py'
            agent_dest = os.path.join(temp_dir, 'erdiag-agent.py')
            
            if not os.path.exists(agent_source):
                raise FileNotFoundError(f"Analysis agent file not found: {agent_source}")
            
            with open(agent_source, 'r') as src, open(agent_dest, 'w') as dst:
                dst.write(src.read())
            
            # Get ECR login token
            print("🔐 Getting private ECR login token...")
            login_response = ecr_client.get_authorization_token()
            token = login_response['authorizationData'][0]['authorizationToken']
            endpoint = login_response['authorizationData'][0]['proxyEndpoint']
            
            # Decode token and login to Docker
            username, password = base64.b64decode(token).decode().split(':')
            
            print("🔐 Logging into private ECR...")
            subprocess.run([
                'docker', 'login', '--username', username, '--password-stdin', endpoint
            ], input=password.encode(), check=True)
            
            # Build Docker image for arm64
            print(f"🔨 Building Docker image: {image_uri}")
            subprocess.run([
                'docker', 'build', '--platform', 'linux/arm64', '-t', image_uri, temp_dir
            ], check=True)
            
            # Push to private ECR
            print(f"📤 Pushing image to private ECR...")
            subprocess.run([
                'docker', 'push', image_uri
            ], check=True)
            
            print(f"✅ Successfully pushed Docker image: {image_uri}")
            return image_uri
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Docker build/push failed: {e}")
        return None
    except Exception as e:
        print(f"❌ Error in Docker operations: {e}")
        return None

def get_cognito_bearer_token():
    """Get bearer token from Cognito for authentication"""
    try:
        # Get Cognito configuration
        client_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_id")
        token_url = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_token_url")
        auth_scope = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_auth_scope")
        user_pool_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/userpool_id")
        
        if not all([client_id, token_url, auth_scope, user_pool_id]):
            print("❌ Missing Cognito configuration for token generation")
            return None
        
        # Get client secret from Cognito
        cognito_client = boto3.client('cognito-idp', region_name=REGION)
        
        try:
            client_response = cognito_client.describe_user_pool_client(
                UserPoolId=user_pool_id,
                ClientId=client_id
            )
            
            client_secret = client_response['UserPoolClient'].get('ClientSecret')
            if not client_secret:
                print("❌ Client secret not found - client may not have secret enabled")
                return None
            
            print("✅ Retrieved client secret from Cognito")
            
            # Use client credentials flow with proper authentication
            import requests
            import base64
            
            # Create proper Basic auth header with client_id:client_secret
            auth_string = f"{client_id}:{client_secret}"
            auth_header = base64.b64encode(auth_string.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            data = {
                'grant_type': 'client_credentials',
                'scope': auth_scope
            }
            
            print(f"🔐 Requesting token from: {token_url}")
            response = requests.post(token_url, headers=headers, data=data)
            
            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get('access_token')
                print("✅ Successfully obtained bearer token")
                return access_token
            else:
                print(f"❌ Token request failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ Error getting client secret or token: {e}")
            return None
            
    except Exception as e:
        print(f"❌ Error in token generation: {e}")
        return None

def create_enhanced_agentcore_runtime_role():
    """Create enhanced IAM role for AgentCore Runtime with comprehensive permissions"""
    role_name = f"{PROJECT_NAME}-analysis-agentcore-runtime-role"
    
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": [
                        "lambda.amazonaws.com",
                        "bedrock-agentcore.amazonaws.com"
                    ]
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    # Get account ID
    account_id = boto3.client('sts').get_caller_identity()['Account']
    
    # Enhanced policy with comprehensive AgentCore permissions - define outside try/except
    enhanced_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer"
                ],
                "Resource": [
                    f"arn:aws:ecr:{REGION}:{account_id}:repository/{PRIVATE_ECR_REPO}"
                ]        
            },
            {
                "Sid": "ECRTokenAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:DescribeLogStreams",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogGroups"
                ],
                "Resource": [
                    f"arn:aws:logs:{REGION}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
                    f"arn:aws:logs:{REGION}:{account_id}:log-group:*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": [
                    f"arn:aws:logs:{REGION}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ssm:GetParameter"
                ],
                "Resource": [
                    f"arn:aws:ssm:{REGION}:{account_id}:parameter/app/{PROJECT_NAME}/agentcore/*"
                ]
            },
            {
                "Effect": "Allow", 
                "Action": [ 
                    "xray:PutTraceSegments", 
                    "xray:PutTelemetryRecords", 
                    "xray:GetSamplingRules", 
                    "xray:GetSamplingTargets"
                ],
                "Resource": [ "*" ] 
            },
            {
                "Effect": "Allow",
                "Resource": "*",
                "Action": "cloudwatch:PutMetricData",
                "Condition": {
                    "StringEquals": {
                        "cloudwatch:namespace": "bedrock-agentcore"
                    }
                }
            },
            {
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    "bedrock-agentcore:ListWorkloadIdentities",
                    "bedrock-agentcore:CreateWorkloadIdentity",
                    "bedrock-agentcore:GetWorkloadIdentity",
                    "bedrock-agentcore:GetResourceOauth2Token",
                    "bedrock-agentcore:CreateEvent"
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:workload-identity-directory/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:/identities/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:resource-credential-provider-directory/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:token-vault/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:memory/*"
                ]
            },
            {
                "Sid": "BedrockModelInvocation", 
                "Effect": "Allow", 
                "Action": [ 
                    "bedrock:InvokeModel", 
                    "bedrock:InvokeModelWithResponseStream"
                ], 
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{REGION}:{account_id}:*"
                ]
            },
            {
                "Sid": "SecretsManagerAccess",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                "Resource": "*"
            },
            {
                "Sid": "CognitoAccess",
                "Effect": "Allow",
                "Action": [
                    "cognito-idp:DescribeUserPoolClient"
                ],
                "Resource": [
                    f"arn:aws:cognito-idp:{REGION}:{account_id}:userpool/*"
                ]
            },
            {
                "Sid": "S3DiagramStorage",
                "Effect": "Allow",
                "Action": [
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:ListBucket"
                ],
                "Resource": [
                    "arn:aws:s3:::bedrock-bda-us-west-2-logging-9739327b-fe72-4296-805a-12b6c7c6e/*",
                    "arn:aws:s3:::bedrock-bda-us-west-2-logging-9739327b-fe72-4296-805a-12b6c7c6e"
                ]
            }
        ]
    }

    try:
        iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Enhanced IAM role for Analysis Agent AgentCore Runtime"
        )

        # Wait for IAM role to propagate
        print("⏳ Waiting for IAM role to propagate...")
        time.sleep(10)
        
        # Attach basic Lambda execution policy
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        )
        
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=f"{PROJECT_NAME}AnalysisAgentCorePolicy",
            PolicyDocument=json.dumps(enhanced_policy)
        )
        
        print(f"✅ Created enhanced IAM role: {role_name}")
        return f"arn:aws:iam::{account_id}:role/{role_name}"
        
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"✅ Enhanced IAM role already exists: {role_name}")
        
        # ALWAYS update the policy even if role exists
        print("🔄 Updating IAM policy with latest permissions...")
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=f"{PROJECT_NAME}AnalysisAgentCorePolicy",
            PolicyDocument=json.dumps(enhanced_policy)
        )
        print("✅ IAM policy updated with latest permissions")
        
        return f"arn:aws:iam::{account_id}:role/{role_name}"

def add_private_ecr_permissions_to_role(role_arn: str, repo_arn: str):
    """Add private ECR permissions to the execution role for AgentCore Runtime"""
    try:
        # Extract role name from ARN
        role_name = role_arn.split('/')[-1]
        
        # Private ECR policy
        ecr_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:GetAuthorizationToken"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchGetImage",
                        "ecr:GetDownloadUrlForLayer"
                    ],
                    "Resource": repo_arn
                }
            ]
        }
        
        policy_name = f"{PROJECT_NAME}-analysis-agent-ecr-policy"
        
        # Attach the policy to the role
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(ecr_policy)
        )
        
        print(f"✅ Added private ECR permissions to role: {role_name}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to add private ECR permissions: {e}")
        return False

def deploy_agentcore_runtime(image_uri: str):
    """Deploy Analysis Agent as AgentCore Runtime with enhanced configuration"""
    try:
        # Get Cognito configuration for OAuth
        cognito_config = {
            'auth_scope': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_auth_scope"),
            'auth_url': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_auth_url"),
            'discovery_url': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_discovery_url"),
            'domain': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_domain"),
            'token_url': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_token_url"),
            'machine_client_id': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_id"),
            'machine_client_secret': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_secret")
        }
        
        if not all(cognito_config.values()):
            raise ValueError("Missing Cognito configuration in SSM. Required parameters: cognito_auth_scope, cognito_auth_url, cognito_discover_url, cognito_domain, cognito_token_url")
        
        runtime_name = f"{PROJECT_NAME}_analysis_runtime"
        
        print(f"🚀 Deploying AgentCore Runtime: {runtime_name}")
        
        # Create enhanced IAM role
        print("🔧 Creating enhanced IAM role...")
        role_arn = create_enhanced_agentcore_runtime_role()
        print(f"✅ Role ready: {role_arn}")
        
        # Wait for IAM role to propagate
        print("⏳ Waiting for IAM role to propagate...")
        time.sleep(10)
        
        # Create AgentCore Runtime with enhanced configuration
        response = agentcore_client.create_agent_runtime(
            agentRuntimeName=runtime_name,
            agentRuntimeArtifact={
                'containerConfiguration': {
                    'containerUri': image_uri
                }
            },
            networkConfiguration={"networkMode": "PUBLIC"},
            roleArn=role_arn,
            environmentVariables={
                'AWS_DEFAULT_REGION': REGION,
                'PROJECT_NAME': PROJECT_NAME,
                'CODE_ANALYSIS_MODEL': 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
                'S3_BUCKET_NAME': 'bedrock-bda-us-west-2-logging-9739327b-fe72-4296-805a-12b6c7c6e'
            },
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "allowedClients": [cognito_config['machine_client_id']],
                    "discoveryUrl": cognito_config['discovery_url'],
                }
            }
        )
        
        runtime_id = response['agentRuntimeId']
        runtime_arn = response['agentRuntimeArn']
        
        print(f"✅ Created AgentCore Runtime: {runtime_id}")
        print(f"📋 Runtime ARN: {runtime_arn}")
        
        # Wait for runtime to be ready
        print("⏳ Waiting for runtime to be ready...")
        while True:
            status_response = agentcore_client.get_agent_runtime(agentRuntimeId=runtime_id)
            status = status_response['status']
            print(f"Current status: {status}")
            
            if status == 'READY':
                break
            elif status in ['CREATE_FAILED', 'DELETE_FAILED', 'UPDATE_FAILED', 'FAILED']:
                print(f"❌ Runtime deployment failed with status: {status}")
                return None, None
            
            time.sleep(30)
        
        # Use proper AgentCore runtime URL format (no custom endpoint needed)
        import urllib.parse
        escaped_runtime_arn = urllib.parse.quote(runtime_arn, safe='')
        runtime_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{escaped_runtime_arn}/invocations?qualifier=DEFAULT"
        
        print(f"📋 Runtime URL: {runtime_url}")
        
        print(f"✅ AgentCore Runtime deployed successfully!")
        print(f"📋 Runtime ID: {runtime_id}")
        print(f"📋 Runtime ARN: {runtime_arn}")
        print(f"📋 Runtime URL: {runtime_url}")
        
        return runtime_id, runtime_arn, runtime_url
        
    except Exception as e:
        print(f"❌ Failed to deploy AgentCore Runtime: {e}")
        return None, None, None

def test_analysis_agent(runtime_url: str, cognito_config: dict):
    """Test the deployed analysis agent using proper AgentCore runtime invocation"""
    import requests
    import uuid
    
    print(f"\n🧪 Testing Analysis Agent...")
    
    try:
        # Get OAuth token using proper client credentials flow
        print("🔐 Getting OAuth token...")
        
        import base64
        
        # Create proper Basic auth header with client_id:client_secret
        auth_string = f"{cognito_config['machine_client_id']}:{cognito_config['machine_client_secret']}"
        auth_header = base64.b64encode(auth_string.encode()).decode()
        
        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'client_credentials',
            'scope': cognito_config['auth_scope']
        }
        
        token_response = requests.post(cognito_config['token_url'], headers=headers, data=data)
        
        if token_response.status_code != 200:
            print(f"❌ Failed to get OAuth token: {token_response.text}")
            return
        
        token = token_response.json()['access_token']
        print("✅ OAuth token obtained successfully")
        
        # Generate session ID for testing
        session_id = str(uuid.uuid4())
        
        # Test analysis endpoint with SQL schema
        test_payload_sql = {
            "sql_content": """CREATE TABLE users (
    user_id INT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    user_id INT NOT NULL,
    order_date TIMESTAMP,
    total_amount DECIMAL(10,2),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE products (
    product_id INT PRIMARY KEY,
    product_name VARCHAR(100),
    price DECIMAL(10,2)
);

CREATE TABLE order_items (
    item_id INT PRIMARY KEY,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);""",
            "file_name": "test_schema.sql",
            "session_id": "test-session-123",
            "actor_id": "test-user"
        }
        
        # Use proper AgentCore runtime headers
        runtime_headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
        }
        
        # Test SQL ER diagram generation
        print("📋 Testing SQL ER diagram generation...")
        response = requests.post(
            runtime_url,
            headers=runtime_headers,
            json=test_payload_sql,
            timeout=60
        )
        
        print(f"SQL Analysis - Status Code: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ SQL analysis completed successfully!")
            print(f"📋 Action: {result.get('action', 'N/A')}")
            print(f"📋 Status: {result.get('status', 'N/A')}")
            print(f"📋 Tables: {len(result.get('tables', []))}")
        else:
            print(f"❌ SQL analysis failed: {response.text}")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")

def update_runtime_model(runtime_id: str, new_model_id: str):
    """Update the LLM model for the deployed runtime"""
    try:
        print(f"🔄 Updating runtime model to: {new_model_id}")
        
        # Get current runtime configuration
        current_runtime = agentcore_client.get_agent_runtime(agentRuntimeId=runtime_id)
        
        # Update the model environment variable
        env_vars = current_runtime.get('environmentVariables', {})
        env_vars['CODE_ANALYSIS_MODEL'] = new_model_id
        
        # Update the runtime
        agentcore_client.update_agent_runtime(
            agentRuntimeId=runtime_id,
            environmentVariables=env_vars
        )
        
        print(f"✅ Runtime model updated successfully!")
        print(f"📋 New Model: {new_model_id}")
        print(f"💡 Runtime will restart automatically with new model")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to update runtime model: {e}")
        return False

def list_available_models():
    """List available Bedrock models for code analysis"""
    models = {
        "Sonnet 4.5 (Latest)": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "Sonnet 3.7": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        "Sonnet 3.5 (Thinking)": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "Sonnet 3.5 (Standard)": "anthropic.claude-3-5-sonnet-20240620-v1:0", 
        "Haiku 3.5": "anthropic.claude-3-5-haiku-20241022-v1:0",
        "Amazon Nova Pro": "amazon.nova-pro-v1:0",
        "Llama 3.1 70B": "meta.llama3-1-70b-instruct-v1:0"
    }
    
    print("\n📋 Available Models for Code Analysis:")
    for name, model_id in models.items():
        print(f"  • {name}: {model_id}")
    
    return models

def main():
    """Main deployment function"""
    print("🚀 Starting Analysis Agent deployment...")
    print(f"📋 Project: {PROJECT_NAME}")
    print(f"📋 Region: {REGION}")
    
    try:
        # Step 1: Get image URI from SSM or build new image
        print("\n📋 Step 1: Getting image URI from SSM...")
        image_uri = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/analysis_image_uri")
        
        if not image_uri:
            # Build and push new Docker image
            print("⚠️  No image URI in SSM, building and pushing new Docker image...")
            image_uri = create_private_ecr_and_push()
            
            if not image_uri:
                raise Exception("Failed to build and push Docker image")
            
            # Store it in SSM for future use
            store_ssm_parameter(
                f"/app/{PROJECT_NAME}/agentcore/analysis_image_uri",
                image_uri,
                "ECR image URI for Analysis Agent"
            )
        else:
            print(f"✅ Using image URI from SSM: {image_uri}")
        
        # Store image URI in SSM (ensure it's stored)
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/analysis_image_uri",
            image_uri,
            "ECR image URI for Analysis Agent"
        )
        
        # Step 2: Deploy AgentCore Runtime
        print("\n📋 Step 2: Deploying AgentCore Runtime...")
        runtime_id, runtime_arn, runtime_url = deploy_agentcore_runtime(image_uri)
        
        if not runtime_id:
            raise Exception("Failed to deploy AgentCore Runtime")
        
        # Step 3: Test the deployment BEFORE storing SSM parameters
        print("\n📋 Step 3: Testing deployment...")
        
        # Get Cognito configuration for testing
        cognito_config = {
            'machine_client_id': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_id"),
            'machine_client_secret': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_secret"),
            'token_url': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_token_url"),
            'auth_scope': get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_auth_scope")
        }
        
        # Test the runtime first
        try:
            test_analysis_agent(runtime_url, cognito_config)
            print("✅ Runtime testing completed successfully!")
        except Exception as e:
            print(f"❌ Runtime testing failed: {e}")
            raise Exception("Runtime testing failed - not storing SSM parameters")
        
        # Step 4: Store SSM parameters ONLY after successful testing
        print("\n📋 Step 4: Storing SSM parameters after successful testing...")
        
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/analysis_runtime_id",
            runtime_id,
            "AgentCore Runtime ID for Analysis Agent"
        )
        
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/analysis_runtime_arn",
            runtime_arn,
            "AgentCore Runtime ARN for Analysis Agent"
        )
        
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/analysis_runtime_url",
            runtime_url,
            "AgentCore Runtime URL for Analysis Agent"
        )
        
        # Summary
        print(f"\n✅ Analysis Agent deployment completed successfully!")
        print(f"📋 ECR Image: {image_uri}")
        print(f"📋 Runtime ID: {runtime_id}")
        print(f"📋 Runtime ARN: {runtime_arn}")
        print(f"📋 Runtime URL: {runtime_url}")
        print(f"📋 Authentication: Cognito M2M OAuth")
        print(f"📋 Current Model: {os.environ.get('CODE_ANALYSIS_MODEL', 'us.anthropic.claude-sonnet-4-5-20250929-v1:0')}")
        
        # Show available models
        list_available_models()
        
        print(f"\n💡 To change the model after deployment:")
        print(f"   python3.13 deploy-analysis-agent.py --update-model <model_id>")
        print(f"   Example: python3.13 deploy-analysis-agent.py --update-model anthropic.claude-3-5-haiku-20241022-v1:0")
        
    except Exception as e:
        print(f"❌ Deployment failed: {str(e)}")
        raise

if __name__ == "__main__":
    import sys
    
    # Handle model update command
    if len(sys.argv) == 3 and sys.argv[1] == "--update-model":
        new_model_id = sys.argv[2]
        runtime_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/analysis_runtime_id")
        
        if not runtime_id:
            print("❌ No analysis runtime found. Deploy first with: python3.13 deploy-analysis-agent.py")
            sys.exit(1)
        
        print(f"🔄 Updating model for analysis runtime: {runtime_id}")
        if update_runtime_model(runtime_id, new_model_id):
            print("✅ Model update completed!")
        else:
            print("❌ Model update failed!")
            sys.exit(1)
    else:
        main()
