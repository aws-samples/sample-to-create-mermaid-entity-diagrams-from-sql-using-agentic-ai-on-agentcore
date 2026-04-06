# SQL to ER Diagram Generator

Generates Mermaid ER diagrams from SQL schema files using AWS Bedrock AgentCore.

## Architecture

![Architecture Diagram](architecture-diagram.drawio.png)

## Sample Output

![Sample Mermaid ER Diagram](mermaid-erdiagram.png)

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
python3.13 deploy-agentcore-memory.py
```

### 3. AgentCore Runtime
```bash
python3.13 deploy-erdiag-agent.py
```

**Critical:** Runtime code must have:
- `provider_name="cognitoerdiag"` (matches OAuth2 credential provider)
- `auth_flow="M2M"`

### 4. Trigger Lambda
```bash
python3.13 deploy-trigger-lambda.py <bucket_name> <bucket_region>
```
Example:
```bash
python3.13 deploy-trigger-lambda.py my-sql-bucket us-west-2
```

This creates a Lambda function that:
- Triggers when `.sql` files are uploaded to the specified S3 bucket
- Authenticates via Cognito OAuth
- Calls AgentCore runtime to generate Mermaid ER diagrams

## Redeploy Runtime Only
```bash
python3.13 deploy-erdiag-agent.py
```
- deploy script also sends a test payload after deployment and if successful agent will create a mermaid diagram on S3:
	- "test_schema_1761696285.txt"
	- "mermaid-erdiagram.png"
