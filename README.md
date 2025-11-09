# powermem - Intelligent Memory System

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
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

### 🕸️ Graph-Based Memory Storage
- **Knowledge Graph**: Extract entities and relationships to build knowledge graphs
- **Graph Retrieval**: Multi-hop graph traversal for complex memory relationships
- **Relationship Search**: Discover connections between memories through graph queries
- **Hybrid Storage**: Combine vector search with graph relationships for enhanced retrieval

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

For more detailed examples and usage patterns, see the [Getting Started Guide](docs/guides/0001-getting_started.md).

## 📚 Documentation

- **[Getting Started](docs/guides/0001-getting_started.md)**: Installation and quick start guide
- **[Configuration Guide](docs/guides/0002-configuration.md)**: Complete configuration options
- **[Multi-Agent Guide](docs/guides/0004-multi_agent.md)**: Multi-agent scenarios and examples
- **[Integrations Guide](docs/guides/0005.integrations.md)**: LLM and embedding provider integrations
- **[API Documentation](docs/api/overview.md)**: Complete API reference
- **[Architecture Guide](docs/architecture/overview.md)**: System architecture and design
- **[Examples](docs/examples/overview.md)**: Interactive Jupyter notebooks and use cases

## 🏗️ Architecture

powermem is built with a modular architecture that supports:

- **Core Memory Engine**: Base memory operations and intelligent management
- **Agent Framework**: Multi-agent support with collaboration and permissions
- **Storage Adapters**: Pluggable storage backends (vector, graph, and hybrid)
- **Graph Storage**: Relationship-based graph storage for complex memory interconnections
- **LLM Integrations**: Multiple LLM provider support
- **Embedding Services**: Various embedding model integrations

For detailed architecture information, see the [Architecture Guide](docs/architecture/overview.md).

## 🔧 Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/powermem/powermem.git
cd powermem

# Install development dependencies
pip install -e ".[dev,test,llm,vector_stores]"
```

## 🤝 Contributing

We welcome contributions! Please see our contributing guidelines and code of conduct.

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **Discussions**: [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.