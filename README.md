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

## Prerequisites

- Python 3.13
- Docker (running locally — used to build the agent container image for `linux/arm64`)
- AWS CLI installed and configured with credentials and sufficient permissions:
  - Cognito, SSM, ECR, Bedrock AgentCore, IAM, Lambda, S3, STS
  - [Install the AWS CLI](https://docs.aws.amazon.com/cli/v1/userguide/cli-chap-install.html)
  - [Authenticate using IAM user credentials](https://docs.aws.amazon.com/cli/latest/userguide/cli-authentication-user.html)
- Bedrock model access: enable `claude-sonnet-4-5` in `us-west-2` via the AWS Console

## Setup

```bash
# Create and activate a virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Deployment Order

### 1. Cognito OAuth
```bash
python3.13 deploy-cognito-auth.py
```
Creates Cognito User Pool with M2M client credentials flow. Stores client ID, secret, and token URLs in SSM Parameter Store under `/app/erdiagfromsql/agentcore/`.

Wait 10-15 min for DNS propagation before proceeding.

### 2. AgentCore Memory
```bash
python3.13 deploy-agentcore-memory.py
```
Creates AgentCore Memory with 90-day expiry for storing SQL analysis context. Enables semantic search, summaries, and user preferences. Takes 2-3 minutes to provision.

### 3. AgentCore Runtime
```bash
python3.13 deploy-erdiag-agent.py --s3-bucket <your-s3-bucket-name>
```
Example:
```bash
python3.13 deploy-erdiag-agent.py --s3-bucket my-erdiagram-bucket
```

Builds a Docker image, pushes it to ECR, and deploys it as an AgentCore Runtime. The agent:
- Parses SQL DDL statements (CREATE TABLE, constraints, foreign keys)
- Generates Mermaid ER diagram syntax
- Saves `.mmd` files to `s3://<bucket>/erdiags/`

After deployment the script sends a test SQL payload. On success you will see:

```text
SQL Analysis - Status Code: 200
✅ SQL analysis completed successfully!
📋 Action: CREATE
📋 Tables: 4
```

**To redeploy after code changes**, use the `--rebuild` flag to force a fresh Docker image build:
```bash
python3.13 deploy-erdiag-agent.py --s3-bucket <your-s3-bucket-name> --rebuild
```

**To update the LLM model after deployment:**
```bash
python3.13 deploy-erdiag-agent.py --update-model <model-id>
```

### 4. Trigger Lambda
```bash
python3.13 deploy-trigger-lambda.py --s3-bucket <your-s3-bucket-name> --bucket-region <region>
```
Example:
```bash
python3.13 deploy-trigger-lambda.py --s3-bucket my-erdiagram-bucket --bucket-region us-west-2
```

Creates a Lambda function that:
- Triggers when `.sql` files are uploaded to the specified S3 bucket
- Authenticates via Cognito OAuth (client credentials flow)
- Calls AgentCore runtime to generate Mermaid ER diagrams
- Includes exponential backoff retry for AgentCore rate limits (4 req/min)

## Testing End-to-End

Upload any `.sql` file to your S3 bucket:
```bash
aws s3 cp my-schema.sql s3://<your-bucket>/
```

The Lambda triggers automatically. Check for the generated diagram:
```bash
aws s3 ls s3://<your-bucket>/erdiags/
```

You should see a `.mmd` file appear within ~30 seconds.

## Notes

- All deploy scripts default to `us-west-2`. The region is hardcoded at the top of each script.
- All configuration is stored in SSM Parameter Store under `/app/erdiagfromsql/agentcore/`.
- The `--rebuild` flag on `deploy-erdiag-agent.py` is required any time `erdiag-agent.py` is changed, to force a fresh Docker image build instead of reusing the cached ECR image.
