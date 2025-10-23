"""
Unified Agent Memory Demo

This example demonstrates the new unified AgentMemory interface that provides
a simple, consistent API for all agent memory scenarios:
- Multi-Agent: Multiple agents with collaboration
- Multi-User: Single agent with multiple users
- Hybrid: Dynamic switching between modes
- Auto: Intelligent mode detection

The interface automatically handles the complexity of the underlying implementations
while providing a clean, easy-to-use API.
"""

import os
import sys
from typing import Dict, Any
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mem.agent import AgentMemory


def load_oceanbase_config():
    """Load OceanBase configuration from environment variables."""
    config_path = os.path.join(os.path.dirname(__file__), 'configs', 'oceanbase.env')
    load_dotenv(config_path)
    
    # Build connection args
    connection_args = {
        "host": os.getenv('DATABASE_HOST', '127.0.0.1'),
        "port": int(os.getenv('DATABASE_PORT', '2881')),
        "user": os.getenv('DATABASE_USER', 'root@sys'),
        "password": os.getenv('DATABASE_PASSWORD', 'password'),
        "db_name": os.getenv('DATABASE_NAME', 'powermem')
    }
    
    # Build configuration dictionary
    config = {
        'database': {
            'provider': os.getenv('DATABASE_PROVIDER', 'oceanbase'),
            'config': {
                'collection_name': os.getenv('DATABASE_COLLECTION_NAME', 'memories'),
                'connection_args': connection_args,
                'vidx_metric_type': os.getenv('DATABASE_VECTOR_METRIC_TYPE', 'cosine'),
                'index_type': os.getenv('DATABASE_INDEX_TYPE', 'IVF_FLAT'),
                'embedding_model_dims': int(os.getenv('DATABASE_EMBEDDING_MODEL_DIMS', '1536')),
                'primary_field': os.getenv('DATABASE_PRIMARY_FIELD', 'id'),
                'vector_field': os.getenv('DATABASE_VECTOR_FIELD', 'embedding'),
                'text_field': os.getenv('DATABASE_TEXT_FIELD', 'document'),
                'metadata_field': os.getenv('DATABASE_METADATA_FIELD', 'metadata'),
                'vidx_name': os.getenv('DATABASE_VIDX_NAME', 'memories_vidx')
            }
        },
        'llm': {
            'provider': os.getenv('LLM_PROVIDER', 'qwen'),
            'config': {
                'api_key': os.getenv('LLM_API_KEY'),
                'model': os.getenv('LLM_MODEL', 'qwen-plus'),
                'temperature': float(os.getenv('LLM_TEMPERATURE', '0.7')),
                'max_tokens': int(os.getenv('LLM_MAX_TOKENS', '1000')),
                'top_p': float(os.getenv('LLM_TOP_P', '0.8')),
                'top_k': int(os.getenv('LLM_TOP_K', '50')),
                'dashscope_base_url': os.getenv('LLM_BASE_URL', 'https://dashscope.aliyuncs.com/api/v1'),
                'enable_search': os.getenv('LLM_ENABLE_SEARCH', 'false').lower() == 'true',
                'response_callback': None
            }
        },
        'embedding': {
            'provider': os.getenv('EMBEDDING_PROVIDER', 'qwen'),
            'config': {
                'api_key': os.getenv('EMBEDDING_API_KEY'),
                'model': os.getenv('EMBEDDING_MODEL', 'text-embedding-v4'),
                'embedding_dims': int(os.getenv('EMBEDDING_DIMS', '1536'))
            }
        },
        'intelligent_memory': {
            'enabled': os.getenv('INTELLIGENT_MEMORY_ENABLED', 'true').lower() == 'true',
            'initial_retention': float(os.getenv('INTELLIGENT_MEMORY_INITIAL_RETENTION', '1.0')),
            'decay_rate': float(os.getenv('INTELLIGENT_MEMORY_DECAY_RATE', '0.1')),
            'reinforcement_factor': float(os.getenv('INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR', '0.3')),
            'working_threshold': float(os.getenv('INTELLIGENT_MEMORY_WORKING_THRESHOLD', '0.3')),
            'short_term_threshold': float(os.getenv('INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD', '0.6')),
            'long_term_threshold': float(os.getenv('INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD', '0.8'))
        },
        'telemetry': {
            'enable_telemetry': os.getenv('TELEMETRY_ENABLED', 'false').lower() == 'true',
            'telemetry_endpoint': os.getenv('TELEMETRY_ENDPOINT', 'https://telemetry.powermem.ai'),
            'telemetry_api_key': os.getenv('TELEMETRY_API_KEY'),
            'batch_size': int(os.getenv('TELEMETRY_BATCH_SIZE', '100')),
            'flush_interval': int(os.getenv('TELEMETRY_FLUSH_INTERVAL', '30'))
        },
        'audit': {
            'enabled': os.getenv('AUDIT_ENABLED', 'true').lower() == 'true',
            'log_file': os.getenv('AUDIT_LOG_FILE', './logs/audit.log'),
            'log_level': os.getenv('AUDIT_LOG_LEVEL', 'INFO'),
            'retention_days': int(os.getenv('AUDIT_RETENTION_DAYS', '90'))
        },
        'logging': {
            'level': os.getenv('LOGGING_LEVEL', 'DEBUG'),
            'format': os.getenv('LOGGING_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            'file': os.getenv('LOGGING_FILE', './logs/powermem.log')
        }
    }
    
    return config


def demonstrate_auto_mode():
    """Demonstrate automatic mode detection."""
    print("🤖 Auto Mode Detection Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory with auto mode detection
    agent_memory = AgentMemory(config, mode='auto')
    
    print(f"✅ Detected mode: {agent_memory.get_mode()}")
    
    # Add some memories
    print("\n📝 Adding memories...")
    
    agent_memory.add(
        "User prefers email support over phone calls",
        user_id="customer_123",
        agent_id="support_agent",
        metadata={"priority": "high", "category": "communication_preference"}
    )
    
    agent_memory.add(
        "Customer budget is $1000/month for enterprise solutions",
        user_id="customer_123",
        agent_id="sales_agent",
        metadata={"budget": 1000, "category": "budget_info"}
    )
    
    print("✅ Memories added successfully!")
    
    # Search memories
    print("\n🔍 Searching memories...")
    results = agent_memory.search("customer preferences", user_id="customer_123")
    print(f"Found {len(results)} memories")
    
    # Get statistics
    stats = agent_memory.get_statistics()
    print(f"\n📊 Statistics: {stats}")


def demonstrate_multi_agent_mode():
    """Demonstrate multi-agent mode."""
    print("\n🤖 Multi-Agent Mode Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory in multi-agent mode
    agent_memory = AgentMemory(config, mode='multi_agent')
    
    print(f"✅ Mode: {agent_memory.get_mode()}")
    
    # Create agents
    support_agent = agent_memory.create_agent("support_agent", "Customer Support")
    sales_agent = agent_memory.create_agent("sales_agent", "Sales Agent")
    
    print("✅ Agents created successfully!")
    
    # Add memories for each agent
    print("\n📝 Adding agent-specific memories...")
    
    support_agent.add(
        "Customer reported slow response times last week",
        user_id="customer_123",
        metadata={"issue_type": "performance", "category": "complaint"}
    )
    
    sales_agent.add(
        "Customer decision maker is CTO, prefers technical demos",
        user_id="customer_123",
        metadata={"decision_maker": "CTO", "category": "stakeholder_info"}
    )
    
    print("✅ Agent memories added successfully!")
    
    # Create a group
    group_result = agent_memory.create_group(
        "customer_team",
        ["support_agent", "sales_agent"],
        permissions={
            "owner": ["read", "write", "delete", "admin"],
            "collaborator": ["read", "write"],
            "viewer": ["read"]
        }
    )
    
    print(f"✅ Group created: {group_result}")
    
    # Search across agents
    print("\n🔍 Cross-agent search...")
    support_results = support_agent.search("customer issues", user_id="customer_123")
    sales_results = sales_agent.search("customer decision", user_id="customer_123")
    
    print(f"Support agent found {len(support_results)} memories")
    print(f"Sales agent found {len(sales_results)} memories")


def demonstrate_multi_user_mode():
    """Demonstrate multi-user mode."""
    print("\n👥 Multi-User Mode Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory in multi-user mode
    agent_memory = AgentMemory(config, mode='multi_user')
    
    print(f"✅ Mode: {agent_memory.get_mode()}")
    
    # Add memories for different users
    print("\n📝 Adding user-specific memories...")
    
    agent_memory.add(
        "User Alice likes Python and machine learning",
        user_id="alice",
        metadata={"interests": ["Python", "ML"], "category": "preferences"}
    )
    
    agent_memory.add(
        "User Bob prefers Java and enterprise solutions",
        user_id="bob",
        metadata={"interests": ["Java", "Enterprise"], "category": "preferences"}
    )
    
    agent_memory.add(
        "User Charlie is interested in DevOps and automation",
        user_id="charlie",
        metadata={"interests": ["DevOps", "Automation"], "category": "preferences"}
    )
    
    print("✅ User memories added successfully!")
    
    # Search for each user
    print("\n🔍 User-specific searches...")
    
    alice_results = agent_memory.search("Python", user_id="alice")
    bob_results = agent_memory.search("Java", user_id="bob")
    charlie_results = agent_memory.search("DevOps", user_id="charlie")
    
    print(f"Alice found {len(alice_results)} memories")
    print(f"Bob found {len(bob_results)} memories")
    print(f"Charlie found {len(charlie_results)} memories")
    
    # Get all memories
    all_memories = agent_memory.get_all()
    print(f"\n📊 Total memories across all users: {len(all_memories)}")


def demonstrate_hybrid_mode():
    """Demonstrate hybrid mode with dynamic switching."""
    print("\n🔄 Hybrid Mode Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory in hybrid mode
    agent_memory = AgentMemory(config, mode='hybrid')
    
    print(f"✅ Mode: {agent_memory.get_mode()}")
    
    # Add memories (will automatically detect context)
    print("\n📝 Adding memories with automatic context detection...")
    
    # This might be detected as multi-agent context
    agent_memory.add(
        "Support agent handled customer complaint about slow response",
        user_id="customer_123",
        agent_id="support_agent",
        metadata={"context": "multi_agent", "category": "support"}
    )
    
    # This might be detected as multi-user context
    agent_memory.add(
        "User Alice requested new features for the mobile app",
        user_id="alice",
        metadata={"context": "multi_user", "category": "feature_request"}
    )
    
    print("✅ Hybrid memories added successfully!")
    
    # Get statistics
    stats = agent_memory.get_statistics()
    print(f"\n📊 Hybrid statistics: {stats}")


def demonstrate_unified_api():
    """Demonstrate the unified API across all modes."""
    print("\n🎯 Unified API Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Test the same API across different modes
    modes = ['auto', 'multi_agent', 'multi_user', 'hybrid']
    
    for mode in modes:
        print(f"\n📋 Testing {mode} mode...")
        
        try:
            # Create agent memory
            agent_memory = AgentMemory(config, mode=mode)
            
            # Test basic operations
            agent_memory.add(
                f"Test memory for {mode} mode",
                user_id="test_user",
                agent_id="test_agent",
                metadata={"mode": mode, "test": True}
            )
            
            # Test search
            results = agent_memory.search("test", user_id="test_user")
            
            # Test statistics
            stats = agent_memory.get_statistics()
            
            print(f"  ✅ {mode}: Added memory, found {len(results)} results, stats: {len(stats)} fields")
            
        except Exception as e:
            print(f"  ❌ {mode}: Error - {e}")


def main():
    """Main function to run the unified agent memory demo."""
    print("🚀 Unified Agent Memory Management Demo")
    print("=" * 60)
    print("Database: OceanBase")
    print("LLM: Qwen")
    print("Embedding: Qwen text-embedding-v4")
    print("=" * 60)
    
    try:
        # Demonstrate different modes
        demonstrate_auto_mode()
        demonstrate_multi_agent_mode()
        demonstrate_multi_user_mode()
        demonstrate_hybrid_mode()
        demonstrate_unified_api()
        
        print("\n🎉 Unified Agent Memory Demo Completed Successfully!")
        print("=" * 60)
        print("✅ All features demonstrated:")
        print("  • Automatic mode detection")
        print("  • Multi-agent collaboration")
        print("  • Multi-user isolation")
        print("  • Hybrid dynamic switching")
        print("  • Unified API across all modes")
        print("  • Simple, consistent interface")
        print("  • No mem0 dependencies")
        
    except Exception as e:
        print(f"❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
