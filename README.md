# powermem - Intelligent Memory System

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

powermem is an AI-powered intelligent memory management system that provides a persistent memory layer for LLM applications. It enables applications to store, retrieve, and manage memories intelligently across multiple agents and users.

## ✨ Key Features

### 🧠 Intelligent Memory Management
- **Ebbinghaus Forgetting Curve**: Smart memory optimization based on cognitive science
- **Automatic Importance Scoring**: AI-powered memory importance evaluation
- **Memory Decay & Reinforcement**: Dynamic memory retention based on usage patterns
- **Intelligent Retrieval**: Context-aware memory search and ranking

### 🤖 Multi-Agent Support
- **Agent Isolation**: Separate memory spaces for different agents
- **Cross-Agent Collaboration**: Shared memory access and collaboration tracking
- **Permission Control**: Fine-grained access control for agent memories
- **Privacy Protection**: Built-in privacy controls and data protection

### 💾 Multiple Storage Backends
- **OceanBase**: Default enterprise-grade, scalable vector database
- **SQLite**: Lightweight, file-based storage for development
- **PostgreSQL**: Open-source vector database solution
- **Custom Adapters**: Extensible storage architecture

### 🔌 Rich Integrations
- **LLM Providers**: OpenAI, Anthropic, Qwen, Ollama, DeepSeek, and more
- **Embedding Models**: OpenAI, Qwen, HuggingFace, Azure, AWS Bedrock
- **Vector Databases**: Native support for multiple vector storage systems
- **API Compatibility**: LangChain and other AI framework integrations

### 📊 Enterprise Features
- **Audit Logging**: Comprehensive audit trails for compliance
- **Scalable Architecture**: Designed for production workloads
- **Configuration Management**: Flexible environment-based configuration

## 🚀 Quick Start

### Installation

```bash
# Basic installation
pip install powermem

# With LLM and vector store dependencies
pip install powermem[llm,vector_stores]

# For development with all dependencies
pip install powermem[dev,test,llm,vector_stores,extras]
```

### Basic Usage

**✨ Simplest Way**: Create memory from `.env` file automatically!

```python
from powermem import create_memory

# Automatically loads from .env or uses mock providers
memory = create_memory()

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
memories = memory.search("user preferences", user_id="user123")
for memory in memories:
    print(f"- {memory.get('memory')}")
```

**Programmatic Configuration**:

```python
from powermem import Memory

# Using mem0-compatible field names
config = {
    'llm': {
        'provider': 'qwen',  # or 'openai', 'anthropic', 'ollama'
        'config': {
            'api_key': 'your_api_key',
            'model': 'qwen-plus'
        }
    },
    'embedder': {  # mem0 field name
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'text-embedding-v4'
        }
    },
    'vector_store': {  # mem0 field name (defaults to oceanbase)
        'provider': 'oceanbase',
        'config': {}
    }
}

memory = Memory(config=config)
```

### Configuration with .env File

Create a `.env` file:
```env
LLM_PROVIDER=qwen
LLM_API_KEY=your_qwen_api_key
LLM_MODEL=qwen-plus
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_qwen_api_key
EMBEDDING_MODEL=text-embedding-v4
```

```python
from powermem import create_memory

# ✨ Automatically loads from .env
memory = create_memory()
```

**Or use programmatic configuration**:

```python
from powermem import Memory, auto_config

# Option 1: Auto-load from environment
config = auto_config()
memory = Memory(config=config)

# Option 2: Direct configuration
memory = Memory(config={
    'llm': {'provider': 'qwen', 'config': {'api_key': '...'}},
    'embedder': {'provider': 'qwen', 'config': {'api_key': '...'}},
    'vector_store': {'provider': 'oceanbase', 'config': {}}
})
```

## 🤖 Multi-Agent Examples

### Basic Multi-Agent Setup

```python
from powermem import create_memory, auto_config

# Simple way: auto-load from .env
config = auto_config()

config = {
    'vector_store': {
        'provider': 'oceanbase',
        'config': {}
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_qwen_api_key',
            'model': 'qwen-plus',
            'temperature': 0.7
        }
    },
    'embedder': { 
        'provider': 'qwen',
        'config': {
            'api_key': 'your_qwen_api_key',
            'model': 'text-embedding-v4'
        }
    }
}

# Create memory instances for different agents
support_memory = create_memory(config=config, agent_id="support_agent")
sales_memory = create_memory(config=config, agent_id="sales_agent")
tech_memory = create_memory(config=config, agent_id="tech_agent")

# Add agent-specific memories
customer_id = "customer_12345"

# Support agent memories
support_memory.add(
    "Customer prefers email support over phone calls",
    user_id=customer_id,
    metadata={"priority": "high", "category": "communication_preference"}
)

# Sales agent memories  
sales_memory.add(
    "Customer interested in AI-powered features and automation",
    user_id=customer_id,
    metadata={"interest": "AI", "category": "product_interest"}
)

# Technical agent memories
tech_memory.add(
    "Customer uses Python and OceanBase in their tech stack",
    user_id=customer_id,
    metadata={"tech_stack": ["Python", "OceanBase"], "category": "technical_info"}
)
```

