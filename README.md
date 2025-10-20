# powermem - Intelligent Memory System

powermem is an AI-powered intelligent memory management system that provides a persistent memory layer for LLM applications.

## Features

- 🧠 **Intelligent Memory Management**: Smart memory optimization based on Ebbinghaus forgetting curve
- 🤖 **Multi-Agent Support**: Support for multi-agent collaboration and memory sharing
- 💾 **Multiple Storage Backends**: Support for OceanBase, PostgreSQL and other storage systems
- 🔌 **Rich Integrations**: Support for OpenAI, Anthropic, Ollama and other mainstream LLMs
- 📊 **Real-time Telemetry**: Complete performance monitoring and audit logging
- 🎯 **Prompt Templates**: Flexible prompt template system for different use cases

## Quick Start

```python
from mem import Memory

# Create memory instance
memory = Memory()

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
memories = memory.search("user preferences", user_id="user123")
```

## Installation

```bash
pip install powermem
```

## Documentation

For detailed documentation, please refer to the [docs/](docs/) directory.

## License

Apache License 2.0
