# Powermem Examples

This directory contains various examples demonstrating how to use powermem with different configurations and use cases.

## Examples Overview

### 1. Basic Usage (`basic_usage.py`)
- **Database**: SQLite
- **Purpose**: Simple memory operations demonstration
- **Configuration**: `configs/.env`
- **Run**: `python examples/basic_usage.py`
- **✨ Simplified**: Now uses `Memory(config=config)` for easy setup

### 2. Multi-Agent Demo (`multi_agent.py`)
- **Database**: OceanBase (configurable)
- **Purpose**: Multi-agent memory management (COMPLEX approach)
- **Features**: Agent isolation, cross-agent search, collaboration
- **Run**: `python examples/multi_agent.py`
- **⚠️ Complex**: Shows the full complexity of multi-agent features

### 3. Agent Memory Demo (`agent_memory.py`) ⭐ NEW!
- **Database**: OceanBase (configurable)
- **Purpose**: Unified interface for all agent memory scenarios
- **Features**: Auto mode detection, multi-agent, multi-user, hybrid modes
- **Run**: `python examples/agent_memory.py`
- **✨ Unified**: Single API for all scenarios, automatic mode detection

## Configuration Files

- `configs/env.example` - Template for development configuration


## Quick Start

1. **Choose your database backend**:
   - **SQLite** (simple, file-based): Use `development.env`
   - **OceanBase** (enterprise, scalable): Use `oceanbase.env`

2. **Configure your environment**:
   ```bash
   # For SQLite
   cp examples/configs/env.example examples/configs/development.env
   # Edit development.env with your settings
   
   # For OceanBase
   cp examples/configs/oceanbase.env.example examples/configs/oceanbase.env
   # Edit oceanbase.env with your OceanBase credentials
   ```

3. **Run an example**:
   ```bash
   # Basic SQLite example
   python examples/basic_usage.py
   
   # Unified agent memory demo (RECOMMENDED)
   python examples/agent_memory.py
   
   # Complex multi-agent demo (for advanced users)
   python examples/multi_agent.py
   ```

## Database Backends

### SQLite
- **Pros**: Simple setup, no external dependencies, good for development
- **Cons**: Limited scalability, single-user access
- **Use Case**: Development, testing, small applications

### OceanBase
- **Pros**: High performance, scalable, enterprise features, multi-user support
- **Cons**: Requires OceanBase installation, more complex setup
- **Use Case**: Production applications, large-scale deployments

## Unified Agent Memory Interface

The new unified interface provides a single, consistent API for all agent memory scenarios:

### Auto Mode (Recommended)
```python
from powermem.agent import AgentMemory

# Automatic mode detection
agent_memory = AgentMemory(config)

# Same API regardless of detected mode
agent_memory.add("Memory content", user_id="user123", agent_id="agent456")
results = agent_memory.search("query", user_id="user123")
```

### Multi-Agent Mode
```python
# Explicit multi-agent mode
agent_memory = AgentMemory(config, mode='multi_agent')

# Create agents
support_agent = agent_memory.create_agent("support_agent", "Customer Support")
sales_agent = agent_memory.create_agent("sales_agent", "Sales Agent")

# Agent-specific operations
support_agent.add("Customer prefers email support", user_id="customer_123")
sales_agent.add("Customer budget is $1000/month", user_id="customer_123")

# Group management
agent_memory.create_group("customer_team", ["support_agent", "sales_agent"])
```

### Multi-User Mode
```python
# Multi-user mode
agent_memory = AgentMemory(config, mode='multi_user')

# User-specific memories
agent_memory.add("Alice likes Python", user_id="alice")
agent_memory.add("Bob prefers Java", user_id="bob")

# User-specific search
alice_memories = agent_memory.search("Python", user_id="alice")
```

### Hybrid Mode
```python
# Hybrid mode with dynamic switching
agent_memory = AgentMemory(config, mode='hybrid')

# Automatic context detection
agent_memory.add("Support agent handled complaint", agent_id="support_agent")
agent_memory.add("User Alice requested features", user_id="alice")

# Mode switching
agent_memory.switch_mode('multi_agent')
```

### Key Benefits
- **Single API** - Consistent interface across all modes
- **Automatic Detection** - Intelligent mode selection
- **Easy Migration** - Simple upgrade from existing code
- **Mode Flexibility** - Switch between modes as needed

## Configuration Examples

### SQLite Configuration
```env
DATABASE_PROVIDER=sqlite
DATABASE_PATH=./data/powermem_dev.db
DATABASE_ENABLE_WAL=true
DATABASE_TIMEOUT=30
```

### OceanBase Configuration
```env
DATABASE_PROVIDER=oceanbase
DATABASE_HOST=localhost
DATABASE_PORT=2881
DATABASE_USER=root
DATABASE_PASSWORD=password
DATABASE_NAME=test
DATABASE_COLLECTION_NAME=memories
DATABASE_VECTOR_METRIC_TYPE=cosine
DATABASE_INDEX_TYPE=ivfflat
EMBEDDING_DIMS=1536
```

## Features Demonstrated

- ✅ **Memory Storage**: Add, update, delete memories
- ✅ **Semantic Search**: Find similar memories using vector similarity
- ✅ **Multi-user Support**: Isolate memories by user ID
- ✅ **Multi-agent Support**: Agent-specific memory management
- ✅ **Metadata Support**: Attach custom metadata to memories
- ✅ **Real-time Operations**: Immediate memory operations
- ✅ **Vector Embeddings**: High-dimensional vector storage
- ✅ **Configuration Management**: Environment-based configuration

## Dependencies

### Common Dependencies
- `python-dotenv` - Environment variable loading
- `dashscope` - Qwen API integration

### OceanBase Dependencies
- `pyobvector` - OceanBase vector operations
- `sqlalchemy` - Database ORM

Install all dependencies:
```bash
pip install python-dotenv dashscope pyobvector sqlalchemy
```

## Troubleshooting

### Common Issues
1. **Import Errors**: Ensure all dependencies are installed
2. **Configuration Errors**: Check environment variable values
3. **Connection Issues**: Verify database connectivity
4. **API Key Issues**: Ensure valid API keys are configured

### Getting Help
- Check individual example documentation
- Review configuration templates
- Verify database connectivity
- Check API key validity