### Cross-Agent Memory Search

```python
# Agent-specific search
print("Support Agent Search:")
support_results = support_memory.search(
    "customer preferences", 
    user_id=customer_id,
    agent_id="support_agent"
)
for result in support_results:
    print(f"- {result.get('memory')}")

# Cross-agent search (all agents)
print("\nCross-Agent Search:")
all_results = support_memory.search(
    "customer information", 
    user_id=customer_id
)
for result in all_results:
    agent_id = result.get('agent_id', 'Unknown')
    print(f"- [{agent_id}] {result['content']}")

# Search with metadata filtering
print("\nTechnical Requirements Search:")
tech_results = tech_memory.search(
    "technical requirements",
    user_id=customer_id,
    metadata_filter={"category": "technical_info"}
)
for result in tech_results:
    print(f"- {result.get('memory')}")
```

### Multi-Agent Collaboration Example

```python
# Project collaboration scenario
project_id = "project_ai_platform"

# Development team memories
dev_memory = Memory(config=config, agent_id="alice_dev")
qa_memory = Memory(config=config, agent_id="charlie_qa")

# Add collaborative memories
dev_memory.add(
    "Implemented user authentication module with JWT tokens",
    user_id="alice_dev",
    run_id=project_id,
    metadata={
        "module": "authentication",
        "technology": "JWT",
        "status": "completed",
        "scope": "development"
    }
)

qa_memory.add(
    "Found critical bug in user registration flow - duplicate emails allowed",
    user_id="charlie_qa", 
    run_id=project_id,
    metadata={
        "issue_type": "bug",
        "severity": "critical",
        "module": "registration",
        "scope": "testing"
    }
)

# Search project-wide memories
print("Project Status Search:")
project_results = dev_memory.search(
    "project status and progress",
    run_id=project_id
)
for result in project_results:
    agent_id = result.get('agent_id', 'Unknown')
    scope = result.get('metadata', {}).get('scope', 'Unknown')
    print(f"- [{agent_id}] [{scope}] {result['content']}")
```

## ⚙️ Configuration

### Environment Configuration

Create a `.env` file with your configuration:

```env
# Vector Store Configuration (defaults to oceanbase)
DATABASE_PROVIDER=oceanbase  # or sqlite, postgresql

# LLM Configuration  
LLM_PROVIDER=qwen  # or openai, anthropic, ollama
LLM_API_KEY=your_api_key
LLM_MODEL=qwen-plus

# Embedder Configuration
EMBEDDING_PROVIDER=qwen
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_API_KEY=your_api_key

# Intelligent Memory Settings
INTELLIGENT_MEMORY_ENABLED=true
INTELLIGENT_MEMORY_INITIAL_RETENTION=1.0
INTELLIGENT_MEMORY_DECAY_RATE=0.1
```

### Programmatic Configuration

```python
config = {
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'host': 'localhost',
            'port': 2881,
            'user': 'root',
            'password': 'password',
            'db_name': 'powermem'
        }
    },
    'llm': {
        'provider': 'openai',
        'config': {
            'api_key': 'your_openai_key',
            'model': 'gpt-4',
            'temperature': 0.7
        }
    },
    'embedder': {
        'provider': 'openai',
        'config': {
            'api_key': 'your_openai_key',
            'model': 'text-embedding-3-large'
        }
    }
}

memory = create_memory(config=config)  # or Memory(config=config)
```

## 📚 Examples and Documentation

### Available Examples

- **[Basic Usage](examples/basic_usage.py)**: Simple memory operations with automatic config loading
- **[Multi-Agent Demo](examples/multi_agent.py)**: Comprehensive multi-agent scenarios
- **[Agent Memory](examples/agent_memory.py)**: Agent memory management with scope detection
- **[Configuration Examples](examples/configs/)**: Different database and LLM configurations

### Run Examples

```bash
# Basic usage example (auto-loads from .env)
python examples/basic_usage.py

# Multi-agent demonstration
python examples/multi_agent.py

# Agent memory demonstration  
python examples/agent_memory.py
```

### Documentation

- **[API Documentation](docs/api/)**: Complete API reference
- **[Architecture Guide](docs/architecture/)**: System architecture and design
- **[Integration Guide](docs/guides/)**: Integration with different frameworks
- **[Examples](docs/examples/)**: More detailed examples and use cases

## 🏗️ Architecture

powermem is built with a modular architecture that supports:

- **Core Memory Engine**: Base memory operations and intelligent management
- **Agent Framework**: Multi-agent support with collaboration and permissions
- **Storage Adapters**: Pluggable storage backends
- **LLM Integrations**: Multiple LLM provider support
- **Embedding Services**: Various embedding model integrations

## 🔧 Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/powermem/powermem.git
cd powermem

# Install development dependencies
pip install -e ".[dev,test,llm,vector_stores]"

```



## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

We welcome contributions! Please see our contributing guidelines and code of conduct.

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **Discussions**: [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

**powermem** - Empowering AI applications with intelligent memory management.