#!/usr/bin/env python3.13
"""
S3 Trigger Lambda for SQL ER Diagram Generation
Triggered when SQL files are uploaded to S3
Calls Bedrock AgentCore to generate Mermaid UML diagrams
"""

import json
import boto3
import urllib3
import urllib.parse
import base64
import os
import logging
import time
import random
from typing import Dict, List, Any, Optional

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
PROJECT_NAME = os.environ.get('PROJECT_NAME', 'erdiagfromsql')
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-west-2')

# Initialize urllib3
http = urllib3.PoolManager()

def exponential_backoff_retry(func, max_retries=3, base_delay=1.0, max_delay=60.0):
    """
    Retry function with exponential backoff for rate limiting
    AgentCore has 4 requests per minute limit
    """
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if result is not None:
                return result
            
            if attempt < max_retries:
                # Calculate delay with jitter
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                logger.info(f"Attempt {attempt + 1} failed, retrying in {delay:.2f} seconds...")
                time.sleep(delay)
            
        except Exception as e:
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                logger.warning(f"Attempt {attempt + 1} failed with error: {e}, retrying in {delay:.2f} seconds...")
                time.sleep(delay)
            else:
                logger.error(f"All {max_retries + 1} attempts failed. Last error: {e}")
                return None
    
    return None

def get_ssm_parameter(name: str) -> Optional[str]:
    """Get SSM parameter value with retry logic"""
    def make_request():
        try:
            ssm_client = boto3.client('ssm', region_name=REGION)
            response = ssm_client.get_parameter(Name=name, WithDecryption=True)
            logger.info(f"Successfully retrieved SSM parameter: {name}")
            return response['Parameter']['Value']
        except ssm_client.exceptions.ThrottlingException:
            logger.warning(f"SSM throttled for parameter: {name}")
            return None  # Trigger retry
        except Exception as e:
            logger.error(f"Failed to get SSM parameter {name}: {e}")
            return None
    
    # Use exponential backoff retry for SSM rate limiting
    return exponential_backoff_retry(make_request, max_retries=3, base_delay=15.0)

