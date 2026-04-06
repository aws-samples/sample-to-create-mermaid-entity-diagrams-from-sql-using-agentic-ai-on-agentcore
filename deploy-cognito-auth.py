#!/usr/bin/env python3.13
"""
Deploy Cognito OAuth authentication for ER Diagram AgentCore Runtime
Creates Cognito User Pool, App Client, and stores configuration in SSM
"""

import boto3
import json
import uuid
from botocore.exceptions import ClientError

# Configuration
REGION = "us-west-2"
PROJECT_NAME = "erdiagfromsql"

def get_account_id():
    """Get current AWS account ID"""
    sts = boto3.client('sts', region_name=REGION)
    return sts.get_caller_identity()['Account']

def store_ssm_parameter(name: str, value: str, description: str):
    """Store parameter in SSM Parameter Store"""
    ssm = boto3.client('ssm', region_name=REGION)
    try:
        ssm.put_parameter(
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
    ssm = boto3.client('ssm', region_name=REGION)
    try:
        response = ssm.get_parameter(Name=name, WithDecryption=True)
        return response['Parameter']['Value']
    except Exception:
        return None

def create_cognito_oauth_resources():
    """
    Create Cognito User Pool and App Client for AgentCore Runtime OAuth authentication
    """
    cognito_client = boto3.client('cognito-idp', region_name=REGION)
    user_pool_name = f"{PROJECT_NAME}RuntimePool"
    machine_client_name = f"{PROJECT_NAME}MachineClient"
    
    # Generate unique identifier for resource server
    unique_id = str(uuid.uuid4())[:8]
    resource_server_id = f"erdiagram-runtime-{unique_id}"
    
    try:
        # Check if user pool already exists
        existing_pool_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/userpool_id")
        if existing_pool_id:
            try:
                cognito_client.describe_user_pool(UserPoolId=existing_pool_id)
                print(f"✅ Found existing User Pool: {existing_pool_id}")
                user_pool_id = existing_pool_id
            except:
                print("Existing pool ID invalid, creating new pool")
                existing_pool_id = None
        
        if not existing_pool_id:
            # Create User Pool
            print(f"Creating User Pool: {user_pool_name}")
            user_pool_response = cognito_client.create_user_pool(
                PoolName=user_pool_name,
                MfaConfiguration='OFF',
                UsernameConfiguration={'CaseSensitive': False},
                UsernameAttributes=['email'],
                AutoVerifiedAttributes=['email']
            )
            user_pool_id = user_pool_response['UserPool']['Id']
            print(f"✅ Created User Pool: {user_pool_id}")
        
        # Create Resource Server (required for scopes)
        print(f"Creating Resource Server: {resource_server_id}")
        try:
            resource_server_response = cognito_client.create_resource_server(
                UserPoolId=user_pool_id,
                Identifier=resource_server_id,
                Name=f"ER Diagram Runtime Resource Server {unique_id}",
                Scopes=[
                    {
                        'ScopeName': 'read',
                        'ScopeDescription': 'Read access for ER diagram generation'
                    }
                ]
            )
            print(f"✅ Created Resource Server: {resource_server_id}")
        except cognito_client.exceptions.ResourceNotFoundException:
            print(f"⚠️ Resource server already exists")
        
        # Create Machine App Client for client_credentials flow
        print(f"Creating Machine Client: {machine_client_name}")
        machine_client_response = cognito_client.create_user_pool_client(
            UserPoolId=user_pool_id,
            ClientName=machine_client_name,
            GenerateSecret=True,
            ExplicitAuthFlows=['ALLOW_REFRESH_TOKEN_AUTH'],
            RefreshTokenValidity=1,
            AccessTokenValidity=60,
            IdTokenValidity=60,
            TokenValidityUnits={
                'AccessToken': 'minutes',
                'IdToken': 'minutes',
                'RefreshToken': 'days'
            },
            AllowedOAuthFlows=['client_credentials'],
            AllowedOAuthScopes=[f"{resource_server_id}/read"],
            AllowedOAuthFlowsUserPoolClient=True,
            SupportedIdentityProviders=['COGNITO'],
            EnableTokenRevocation=True
        )
        
        machine_client_id = machine_client_response['UserPoolClient']['ClientId']
        machine_client_secret = machine_client_response['UserPoolClient']['ClientSecret']
        print(f"✅ Created Machine Client: {machine_client_id}")
        
        # Create User Pool Domain
        domain_name = f"{PROJECT_NAME}{REGION}{unique_id}".replace('-', '').lower()[:63]
        try:
            cognito_client.create_user_pool_domain(
                Domain=domain_name,
                UserPoolId=user_pool_id
            )
            print(f"✅ Created User Pool Domain: {domain_name}")
        except cognito_client.exceptions.InvalidParameterException:
            print(f"⚠️ Domain already exists: {domain_name}")
        
        # Build URLs
        cognito_domain = f"https://{domain_name}.auth.{REGION}.amazoncognito.com"
        token_url = f"{cognito_domain}/oauth2/token"
        auth_url = f"{cognito_domain}/oauth2/authorize"
        discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
        auth_scope = f"{resource_server_id}/read"
        
        # Store all configuration in SSM Parameter Store
        ssm_params = {
            f"/app/{PROJECT_NAME}/agentcore/machine_client_id": (machine_client_id, "Machine Cognito client ID for ER diagram runtime"),
            f"/app/{PROJECT_NAME}/agentcore/machine_client_secret": (machine_client_secret, "Machine Cognito client Secret"),
            f"/app/{PROJECT_NAME}/agentcore/userpool_id": (user_pool_id, "Cognito User Pool ID"),
            f"/app/{PROJECT_NAME}/agentcore/cognito_auth_scope": (auth_scope, "OAuth2 scope for Cognito auth"),
            f"/app/{PROJECT_NAME}/agentcore/cognito_discovery_url": (discovery_url, "OAuth2 Discovery URL"),
            f"/app/{PROJECT_NAME}/agentcore/cognito_token_url": (token_url, "OAuth2 Token URL"),
            f"/app/{PROJECT_NAME}/agentcore/cognito_auth_url": (auth_url, "OAuth2 Authorize URL"),
            f"/app/{PROJECT_NAME}/agentcore/cognito_domain": (cognito_domain, "Cognito hosted domain for OAuth2")
        }
        
        print("\n📋 Storing configuration in SSM Parameter Store...")
        for param_name, (param_value, param_desc) in ssm_params.items():
            store_ssm_parameter(param_name, param_value, param_desc)
        
        print(f"\n✅ Cognito OAuth setup completed successfully!")
        print(f"📋 User Pool ID: {user_pool_id}")
        print(f"📋 Machine Client ID: {machine_client_id}")
        print(f"📋 OAuth Domain: {cognito_domain}")
        print(f"📋 Auth Scope: {auth_scope}")
        print(f"📋 Token URL: {token_url}")
        
        return {
            'user_pool_id': user_pool_id,
            'machine_client_id': machine_client_id,
            'machine_client_secret': machine_client_secret,
            'auth_scope': auth_scope,
            'token_url': token_url,
            'cognito_domain': cognito_domain,
            'discovery_url': discovery_url
        }
        
    except Exception as e:
        print(f"❌ Error creating Cognito resources: {e}")
        raise

def create_agentcore_oauth2_credential_provider():
    """Create OAuth2 credential provider for AgentCore"""
    try:
        agentcore_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
        
        # Get Cognito configuration from SSM
        machine_client_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_id")
        machine_client_secret = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_secret")
        discovery_url = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_discovery_url")
        
        if not all([machine_client_id, machine_client_secret, discovery_url]):
            print("❌ Missing Cognito configuration for OAuth2 provider.")
            return False
        
        # Check if provider already exists
        try:
            providers = agentcore_client.list_oauth2_credential_providers()
            existing_providers = [p['name'] for p in providers.get('oauth2CredentialProviders', [])]
            
            if "cognitoerdiag" in existing_providers:
                print("✅ OAuth2 credential provider already exists")
                return True
        except Exception as e:
            print(f"⚠️ Could not check existing providers: {e}")
        
        # Create OAuth2 credential provider
        agentcore_client.create_oauth2_credential_provider(
            name="cognitoerdiag",
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                'customOauth2ProviderConfig': {
                    'oauthDiscovery': {
                        'discoveryUrl': discovery_url
                    },
                    'clientId': machine_client_id,
                    'clientSecret': machine_client_secret
                }
            }
        )
        print("✅ Created OAuth2 credential provider: cognitoerdiag")
        return True
        
    except Exception as e:
        print(f"❌ Error creating OAuth2 credential provider: {e}")
        return False

