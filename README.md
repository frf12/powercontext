[English](README.md) | [中文](README_CN.md) | [日本語](README_JP.md)

<p align="center">
  <a href="https://powermem.ai">Learn more</a>
  ·
  <a href="https://discord.com/invite/74cF8vbNEs">Join Discord</a>
  ·
  <a href="https://powermem.ai/benchmark">Benchmark Result</a>
</p>

<p align="center">
    <a href="https://pepy.tech/project/powermem">
        <img src="https://img.shields.io/pypi/dm/powermem" alt="PowerMem PyPI - Downloads">
    </a>
    <a href="https://github.com/oceanbase/powermem">
        <img src="https://img.shields.io/github/commit-activity/m/oceanbase/powermem?style=flat-square" alt="GitHub commit activity">
    </a>
    <a href="https://pypi.org/project/powermem" target="blank">
        <img src="https://img.shields.io/pypi/v/powermem?color=%2334D058&label=pypi%20package" alt="Package version">
    </a>
    <a href="https://github.com/oceanbase/powermem/blob/master/LICENSE">
        <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-green.svg" />
    </a>
    <a href="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg">
        <img alt="pyversions" src="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg" />
    </a>
    <a href="https://deepwiki.com/oceanbase/powermem">
        <img alt="Ask DeepWiki" src="https://deepwiki.com/badge.svg" />
    </a>
</p>

<p align="center">
  <strong>⚡ +48.53% Accuracy vs. OpenAI Memory • 🚀 91.76% Faster • 💰 96.53% Fewer Tokens</strong>
</p>

## Highlights

**More Accurate**: **[+48.53% Accuracy]** Outperforms OpenAI Memory in the LOCOMO benchmark
**Faster**: **[91.76% Faster Response]** Compared to full-context methods, ensuring low latency for large-scale applications
**More Cost-Effective**: **[96.53% Fewer Tokens]** Significantly reduces costs compared to full-context methods without sacrificing performance

- [See Benchmark Details](https://powermem.ai/benchmark)

# PowerMem - Intelligent Memory System

In AI application development, enabling large language models to persistently "remember" historical conversations, user preferences, and contextual information is a core challenge. PowerMem combines a hybrid storage architecture of vector retrieval, full-text search, and graph databases, and introduces the Ebbinghaus forgetting curve theory from cognitive science to build a powerful memory infrastructure for AI applications. The system also provides comprehensive multi-agent support capabilities, including agent memory isolation, cross-agent collaboration and sharing, fine-grained permission control, and privacy protection mechanisms, enabling multiple AI agents to achieve efficient collaboration while maintaining independent memory spaces.

## Architecture

![Architecture Diagram](docs/images/powermem_en.png)

PowerMem is built with a modular architecture that supports:

- **Core Memory Engine**: Base memory operations and intelligent management
- **Agent Framework**: Multi-agent support with collaboration and permissions
- **Storage Adapters**: Pluggable storage backends (vector, graph, and hybrid)
- **Graph Storage**: Relationship-based graph storage for complex memory interconnections
- **LLM Integrations**: Multiple LLM provider support
- **Embedding Services**: Various embedding model integrations

For detailed architecture information, see the [Architecture Guide](docs/architecture/overview.md).

## Key Features

### Intelligent Memory Management
- **Intelligent Memory Extraction**: Extract memories through LLM models
- **Ebbinghaus Forgetting Curve**: Smart memory optimization based on cognitive science
- **Automatic Importance Scoring**: AI-powered memory importance evaluation
- **Memory Decay & Reinforcement**: Dynamic memory retention based on usage patterns
- **Intelligent Retrieval**: Context-aware memory search and ranking

### Multi-Agent Support
- **Agent Isolation**: Separate memory spaces for different agents
- **Cross-Agent Collaboration**: Shared memory access and collaboration tracking
- **Permission Control**: Fine-grained access control for agent memories
- **Privacy Protection**: Built-in privacy controls and data protection

### Deeply Optimized Data Storage
- **Multiple Database Support**: Extensible storage architecture supporting OceanBase, SeekDB, PostgreSQL, SQLite, and more
- **Sub Stores Support**: Partition data management through sub stores to handle ultra-large-scale data
- **Hybrid Retrieval**: Hybrid retrieval capabilities supporting vector search, full-text search, and graph retrieval
- **Knowledge Graph**: Support both LLM and NLP modes to extract entities and relationships for building knowledge graphs
- **Graph Retrieval**: Multi-hop graph traversal for complex memory relationships
- **Relationship Search**: Discover connections between memories through graph queries
- **Hybrid Storage**: Combine vector search with graph relationships for enhanced retrieval

### Developer Friendly
- **Lightweight Integration**: Support Python SDK/MCP integration, compatible with mem0 usage

## Quick Start

### Installation

```bash
# Production environment, includes LLM and vector store dependencies
pip install powermem[llm,vector_stores]

# Development environment, includes all dependencies
pip install powermem[dev,test,llm,vector_stores,extras]
```

### Basic Usage

**✨ Simplest Way**: Create memory from `.env` file automatically! [Configuration Reference](configs/env.example)

```python
from powermem import create_memory

# Automatically loads from .env
memory = create_memory()

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
memories = memory.search("user preferences", user_id="user123")
for memory in memories:
    print(f"- {memory.get('memory')}")
```

For more detailed examples and usage patterns, see the [Getting Started Guide](docs/guides/0001-getting_started.md).

## Integrations & Demos

- **Langgraph Integration**: Build customer chatbots using LangGraph + PowerMem ([Example](examples))
- **LangChain Integration**: Build multi-agent systems using LangChain + PowerMem ([Example](examples))

## Documentation

- **[Getting Started](docs/guides/0001-getting_started.md)**: Installation and quick start guide
- **[Configuration Guide](docs/guides/0002-configuration.md)**: Complete configuration options
- **[Multi-Agent Guide](docs/guides/0004-multi_agent.md)**: Multi-agent scenarios and examples
- **[Integrations Guide](docs/guides/0005-integrations.md)**: LLM and embedding provider integrations
- **[Sub Stores Guide](docs/guides/0006-sub_stores.md)**: Sub stores usage and examples
- **[API Documentation](docs/api/overview.md)**: Complete API reference
- **[Architecture Guide](docs/architecture/overview.md)**: System architecture and design
- **[Examples](docs/examples/overview.md)**: Interactive Jupyter notebooks and use cases

## Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/powermem/powermem.git
cd powermem

# Install development dependencies
pip install -e ".[dev,test,llm,vector_stores]"
```

## Contributing

We welcome contributions! Please see our contributing guidelines and code of conduct.

## Support

- **Issues**: [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **Discussions**: [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.