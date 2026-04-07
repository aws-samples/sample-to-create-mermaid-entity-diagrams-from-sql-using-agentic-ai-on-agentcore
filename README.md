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

## Project Files

| File | Description |
|------|-------------|
| `deploy-cognito-auth.py` | Creates Cognito User Pool, App Client, and OAuth2 credential provider for M2M authentication |
| `deploy-agentcore-memory.py` | Deploys AgentCore Memory resource with semantic, summary, and user preference strategies |
| `deploy-erdiag-agent.py` | Deploys the AgentCore Runtime that hosts the ER diagram generation agent |
| `erdiag-agent.py` | The agent code - uses Claude Sonnet 4.5 to parse SQL and generate Mermaid ER diagrams |
| `deploy-trigger-lambda.py` | Creates Lambda function and S3 trigger for automatic processing of uploaded SQL files |
| `trigger-lambda.py` | Lambda handler that reads SQL from S3, authenticates via Cognito, and calls AgentCore runtime |

## Deployment Order

### 1. Cognito OAuth
```bash
python3.13 deploy-cognito-auth.py
```
Creates Cognito User Pool with M2M client credentials flow. Stores client ID, secret, and token URLs in SSM Parameter Store under `/app/erdiagfromsql/agentcore/`.

Wait 10-15 min for DNS propagation.

### 2. AgentCore Memory
```bash
python3.13 deploy-agentcore-memory.py
```
Creates AgentCore Memory with 90-day expiry for storing SQL analysis context. Enables semantic search, summaries, and user preferences.

### 3. AgentCore Runtime
```bash
python3.13 deploy-erdiag-agent.py
```
Deploys `erdiag-agent.py` as an AgentCore Runtime. The agent:
- Parses SQL DDL statements (CREATE TABLE, constraints, foreign keys)
- Generates Mermaid ER diagram syntax
- Renders diagram as PNG and uploads to S3

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

Creates a Lambda function that:
- Triggers when `.sql` files are uploaded to the specified S3 bucket
- Authenticates via Cognito OAuth (client credentials flow)
- Calls AgentCore runtime to generate Mermaid ER diagrams
- Includes exponential backoff retry for AgentCore rate limits (4 req/min)

## Redeploy Runtime Only
```bash
python3.13 deploy-erdiag-agent.py
```
- deploy script also sends a test payload after deployment and if successful agent will create a mermaid diagram on S3:
	- "test_schema_1761696285.txt"
	- "mermaid-erdiagram.png"
