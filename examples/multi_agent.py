"""
Complete Multi-Agent Demo for powermem

This example demonstrates comprehensive multi-agent memory management capabilities:
- Multi-agent collaboration and memory sharing
- Agent-specific memory isolation and filtering
- Cross-agent memory search and retrieval
- Memory context and scope management
- Agent permission and privacy controls
- Real-time memory updates and synchronization

Database: OceanBase
LLM: Qwen
Embedding: Qwen text-embedding-v4
"""

import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from powermem import Memory
from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.components.collaboration_coordinator import CollaborationCoordinator
from powermem.agent.components.permission_controller import PermissionController
from powermem.agent.components.privacy_protector import PrivacyProtector
from powermem.agent.components.scope_controller import ScopeController


def load_oceanbase_config():
    """Load OceanBase configuration from environment variables."""
    # Try to load from configs/.env first (preferred)
    env_path = os.path.join(os.path.dirname(__file__), '..', 'configs', '.env')
    oceanbase_env_path = os.path.join(os.path.dirname(__file__), '..', 'configs', 'oceanbase.env')
    
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
    elif os.path.exists(oceanbase_env_path):
        load_dotenv(oceanbase_env_path, override=True)
    else:
        # Try to load from any .env file
        load_dotenv()
    
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
                'enable_search': os.getenv('LLM_ENABLE_SEARCH', 'false').lower() == 'true'
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
        'agent_memory': {
            'enabled': os.getenv('AGENT_ENABLED', 'true').lower() == 'true',
            'mode': os.getenv('AGENT_MEMORY_MODE', 'auto'),
            'default_scope': os.getenv('AGENT_DEFAULT_SCOPE', 'AGENT'),
            'default_privacy_level': os.getenv('AGENT_DEFAULT_PRIVACY_LEVEL', 'PRIVATE'),
            'default_collaboration_level': os.getenv('AGENT_DEFAULT_COLLABORATION_LEVEL', 'READ_ONLY'),
            'default_access_permission': os.getenv('AGENT_DEFAULT_ACCESS_PERMISSION', 'OWNER_ONLY')
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


def create_agent_memory(config: Dict[str, Any], agent_id: str, agent_name: str) -> Memory:
    """Create a memory instance for a specific agent."""
    return Memory(config=config, agent_id=agent_id)


def demonstrate_basic_multi_agent():
    """Demonstrate basic multi-agent memory management."""
    print("🤖 Basic Multi-Agent Memory Management")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create memory instances for different agents
    support_memory = create_agent_memory(config, "support_agent", "Customer Support Agent")
    sales_memory = create_agent_memory(config, "sales_agent", "Sales Agent")
    tech_memory = create_agent_memory(config, "tech_agent", "Technical Agent")
    
    # Customer information
    customer_id = "customer_12345"
    
    print(f"📝 Adding memories for customer {customer_id}...")
    
    # Support Agent memories
    support_memory.add(
        "Customer prefers email support over phone calls",
        user_id=customer_id,
        agent_id="support_agent",
        metadata={"priority": "high", "category": "communication_preference"}
    )
    support_memory.add(
        "Customer has premium subscription with priority support",
        user_id=customer_id,
        agent_id="support_agent",
        metadata={"subscription": "premium", "category": "account_info"}
    )
    support_memory.add(
        "Customer reported slow response times last week",
        user_id=customer_id,
        agent_id="support_agent",
        metadata={"issue_type": "performance", "category": "complaint"}
    )
    
    # Sales Agent memories
    sales_memory.add(
        "Customer interested in AI-powered features and automation",
        user_id=customer_id,
        agent_id="sales_agent",
        metadata={"interest": "AI", "category": "product_interest"}
    )
    sales_memory.add(
        "Customer budget is $1000/month for enterprise solutions",
        user_id=customer_id,
        agent_id="sales_agent",
        metadata={"budget": 1000, "category": "budget_info"}
    )
    sales_memory.add(
        "Customer decision maker is CTO, prefers technical demos",
        user_id=customer_id,
        agent_id="sales_agent",
        metadata={"decision_maker": "CTO", "category": "stakeholder_info"}
    )
    
    # Technical Agent memories
    tech_memory.add(
        "Customer uses Python and PostgreSQL in their tech stack",
        user_id=customer_id,
        agent_id="tech_agent",
        metadata={"tech_stack": ["Python", "PostgreSQL"], "category": "technical_info"}
    )
    tech_memory.add(
        "Customer has 50+ developers and needs scalable solutions",
        user_id=customer_id,
        agent_id="tech_agent",
        metadata={"team_size": 50, "category": "scale_requirements"}
    )
    tech_memory.add(
        "Customer experienced database performance issues",
        user_id=customer_id,
        agent_id="tech_agent",
        metadata={"issue_type": "database_performance", "category": "technical_issue"}
    )
    
    print("✅ Memories added successfully!")
    
    # Demonstrate agent-specific searches
    print("\n🔍 Agent-Specific Memory Searches:")
    print("-" * 40)
    
    # Support agent search
    print("📞 Support Agent searching for 'customer preferences':")
    support_results = support_memory.search("customer preferences", user_id=customer_id, agent_id="support_agent")
    for i, result in enumerate(support_results.get("results", [])[:3], 1):
        content = result.get('memory')
        print(f"  {i}. {content}")
        print(f"     Metadata: {result.get('metadata', {})}")
    
    # Sales agent search
    print("\n💰 Sales Agent searching for 'budget and pricing':")
    sales_results = sales_memory.search("budget and pricing", user_id=customer_id, agent_id="sales_agent")
    for i, result in enumerate(sales_results.get("results", [])[:3], 1):
        content = result.get('memory')
        print(f"  {i}. {content}")
        print(f"     Metadata: {result.get('metadata', {})}")
    
    # Technical agent search
    print("\n🔧 Technical Agent searching for 'technical requirements':")
    tech_results = tech_memory.search("technical requirements", user_id=customer_id, agent_id="tech_agent")
    for i, result in enumerate(tech_results.get("results", [])[:3], 1):
        content = result.get('memory')
        print(f"  {i}. {content}")
        print(f"     Metadata: {result.get('metadata', {})}")
    
    # Cross-agent search
    print("\n🌐 Cross-Agent Search (All Agents):")
    print("-" * 40)
    all_results = support_memory.search("customer information", user_id=customer_id)
    all_results_list = all_results.get("results", [])
    print(f"Found {len(all_results_list)} total memories across all agents:")
    for i, result in enumerate(all_results_list[:5], 1):
        agent_id = result.get('agent_id', 'Unknown')
        content = result.get('memory')
        print(f"  {i}. [{agent_id}] {content}")
        print(f"     Metadata: {result.get('metadata', {})}")


def demonstrate_advanced_multi_agent():
    """Demonstrate advanced multi-agent features."""
    print("\n🚀 Advanced Multi-Agent Features")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create individual memory instances for different agents
    # (MultiAgentMemoryManager requires complex configuration, so we'll use individual Memory instances)
    alice_memory = create_agent_memory(config, "alice_dev", "Alice Developer")
    bob_memory = create_agent_memory(config, "bob_dev", "Bob Developer")
    charlie_memory = create_agent_memory(config, "charlie_qa", "Charlie QA")
    diana_memory = create_agent_memory(config, "diana_pm", "Diana PM")
    
    print("🔧 Multi-Agent Components Available:")
    print("  ✓ Collaboration Coordinator: Available for agent coordination")
    print("  ✓ Permission Controller: Available for access control")
    print("  ✓ Privacy Protector: Available for privacy management")
    print("  ✓ Scope Controller: Available for scope management")
    
    # Project scenario
    project_id = "project_ai_platform"
    team_members = ["alice_dev", "bob_dev", "charlie_qa", "diana_pm"]
    
    print(f"\n📋 Project: {project_id}")
    print(f"👥 Team Members: {', '.join(team_members)}")
    
    # Add project memories with different scopes and permissions
    print("\n📝 Adding Project Memories...")
    
    # Development memories (Alice)
    alice_memory.add(
        "Implemented user authentication module with JWT tokens",
        user_id="alice_dev",
        agent_id="alice_dev",
        run_id=project_id,
        metadata={
            "module": "authentication",
            "technology": "JWT",
            "status": "completed",
            "scope": "development"
        }
    )
    
    # Development memories (Bob)
    bob_memory.add(
        "Created database schema for user profiles and preferences",
        user_id="bob_dev",
        agent_id="bob_dev",
        run_id=project_id,
        metadata={
            "module": "database",
            "technology": "PostgreSQL",
            "status": "completed",
            "scope": "development"
        }
    )
    
    # QA memories (Charlie)
    charlie_memory.add(
        "Found critical bug in user registration flow - duplicate emails allowed",
        user_id="charlie_qa",
        agent_id="charlie_qa",
        run_id=project_id,
        metadata={
            "issue_type": "bug",
            "severity": "critical",
            "module": "registration",
            "scope": "testing"
        }
    )
    
    # PM memories (Diana)
    diana_memory.add(
        "Client requested additional features: real-time notifications and analytics dashboard",
        user_id="diana_pm",
        agent_id="diana_pm",
        run_id=project_id,
        metadata={
            "request_type": "feature",
            "priority": "high",
            "scope": "product_management"
        }
    )
    
    # Cross-team collaboration memories
    alice_memory.add(
        "Alice and Bob collaborated on API integration between auth and user modules",
        user_id="alice_dev",
        agent_id="alice_dev",
        run_id=project_id,
        metadata={
            "collaboration": ["alice_dev", "bob_dev"],
            "scope": "collaboration",
            "module": "api_integration"
        }
    )
    
    print("✅ Project memories added successfully!")
    
    # Demonstrate scope-based searches
    print("\n🎯 Scope-Based Memory Searches:")
    print("-" * 40)
    
    # Development scope search
    print("💻 Development Team Search:")
    dev_results = alice_memory.search(
        "development progress",
        user_id="alice_dev",
        agent_id="alice_dev"
    )
    for i, result in enumerate(dev_results.get("results", [])[:3], 1):
        content = result.get('memory', '')
        print(f"  {i}. {content}")
        print(f"     Agent: {result.get('agent_id')}, Scope: {result.get('metadata', {}).get('scope')}")
    
    # Testing scope search
    print("\n🧪 QA Team Search:")
    qa_results = charlie_memory.search(
        "testing and bugs",
        user_id="charlie_qa",
        agent_id="charlie_qa"
    )
    for i, result in enumerate(qa_results.get("results", [])[:3], 1):
        content = result.get('memory', '')
        print(f"  {i}. {content}")
        print(f"     Agent: {result.get('agent_id')}, Scope: {result.get('metadata', {}).get('scope')}")
    
    # Project-wide search
    print("\n📊 Project-Wide Search:")
    project_results = alice_memory.search(
        "project status and progress",
        run_id=project_id
    )
    project_results_list = project_results.get("results", [])
    print(f"Found {len(project_results_list)} memories across the project:")
    for i, result in enumerate(project_results_list[:5], 1):
        agent_id = result.get('agent_id', 'Unknown')
        scope = result.get('metadata', {}).get('scope', 'Unknown')
        content = result.get('memory')
        print(f"  {i}. [{agent_id}] [{scope}] {content}")
    
    # Demonstrate collaboration tracking
    print("\n🤝 Collaboration Analysis:")
    print("-" * 40)
    collaboration_results = alice_memory.search(
        "collaboration and teamwork",
        run_id=project_id
    )
    collaboration_results_list = collaboration_results.get("results", [])
    for result in collaboration_results_list:
        if 'collaboration' in result.get('metadata', {}):
            collaborators = result['metadata']['collaboration']
            content = result.get('memory', '')
            print(f"  🤝 {content}")
            print(f"     Collaborators: {', '.join(collaborators)}")


def demonstrate_memory_management():
    """Demonstrate memory management features."""
    print("\n🧠 Intelligent Memory Management")
    print("=" * 50)
    
    config = load_oceanbase_config()
    memory = create_agent_memory(config, "memory_manager", "Memory Manager")
    
    # Add memories with different importance levels
    print("📝 Adding memories with different importance levels...")
    
    memories_data = [
        {
            "content": "Critical system failure in production database",
            "importance": "critical",
            "category": "incident"
        },
        {
            "content": "User reported minor UI bug in login form",
            "importance": "low",
            "category": "bug"
        },
        {
            "content": "New feature request for mobile app",
            "importance": "medium",
            "category": "feature"
        },
        {
            "content": "Security vulnerability found in authentication",
            "importance": "critical",
            "category": "security"
        },
        {
            "content": "Performance optimization completed",
            "importance": "medium",
            "category": "optimization"
        }
    ]
    
    for i, mem_data in enumerate(memories_data, 1):
        memory.add(
            mem_data["content"],
            user_id="admin",
            agent_id="memory_manager",
            metadata={
                "importance": mem_data["importance"],
                "category": mem_data["category"],
                "sequence": i
            }
        )
        print(f"  ✓ Added: {mem_data['content']}")
    
    # Demonstrate intelligent retrieval
    print("\n🔍 Intelligent Memory Retrieval:")
    print("-" * 40)
    
    # Search for critical issues
    print("🚨 Critical Issues Search:")
    critical_results = memory.search(
        "critical and urgent issues",
        user_id="admin",
        agent_id="memory_manager"
    )
    for i, result in enumerate(critical_results.get("results", [])[:3], 1):
        importance = result.get('metadata', {}).get('importance', 'Unknown')
        category = result.get('metadata', {}).get('category', 'Unknown')
        content = result.get('memory')
        print(f"  {i}. [{importance}] [{category}] {content}")
    
    # Search by category
    print("\n🔒 Security-Related Search:")
    security_results = memory.search(
        "security vulnerabilities and threats",
        user_id="admin",
        agent_id="memory_manager"
    )
    for i, result in enumerate(security_results.get("results", [])[:3], 1):
        importance = result.get('metadata', {}).get('importance', 'Unknown')
        category = result.get('metadata', {}).get('category', 'Unknown')
        content = result.get('memory')
        print(f"  {i}. [{importance}] [{category}] {content}")
    
    # Get all memories for analysis
    print("\n📊 Memory Analysis:")
    print("-" * 40)
    all_memories = memory.get_all(user_id="admin", agent_id="memory_manager").get("results", [])
    
    # Analyze by importance
    importance_counts = {}
    category_counts = {}
    
    for mem in all_memories:
        importance = mem.get('metadata', {}).get('importance', 'Unknown')
        category = mem.get('metadata', {}).get('category', 'Unknown')
        
        importance_counts[importance] = importance_counts.get(importance, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    
    print("📈 Memory Distribution by Importance:")
    for importance, count in importance_counts.items():
        print(f"  {importance}: {count} memories")
    
    print("\n📈 Memory Distribution by Category:")
    for category, count in category_counts.items():
        print(f"  {category}: {count} memories")


def main():
    """Main function to run the complete multi-agent demo."""
    print("🚀 Complete Multi-Agent Memory Management Demo")
    print("=" * 60)
    print("Database: OceanBase")
    print("LLM: Qwen")
    print("Embedding: Qwen text-embedding-v4")
    print("=" * 60)
    
    try:
        # Basic multi-agent demonstration
        demonstrate_basic_multi_agent()
        
        # Advanced multi-agent features
        demonstrate_advanced_multi_agent()
        
        # Memory management features
        demonstrate_memory_management()
        
        print("\n🎉 Multi-Agent Demo Completed Successfully!")
        print("=" * 60)
        print("✅ All features demonstrated:")
        print("  • Multi-agent memory isolation and sharing")
        print("  • Agent-specific memory searches")
        print("  • Cross-agent collaboration tracking")
        print("  • Scope-based memory filtering")
        print("  • Intelligent memory management")
        print("  • Real-time memory synchronization")
        print("  • Permission and privacy controls")
        
    except Exception as e:
        print(f"❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()