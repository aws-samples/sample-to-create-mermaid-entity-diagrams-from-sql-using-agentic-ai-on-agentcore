# SQL to ER Diagram Generator

Generates Mermaid ER diagrams from SQL schema files using AWS Bedrock AgentCore.

## Architecture

![Architecture Diagram](architecture-diagram.drawio.png)

## Sample Output

![Sample Mermaid ER Diagram](trigger-lambda/mermaid-erdiagram.png)

## What's Working
1. ✅ Cognito OAuth (deployed)
2. ✅ AgentCore Memory (deployed)
3. ✅ AgentCore Runtime (deployed and tested)
4. ✅ Trigger Lambda (deployed - triggers on S3 SQL file uploads)

## Deployment Order

### 1. Cognito OAuth
```bash
python3.13 deploy-cognito-auth.py
```
Wait 10-15 min for DNS propagation.

### 2. AgentCore Memory
```bash
cd agentcore-memory && python3.13 deploy-agentcore-memory.py && cd ..
```

### 3. AgentCore Runtime
```bash
cd erdiag-runtime && python3.13 deploy-erdiag-agent.py && cd ..
```

**Critical:** Runtime code must have:
- `provider_name="cognitoerdiag"` (matches OAuth2 credential provider)
- `auth_flow="M2M"`

### 4. Trigger Lambda
```bash
cd trigger-lambda && python3.13 deploy-trigger-lambda.py <bucket_name> <bucket_region> && cd ..
```
Example:
```bash
cd trigger-lambda && python3.13 deploy-trigger-lambda.py my-sql-bucket us-west-2 && cd ..
```

This creates a Lambda function that:
- Triggers when `.sql` files are uploaded to the specified S3 bucket
- Authenticates via Cognito OAuth
- Calls AgentCore runtime to generate Mermaid ER diagrams

## Redeploy Runtime Only
```bash
cd erdiag-runtime && python3.13 deploy-erdiag-agent.py
```
- deploy script also sends a test payload after deployment and if successful agent will create a mermaid diagram on S3:
	- "test_schema_1761696285.txt"
	- "mermaid-erdiagram.png"