def test_oauth_token(config):
    """Test OAuth token retrieval"""
    import requests
    import base64
    
    print("\n🧪 Testing OAuth token retrieval...")
    
    # Encode client credentials
    credentials = base64.b64encode(
        f"{config['machine_client_id']}:{config['machine_client_secret']}".encode()
    ).decode()
    
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = {
        "grant_type": "client_credentials",
        "scope": config['auth_scope']
    }
    
    try:
        response = requests.post(config['token_url'], headers=headers, data=data)
        
        if response.status_code == 200:
            token_data = response.json()
            print(f"✅ OAuth token obtained successfully")
            print(f"📋 Token Type: {token_data.get('token_type')}")
            print(f"📋 Expires In: {token_data.get('expires_in')} seconds")
            return token_data.get('access_token')
        else:
            print(f"❌ Failed to get OAuth token: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Error testing OAuth: {e}")
        return None

def main():
    """Main deployment function"""
    print("🚀 Starting Cognito OAuth deployment for ER Diagram Runtime...")
    print(f"📋 Project: {PROJECT_NAME}")
    print(f"📋 Region: {REGION}")
    print("=" * 60)
    
    try:
        # Create Cognito OAuth resources
        print("\n📋 Step 1: Creating Cognito OAuth resources...")
        config = create_cognito_oauth_resources()
        
        # Test OAuth token retrieval
        print("\n📋 Step 2: Testing OAuth token retrieval...")
        token = test_oauth_token(config)
        
        if token:
            print("\n✅ All tests passed!")
        else:
            print("\n⚠️ OAuth token test failed, but resources are created")
        
        # Display SSM parameters
        print(f"\n📋 Step 3: SSM Parameters stored:")
        print(f"  Base path: /app/{PROJECT_NAME}/agentcore/")
        print(f"  - machine_client_id")
        print(f"  - machine_client_secret")
        print(f"  - userpool_id")
        print(f"  - cognito_auth_scope")
        print(f"  - cognito_discovery_url")
        print(f"  - cognito_token_url")
        print(f"  - cognito_auth_url")
        print(f"  - cognito_domain")
        
        # Summary
        print(f"\n" + "=" * 60)
        print(f"✅ Cognito OAuth deployment completed!")
        print(f"📋 User Pool ID: {config['user_pool_id']}")
        print(f"📋 Client ID: {config['machine_client_id']}")
        print(f"📋 Token URL: {config['token_url']}")
        print(f"📋 Auth Scope: {config['auth_scope']}")
        
        # Create AgentCore OAuth2 credential provider
        print(f"\n📋 Step 4: Creating AgentCore OAuth2 credential provider...")
        if create_agentcore_oauth2_credential_provider():
            print("✅ AgentCore OAuth2 credential provider ready")
        else:
            print("⚠️ AgentCore OAuth2 credential provider creation failed")
        
        print(f"\n💡 Next steps:")
        print(f"  1. Deploy AgentCore Runtime with these credentials")
        print(f"  2. Deploy trigger Lambda to call the runtime")
        print(f"  3. Test end-to-end SQL file upload → ER diagram generation")
        
    except Exception as e:
        print(f"\n❌ Deployment failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()
