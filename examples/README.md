# Powermem Examples

This directory contains various examples demonstrating how to use powermem with different configurations and use cases.

## Examples Overview

### 1. Basic Usage (`basic_usage.py`)
- **Database**: SQLite
- **Purpose**: Simple memory operations demonstration
- **Configuration**: `configs/.env`
- **Run**: `python examples/basic_usage.py`

### 2. Multi-Agent Demo (`multi_agent.py`)
- **Database**: SQLite (default)
- **Purpose**: Multi-agent memory management
- **Features**: Agent isolation, cross-agent search
- **Run**: `python examples/multi_agent.py`

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
   
   # Multi-agent demo
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
