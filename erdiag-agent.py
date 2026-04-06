#!/usr/bin/env python3.13
import json
import time
import logging
import boto3
import os
import asyncio
from typing import Dict, List, Any, Optional
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.identity.auth import requires_access_token
from strands import Agent
from strands.models import BedrockModel
from opentelemetry import baggage, context, trace
from opentelemetry.trace import Status, StatusCode

# Configure CloudWatch logging with verbose output for troubleshooting
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
)

logger = logging.getLogger(__name__)

# Configuration
REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-west-2')
PROJECT_NAME = os.environ.get('PROJECT_NAME', 'erdiagfromsql')
ER_DIAGRAM_MODEL = os.environ.get('ER_DIAGRAM_MODEL', 'us.anthropic.claude-sonnet-4-5-20250929-v1:0')
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', '')

def get_ssm_parameter(parameter_name: str) -> Optional[str]:
    """Retrieve SSM parameter"""
    try:
        ssm_client = boto3.client('ssm', region_name=REGION)
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=True)
        return response['Parameter']['Value']
    except Exception as e:
        logger.warning(f"Failed to retrieve SSM parameter {parameter_name}: {e}")
        return None

# Get OAuth2 scope from SSM
try:
    OAUTH2_SCOPE = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/cognito_auth_scope")
    if not OAUTH2_SCOPE:
        OAUTH2_SCOPE = "erdiagram/read"
        logger.warning("Using fallback OAuth2 scope: erdiagram/read")
    else:
        logger.info(f"Using OAuth2 scope from SSM: {OAUTH2_SCOPE}")
except Exception as e:
    OAUTH2_SCOPE = "erdiagram/read"
    logger.error(f"Error getting OAuth2 scope from SSM: {e}")

# Get memory configuration
MEMORY_ID = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/memory_id")

# Initialize OpenTelemetry tracer
tracer = trace.get_tracer(__name__)

# Initialize AgentCore Runtime App
app = BedrockAgentCoreApp()

# Initialize Strands Agent with configurable model
model = BedrockModel(model_id=ER_DIAGRAM_MODEL, region_name=REGION)
erdiagram_agent = Agent(model=model)

# Initialize Memory Client
memory_client = MemoryClient(region_name=REGION)

def save_mermaid_to_s3(file_name: str, mermaid_content: str, action: str) -> Optional[str]:
    """Save Mermaid diagram to S3 bucket under erdiags/ folder"""
    if not S3_BUCKET_NAME:
        logger.warning("S3_BUCKET_NAME not configured, skipping S3 save")
        return None
    
    try:
        s3_client = boto3.client('s3', region_name=REGION)
        
        # Generate S3 key: erdiags/<filename_without_extension>_<timestamp>.mmd
        base_name = file_name.replace('.sql', '').replace('.SQL', '')
        timestamp = int(time.time())
        s3_key = f"erdiags/{base_name}_{timestamp}.mmd"
        
        # Save Mermaid content to S3
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=mermaid_content.encode('utf-8'),
            ContentType='text/plain',
            Metadata={
                'action': action,
                'source_file': file_name,
                'generated_at': str(timestamp)
            }
        )
        
        s3_path = f"s3://{S3_BUCKET_NAME}/{s3_key}"
        logger.info(f"Saved Mermaid diagram to {s3_path}")
        return s3_path
        
    except Exception as e:
        logger.error(f"Failed to save Mermaid diagram to S3: {e}")
        return None

def chunk_sql_content(sql_content: str, max_chunk_size: int = 80000) -> List[str]:
    """Split large SQL content into chunks for LLM processing"""
    if len(sql_content) <= max_chunk_size:
        return [sql_content]
    
    chunks = []
    lines = sql_content.split('\n')
    current_chunk = ""
    
    for line in lines:
        if len(current_chunk + line + '\n') > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.rstrip())
                current_chunk = line + '\n'
            else:
                chunks.append(line)
        else:
            current_chunk += line + '\n'
    
    if current_chunk:
        chunks.append(current_chunk.rstrip())
    
    return chunks

async def generate_er_diagram_from_sql(sql_content: str, file_name: str) -> Dict[str, Any]:
    """Generate Mermaid ER diagram from SQL using Claude Sonnet 4.5 with OpenTelemetry tracing"""
    
    logger.debug(f"=== GENERATE_ER_DIAGRAM STARTED === file={file_name}")
    
    with tracer.start_as_current_span("generate_er_diagram_from_sql") as span:
        span.set_attribute("file.name", file_name)
        span.set_attribute("sql.size", len(sql_content))
        span.set_attribute("sql.lines", len(sql_content.split('\n')) if sql_content else 0)
        
        logger.debug(f"ER diagram generation span created, SQL size: {len(sql_content)} chars")
    
    er_diagram_prompt = f"""You are an expert database architect. Analyze the following SQL schema and generate a Mermaid ER diagram.

<thinking>
Let me analyze this SQL systematically:

1. **Identify Tables**
   - Extract all CREATE TABLE statements
   - Note table names and their columns

2. **Identify Columns and Data Types**
   - List all columns with their data types
   - Note primary keys and unique constraints

3. **Identify Relationships**
   - Find FOREIGN KEY constraints
   - Determine relationship types (one-to-one, one-to-many, many-to-many)
   - Note referential actions (CASCADE, SET NULL, etc.)

4. **Determine Action Required**
   - If this is a new schema: CREATE
   - If modifying existing tables: UPDATE
   - If no changes needed: NO_UPDATE

Let me extract the entities and relationships to build the ER diagram.
</thinking>

**File:** {file_name}

**SQL Schema:**
```sql
{sql_content}
```

Please provide:

1. **Action:** One of: CREATE, UPDATE, or NO_UPDATE
2. **Mermaid ER Diagram:** Complete Mermaid erDiagram syntax
3. **Summary:** Brief description of entities and relationships
4. **Tables:** List of table names identified

Format your response as:

**Action:** [CREATE|UPDATE|NO_UPDATE]

**Mermaid Diagram:**
```mermaid
erDiagram
    [Your complete ER diagram here]
```

**Summary:**
[Brief description of the schema]

**Tables:**
- [Table 1]
- [Table 2]
- [etc.]
"""

    try:
        # Process SQL in chunks if needed
        sql_chunks = chunk_sql_content(sql_content)
        span.set_attribute("sql.chunks", len(sql_chunks))
        
        if len(sql_chunks) > 1:
            logger.info(f"Processing {len(sql_chunks)} chunks for {file_name}")
            
            # Analyze each chunk with tracing
            chunk_diagrams = []
            for i, chunk in enumerate(sql_chunks):
                with tracer.start_as_current_span(f"analyze_chunk_{i+1}") as chunk_span:
                    chunk_span.set_attribute("chunk.index", i+1)
                    chunk_span.set_attribute("chunk.size", len(chunk))
                    
                    chunk_prompt = er_diagram_prompt.replace(sql_content, chunk)
                    chunk_prompt = chunk_prompt.replace(f"**File:** {file_name}", f"**File:** {file_name} (Chunk {i+1}/{len(sql_chunks)})")
                    
                    result = await erdiagram_agent.invoke_async(chunk_prompt)
                    chunk_diagrams.append(str(result))
            
            # Consolidate diagrams with tracing
            with tracer.start_as_current_span("consolidate_diagram") as consolidate_span:
                consolidate_span.set_attribute("chunks.count", len(sql_chunks))
                
                consolidation_prompt = f"""Consolidate the following {len(sql_chunks)} ER diagram chunks for {file_name}:

{chr(10).join([f"Chunk {i+1}: {diagram}" for i, diagram in enumerate(chunk_diagrams)])}

Provide a unified response with:
1. **Action:** Overall action (CREATE/UPDATE/NO_UPDATE)
2. **Mermaid Diagram:** Complete consolidated ER diagram
3. **Summary:** Combined description
4. **Tables:** All tables across chunks
"""
                
                result = await erdiagram_agent.invoke_async(consolidation_prompt)
                response_text = str(result)
        else:
            # Single chunk analysis with tracing
            with tracer.start_as_current_span("single_chunk_diagram") as single_span:
                single_span.set_attribute("diagram.type", "single_chunk")
                
                result = await erdiagram_agent.invoke_async(er_diagram_prompt)
                response_text = str(result)
        
        # Extract action type
        action = "CREATE"  # default
        if "**Action:**" in response_text:
            action_line = response_text.split("**Action:**")[1].split('\n')[0].strip()
            if "UPDATE" in action_line.upper():
                action = "UPDATE"
            elif "NO_UPDATE" in action_line.upper() or "NO UPDATE" in action_line.upper():
                action = "NO_UPDATE"
        
        # Extract Mermaid diagram
        mermaid_diagram = ""
        if "```mermaid" in response_text:
            mermaid_section = response_text.split("```mermaid")[1].split("```")[0].strip()
            mermaid_diagram = mermaid_section
        
        # Extract tables
        tables = []
        if "**Tables:**" in response_text:
            tables_section = response_text.split("**Tables:**")[1]
            if "**" in tables_section:
                tables_section = tables_section.split("**")[0]
            
            for line in tables_section.split('\n'):
                line = line.strip()
                if line.startswith('- '):
                    table_name = line.replace('- ', '').strip()
                    if table_name:
                        tables.append(table_name)
        
        span.set_attribute("diagram.action", action)
        span.set_attribute("diagram.tables_count", len(tables))
        
        # Save Mermaid diagram to S3
        s3_path = None
        if mermaid_diagram:
            s3_path = save_mermaid_to_s3(file_name, mermaid_diagram, action)
            if s3_path:
                span.set_attribute("diagram.s3_path", s3_path)
        
        span.set_status(Status(StatusCode.OK))
        
        return {
            "file_name": file_name,
            "action": action,
            "mermaid_diagram": mermaid_diagram,
            "s3_path": s3_path,
            "full_response": response_text,
            "tables": tables,
            "model_used": ER_DIAGRAM_MODEL,
            "status": "completed"
        }
        
    except Exception as e:
        span.set_status(Status(StatusCode.ERROR, str(e)))
        span.set_attribute("error.message", str(e))
        logger.error(f"ER diagram generation failed for {file_name}: {e}")
        return {
            "file_name": file_name,
            "action": "ERROR",
            "mermaid_diagram": "",
            "s3_path": None,
            "full_response": f"Generation failed: {str(e)}",
            "tables": [],
            "model_used": ER_DIAGRAM_MODEL,
            "status": "failed",
            "error": str(e)
        }

def store_diagram_in_memory(session_id: str, actor_id: str, diagram_result: Dict[str, Any]):
    """Store ER diagram result in AgentCore Memory"""
    if not MEMORY_ID:
        logger.warning("No memory ID available, skipping memory storage")
        return
    
    try:
        user_input = f"Generate ER diagram for {diagram_result['file_name']}"
        agent_response = json.dumps({
            "action": diagram_result["action"],
            "s3_path": diagram_result.get("s3_path"),
            "tables": diagram_result["tables"][:5],
            "status": diagram_result["status"]
        })
        
        memory_client.save_turn(
            memory_id=MEMORY_ID,
            actor_id=actor_id,
            session_id=session_id,
            user_input=user_input,
            agent_response=agent_response
        )
        
        logger.info(f"Stored ER diagram result in memory for session {session_id}")
        
    except Exception as e:
        logger.error(f"Failed to store diagram in memory: {e}")

@app.entrypoint
@requires_access_token(
    provider_name="cognitoerdiag",
    into="access_token",
    scopes=[OAUTH2_SCOPE],
    auth_flow="M2M"
)
async def generate_er_diagram(payload: Dict[str, Any], access_token) -> Dict[str, Any]:
    """
    Main entrypoint for SQL ER diagram generation requests
    Protected by Cognito M2M OAuth with OpenTelemetry tracing
    """
    logger.info(f"=== ENTRYPOINT CALLED === Payload keys: {list(payload.keys())}")
    logger.info(f"Payload: {json.dumps(payload, indent=2)}")
    
    with tracer.start_as_current_span("generate_er_diagram_entrypoint") as span:
        start_time = time.time()
        
        try:
            logger.info("Extracting parameters from payload...")
            # Extract parameters
            sql_content = payload.get("sql_content", "")
            file_name = payload.get("file_name", "unknown_file.sql")
            session_id = payload.get("session_id", f"session_{int(time.time())}")
            actor_id = payload.get("actor_id", "erdiagram_generator")
            
            logger.info(f"Extracted: file_name={file_name}, session_id={session_id}, actor_id={actor_id}, sql_length={len(sql_content)}")
            
            # Set OpenTelemetry baggage for session tracking
            ctx = baggage.set_baggage("session.id", session_id)
            ctx = baggage.set_baggage("actor.id", actor_id)
            token = context.attach(ctx)
            
            # Set span attributes
            span.set_attribute("file.name", file_name)
            span.set_attribute("session.id", session_id)
            span.set_attribute("actor.id", actor_id)
            span.set_attribute("sql.size", len(sql_content))
            
            logger.info(f"Starting ER diagram generation for {file_name} (session: {session_id})")
            
            if not sql_content:
                logger.warning("No SQL content provided in payload")
                span.set_attribute("error.type", "missing_content")
                return {
                    "error": "No SQL content provided",
                    "status": "failed"
                }
            
            logger.info(f"Calling generate_er_diagram_from_sql...")
            # Generate ER diagram with tracing
            diagram_result = await generate_er_diagram_from_sql(sql_content, file_name)
            logger.info(f"Diagram generation completed, result keys: {list(diagram_result.keys())}")
            
            # Add metadata
            diagram_result.update({
                "session_id": session_id,
                "actor_id": actor_id,
                "duration_ms": (time.time() - start_time) * 1000,
                "timestamp": time.time()
            })
            
            # Store in memory
            store_diagram_in_memory(session_id, actor_id, diagram_result)
            
            # Set final span attributes
            span.set_attribute("diagram.action", diagram_result.get("action", "unknown"))
            span.set_attribute("diagram.status", diagram_result.get("status", "unknown"))
            span.set_attribute("diagram.duration_ms", diagram_result["duration_ms"])
            span.set_status(Status(StatusCode.OK))
            
            logger.info(f"ER diagram generation completed for {file_name} - Action: {diagram_result['action']} (Session: {session_id})")
            
            # Detach context
            context.detach(token)
            
            return diagram_result
            
        except Exception as e:
            logger.error(f"=== EXCEPTION IN ENTRYPOINT ===")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception message: {str(e)}")
            logger.error(f"Exception details:", exc_info=True)
            
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("error.message", str(e))
            span.set_attribute("error.type", type(e).__name__)
            
            return {
                "error": str(e),
                "error_type": type(e).__name__,
                "status": "failed",
                "duration_ms": (time.time() - start_time) * 1000
            }