def get_cognito_bearer_token() -> Optional[str]:
    """Get bearer token from Cognito for authentication"""
    try:
        # Get Cognito configuration
        client_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/machine_client_id")
        token_url = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_token_url")
        auth_scope = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_auth_scope")
        user_pool_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/userpool_id")
        
        if not all([client_id, token_url, auth_scope, user_pool_id]):
            logger.error("Missing Cognito configuration")
            return None
        
        # Get client secret from Cognito
        cognito_client = boto3.client('cognito-idp', region_name=REGION)
        client_response = cognito_client.describe_user_pool_client(
            UserPoolId=user_pool_id,
            ClientId=client_id
        )
        client_secret = client_response['UserPoolClient']['ClientSecret']
        
        # Get token using client credentials flow
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
        
        logger.info(f"Getting OAuth token from: {token_url}")
        
        # Prepare form data
        form_data = urllib.parse.urlencode({
            'grant_type': 'client_credentials',
            'scope': auth_scope
        })
        
        response = http.request(
            'POST',
            token_url,
            body=form_data,
            headers={
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            timeout=30
        )
        
        if response.status == 200:
            token_data = json.loads(response.data.decode('utf-8'))
            logger.info("Successfully obtained bearer token")
            return token_data.get('access_token')
        else:
            logger.error(f"Token request failed: {response.status} - {response.data.decode('utf-8')}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting bearer token: {e}")
        return None

def read_s3_file(bucket: str, key: str) -> Optional[str]:
    """Read file content from S3"""
    try:
        s3_client = boto3.client('s3', region_name=REGION)
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        logger.info(f"Successfully read S3 file: s3://{bucket}/{key}")
        return content
    except Exception as e:
        logger.error(f"Error reading S3 file s3://{bucket}/{key}: {e}")
        return None

def generate_er_diagram_with_agent(sql_content: str, file_name: str, bearer_token: str) -> Optional[Dict]:
    """Call Bedrock Agent to generate ER diagram from SQL"""
    try:
        # Get analysis runtime URL
        runtime_url = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/analysis_runtime_url")
        if not runtime_url:
            logger.error("Analysis runtime URL not found")
            return None
        
        # Prepare payload matching agent's expected parameters
        payload = {
            "sql_content": sql_content,
            "file_name": file_name,
            "session_id": f"session_{int(time.time())}",
            "actor_id": "trigger_lambda"
        }
        
        def make_request():
            logger.info(f"Calling Bedrock Agent for ER diagram: {runtime_url}")
            
            response = http.request(
                'POST',
                runtime_url,
                body=json.dumps(payload),
                headers={
                    'Authorization': f'Bearer {bearer_token}',
                    'Content-Type': 'application/json'
                },
                timeout=60
            )
            
            if response.status == 200:
                result = json.loads(response.data.decode('utf-8'))
                logger.info("ER diagram generation completed successfully")
                return result
            elif response.status == 429:  # Rate limited
                logger.warning(f"Bedrock Agent rate limited: {response.status}")
                return None  # Trigger retry
            else:
                logger.error(f"Bedrock Agent failed: {response.status} - {response.data.decode('utf-8')}")
                return None
        
        # Use exponential backoff retry for rate limiting
        return exponential_backoff_retry(make_request, max_retries=3, base_delay=15.0)
            
    except Exception as e:
        logger.error(f"Error calling Bedrock Agent: {e}")
        return None

def lambda_handler(event, context):
    """Main Lambda handler for S3 trigger"""
    try:
        logger.info(f"Lambda triggered with event: {json.dumps(event)}")
        
        # Parse S3 event
        for record in event.get('Records', []):
            if record.get('eventSource') != 'aws:s3':
                continue
                
            bucket = record['s3']['bucket']['name']
            key = urllib.parse.unquote_plus(record['s3']['object']['key'])
            
            logger.info(f"Processing S3 object: s3://{bucket}/{key}")
            
            # Only process SQL files (case-insensitive)
            if not key.lower().endswith('.sql'):
                logger.info(f"Skipping non-SQL file: {key}")
                continue
            
            # Get OAuth token
            bearer_token = get_cognito_bearer_token()
            if not bearer_token:
                logger.error("Failed to get authentication token")
                continue
            
            # Read SQL file
            sql_content = read_s3_file(bucket, key)
            if not sql_content:
                logger.error(f"Failed to read SQL file: {key}")
                continue
            
            # Initialize results
            analysis_results = {
                'file_name': key,
                'bucket': bucket,
                'timestamp': context.aws_request_id,
                'action': None,
                's3_path': None,
                'tables': [],
                'overall_status': 'processing'
            }
            
            # Generate ER diagram with Bedrock Agent
            logger.info("Starting ER diagram generation...")
            er_result = generate_er_diagram_with_agent(sql_content, key, bearer_token)
            if er_result:
                analysis_results['action'] = er_result.get('action')
                analysis_results['s3_path'] = er_result.get('s3_path')
                analysis_results['tables'] = er_result.get('tables', [])
                analysis_results['mermaid_diagram'] = er_result.get('mermaid_diagram', '')
                analysis_results['overall_status'] = 'completed'
                logger.info(f"ER diagram generation completed - Action: {er_result.get('action')}, S3: {er_result.get('s3_path')}")
            else:
                analysis_results['overall_status'] = 'failed'
            
            # Log comprehensive results
            logger.info("=== ER DIAGRAM GENERATION COMPLETE ===")
            logger.info(f"File: {key}")
            logger.info(f"Action: {analysis_results.get('action')}")
            logger.info(f"S3 Path: {analysis_results.get('s3_path')}")
            logger.info(f"Tables: {', '.join(analysis_results.get('tables', []))}")
            logger.info(f"Overall Status: {analysis_results['overall_status']}")
            logger.info(f"Full Results: {json.dumps(analysis_results, indent=2)}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'ER diagram generation completed',
                'processed_files': len(event.get('Records', []))
            })
        }
        
    except Exception as e:
        logger.error(f"Lambda execution failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }
