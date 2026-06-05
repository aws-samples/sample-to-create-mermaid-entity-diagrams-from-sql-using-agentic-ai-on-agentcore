#!/usr/bin/env python3.13
import boto3
import json
import time
from botocore.exceptions import ClientError
from botocore.config import Config

# AgentCore Memory imports
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

# Configuration
REGION = "us-west-2"
PROJECT_NAME = "erdiagfromsql" # change this to your project name
AGENTCORE_MEMORY_NAME = f"{PROJECT_NAME}_erdiagram_memory"

# Initialize clients
ssm_client = boto3.client('ssm', region_name=REGION)
memory_client = MemoryClient(region_name=REGION)

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

def create_agentcore_memory():
    """
    Create AgentCore Memory resource for Developer Dashboard
    """
    try:
        # Check if memory already exists
        existing_memory_id = get_ssm_parameter(f"/app/{PROJECT_NAME}/agentcore/memory_id")
        if existing_memory_id:
            try:
                # Verify memory still exists
                response = memory_client.get_memory(memory_id=existing_memory_id)
                print(f"Found existing AgentCore Memory: {existing_memory_id}")
                return existing_memory_id
            except:
                print("Existing memory ID in SSM is invalid, creating new memory")
        
        # Define memory strategies for ER diagram generation
        strategies = [
            # One SEMANTIC strategy - stores ER diagram generation results
            {
                StrategyType.SEMANTIC.value: {
                    "name": "ERDiagramData",
                    "description": "Stores SQL schema analysis, ER diagrams, and S3 paths",
                    "namespaces": ["erdiagram/data/{actorId}/all"],
                }
            },
            # One SUMMARY strategy (requires sessionId)
            {
                StrategyType.SUMMARY.value: {
                    "name": "ERDiagramMetrics",
                    "description": "Aggregated metrics for ER diagram generation history",
                    "namespaces": ["erdiagram/metrics/{actorId}/{sessionId}/summary"],
                }
            },
            # One USER_PREFERENCE strategy
            {
                StrategyType.USER_PREFERENCE.value: {
                    "name": "ERDiagramPreferences",
                    "description": "User preferences for diagram generation and formatting",
                    "namespaces": ["erdiagram/preferences/{actorId}/settings"],
                }
            }
        ]
        
        print(f"Creating AgentCore Memory: {AGENTCORE_MEMORY_NAME}")
        print("This will take 2-3 minutes...")
        print("• Setting up managed vector databases for semantic search")
        print("• Configuring memory extraction pipelines")
        print("• Provisioning secure, multi-tenant storage")
        print("• Establishing namespace isolation for ER diagram data")
        
        # Create memory resource
        response = memory_client.create_memory_and_wait(
            name=AGENTCORE_MEMORY_NAME,
            description="ER diagram generation memory for SQL schema analysis and Mermaid diagrams",
            strategies=strategies,
            event_expiry_days=90,  # Memories expire after 90 days
        )
        
        memory_id = response["id"]
        
        # Store memory ID in SSM
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/memory_id",
            memory_id,
            "AgentCore Memory ID for ER Diagram Generation"
        )
        
        # Store memory configuration details
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/memory_name",
            AGENTCORE_MEMORY_NAME,
            "AgentCore Memory Name for ER Diagram Generation"
        )
        
        # Store strategy configuration for discovery
        strategy_config = {
            "semantic_namespaces": [
                "erdiagram/data/{actorId}/all"
            ],
            "summary_namespaces": [
                "erdiagram/metrics/{actorId}/{sessionId}/summary"
            ],
            "preference_namespaces": [
                "erdiagram/preferences/{actorId}/settings"
            ]
        }
        
        store_ssm_parameter(
            f"/app/{PROJECT_NAME}/agentcore/memory_strategy_config",
            json.dumps(strategy_config),
            "Memory strategy configuration for ER diagram queries"
        )
        
        print(f"✅ AgentCore Memory created successfully!")
        print(f"📋 Memory ID: {memory_id}")
        print(f"📋 Memory Name: {AGENTCORE_MEMORY_NAME}")
        print(f"📋 Expiry: 90 days")
        print(f"📋 Strategies: SEMANTIC, SUMMARY, USER_PREFERENCE")
        
        return memory_id
        
    except Exception as e:
        print(f"❌ Failed to create memory resource: {e}")
        raise

def test_memory_access(memory_id):
    """Test memory access and basic operations"""
    print(f"\n🧪 Testing AgentCore Memory access...")
    
    try:
        # Test memory retrieval
        response = memory_client.get_memory(memory_id=memory_id)
        print(f"✅ Memory access successful")
        print(f"📋 Status: {response.get('status', 'Unknown')}")
        print(f"📋 Strategies: {len(response.get('strategies', []))}")
        
        # Test memory search (should return empty initially)
        try:
            search_response = memory_client.search_memory(
                memory_id=memory_id,
                query="test query",
                max_results=1
            )
            print(f"✅ Memory search capability verified")
            print(f"📋 Initial search results: {len(search_response.get('results', []))}")
        except Exception as e:
            print(f"⚠️ Memory search test: {e}")
        
    except Exception as e:
        print(f"❌ Memory access test failed: {e}")

def demonstrate_erdiagram_discovery():
    """Demonstrate how to discover and query ER diagram memory"""
    print(f"\n📊 ER Diagram Memory Query Methods:")
    
    print(f"\n1️⃣ Get Memory ID from SSM:")
    print(f"   memory_id = get_ssm_parameter('/app/{PROJECT_NAME}/agentcore/memory_id')")
    
    print(f"\n2️⃣ Get Strategy Configuration from SSM:")
    print(f"   config = get_ssm_parameter('/app/{PROJECT_NAME}/agentcore/memory_strategy_config')")
    print(f"   namespaces = json.loads(config)")
    
    print(f"\n3️⃣ Query Memory by Namespace:")
    print(f"   # Get all ER diagram generation results")
    print(f"   memory_client.retrieve_memories(")
    print(f"       memory_id=memory_id,")
    print(f"       namespace='erdiagram/data/{{actor_id}}/all',")
    print(f"       query='ER diagram generation',")
    print(f"       actor_id='erdiagram_generator'")
    print(f"   )")
    
    print(f"\n4️⃣ Get Generation Session History:")
    print(f"   # Get conversation history for a session")
    print(f"   memory_client.list_events(")
    print(f"       memory_id=memory_id,")
    print(f"       actor_id='erdiagram_generator',")
    print(f"       session_id='session456'")
    print(f"   )")
    
    print(f"\n5️⃣ Query Aggregated Metrics (requires sessionId):")
    print(f"   # Get summary data for reporting")
    print(f"   memory_client.retrieve_memories(")
    print(f"       memory_id=memory_id,")
    print(f"       namespace='erdiagram/metrics/{{actor_id}}/{{session_id}}/summary',")
    print(f"       query='generation trends',")
    print(f"       actor_id='erdiagram_generator'")
    print(f"   )")

def main():
    """Main deployment function"""
    print("🚀 Starting AgentCore Memory deployment...")
    print(f"📋 Project: {PROJECT_NAME}")
    print(f"📋 Region: {REGION}")
    
    try:
        # Create AgentCore Memory
        print("\n📋 Step 1: Creating AgentCore Memory...")
        memory_id = create_agentcore_memory()
        
        # Test memory access
        print("\n📋 Step 2: Testing memory access...")
        test_memory_access(memory_id)
        
        # Display dashboard integration info
        print(f"\n📋 Step 4: ER Diagram Memory Integration Guide...")
        demonstrate_erdiagram_discovery()
        
        # Display SSM parameters
        print(f"\n📋 Step 5: Resources stored in SSM Parameter Store:")
        print(f"  Memory Resources: /app/{PROJECT_NAME}/agentcore/")
        
        # Summary
        print(f"\n✅ AgentCore Memory deployment completed successfully!")
        print(f"📋 Memory ID: {memory_id}")
        print(f"📋 Memory Name: {AGENTCORE_MEMORY_NAME}")
        print(f"📋 Region: {REGION}")
        print(f"📋 Capabilities:")
        print(f"  • SQL schema analysis results storage and retrieval")
        print(f"  • ER diagram generation tracking (CREATE/UPDATE/NO_UPDATE)")
        print(f"  • S3 Mermaid file path storage")
        print(f"  • Table identification history")
        print(f"  • User preference management")
        print(f"  • Aggregated metrics and reporting")
        print(f"📋 Memory stored in SSM: /app/{PROJECT_NAME}/agentcore/memory_id")
        print(f"📋 Strategy config in SSM: /app/{PROJECT_NAME}/agentcore/memory_strategy_config")
        
    except Exception as e:
        print(f"❌ Deployment failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()