@app.route("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy", "service": "erdiagram-generation-agent"}

@app.route("/history/{session_id}")
@requires_access_token(
    provider_name="cognitoerdiag",
    into="access_token",
    scopes=[OAUTH2_SCOPE],
    auth_flow="M2M"
)
def get_diagram_history(session_id: str, access_token) -> Dict[str, Any]:
    """Get ER diagram generation history for a session"""
    try:
        if not MEMORY_ID:
            return {"history": [], "message": "Memory not available"}
        
        memories = memory_client.retrieve_memories(
            memory_id=MEMORY_ID,
            namespace="dashboard/data/{actorId}/all",
            query="ER diagram generation",
            actor_id="erdiagram_generator",
            top_k=10
        )
        
        return {
            "session_id": session_id,
            "history": memories,
            "count": len(memories)
        }
        
    except Exception as e:
        logger.error(f"Failed to retrieve history: {e}")
        return {"error": str(e), "history": []}

if __name__ == "__main__":
    logger.info(f"Starting ER Diagram Generation Agent with model: {ER_DIAGRAM_MODEL}")
    logger.info(f"Memory ID: {MEMORY_ID}")
    logger.info(f"S3 Bucket: {S3_BUCKET_NAME if S3_BUCKET_NAME else 'NOT CONFIGURED'}")
    app.run(port=8080, host="0.0.0.0")
