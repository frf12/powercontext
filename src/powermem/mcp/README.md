# powermem MCP Server

This directory contains the Model Context Protocol (MCP) server implementation for powermem, which exposes powermem's intelligent memory management capabilities as MCP tools and resources.

## What is MCP?

Model Context Protocol (MCP) is an open standard protocol that enables AI applications to securely access external tools, data sources, and services. The powermem MCP server allows AI assistants and agents to interact with powermem's memory system through a standardized interface.

## Features

The powermem MCP server provides the following capabilities:

### Tools

- **add_memory**: Add new memories to the store (supports text, messages, or conversations)
- **search_memories**: Search for memories using semantic similarity and keyword matching
- **get_memory**: Retrieve a specific memory by ID
- **update_memory**: Update an existing memory's content and metadata
- **delete_memory**: Delete a memory by ID
- **delete_all_memories**: Delete all memories for a user/agent
- **list_memories**: List all memories with optional filters

### Resources

- **powermem://memory**: Access to the memory store as a resource

## Installation

The MCP server is included with powermem. No additional installation is required.

## Usage

### Running the Server

The MCP server can be run in several ways:

#### 1. As a Python Module

```bash
python -m powermem.mcp
```

#### 2. With Configuration File

```bash
python -m powermem.mcp --config config.json
```

#### 3. With Environment File

```bash
python -m powermem.mcp --env .env
```

#### 4. Direct Python Script

```bash
python src/powermem/mcp/server.py
```

### Configuration

The server uses powermem's standard configuration system. It will:

1. Load configuration from the provided config file (if `--config` is used)
2. Load configuration from `.env` file (if `--env` is used)
3. Automatically load from `.env` in the current directory
4. Fall back to default/mock providers if no configuration is found

See the main [powermem README](../README.md) for configuration details.

## Client Configuration

To use the powermem MCP server with an MCP client (like Claude Desktop, Cursor, etc.), add it to your client's configuration.

### Example: Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "python",
      "args": [
        "-m",
        "powermem.mcp"
      ],
      "env": {
        "LLM_PROVIDER": "qwen",
        "LLM_API_KEY": "your_api_key",
        "EMBEDDING_PROVIDER": "qwen",
        "EMBEDDING_API_KEY": "your_api_key"
      }
    }
  }
}
```

### Example: Cursor / VS Code

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "python",
      "args": [
        "-m",
        "powermem.mcp"
      ],
      "env": {
        "DATABASE_PROVIDER": "sqlite",
        "LLM_PROVIDER": "openai",
        "LLM_API_KEY": "sk-...",
        "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_API_KEY": "sk-..."
      }
    }
  }
}
```

### Example: Using Configuration File

```json
{
  "mcpServers": {
    "powermem": {
      "command": "python",
      "args": [
        "-m",
        "powermem.mcp",
        "--config",
        "/path/to/config.json"
      ]
    }
  }
}
```

## Protocol

The server implements the MCP protocol over STDIO using JSON-RPC 2.0. It supports:

- **Protocol Version**: 2024-11-05
- **Communication**: STDIN/STDOUT (JSON-RPC 2.0)
- **Logging**: STDERR

## Tool Examples

### Adding a Memory

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "add_memory",
    "arguments": {
      "messages": "User prefers coffee in the morning",
      "user_id": "user123",
      "metadata": {
        "category": "preference",
        "priority": "high"
      }
    }
  }
}
```

### Searching Memories

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "search_memories",
    "arguments": {
      "query": "user preferences",
      "user_id": "user123",
      "limit": 5
    }
  }
}
```

### Getting a Memory

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "get_memory",
    "arguments": {
      "memory_id": "mem_abc123"
    }
  }
}
```

## Development

### Testing the Server

You can test the server manually using JSON-RPC requests:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python -m powermem.mcp
```

### Logging

The server logs to STDERR, so you can redirect logs:

```bash
python -m powermem.mcp 2> mcp.log
```

## User ID Handling

The MCP server automatically handles `user_id`:

- **If provided**: Uses the user_id from tool arguments
- **If not provided**: Automatically retrieves or generates a default user_id
  1. First tries to get from `~/.powermem/config.json` (via `get_user_id()`)
  2. Falls back to `"mcp_user"` if config doesn't exist

This ensures all memories are properly associated with a user_id even when Chatbox doesn't explicitly provide one.

## Troubleshooting

### Server Not Starting

- Ensure powermem is properly installed: `pip install -e .`
- Check that configuration is valid
- Review STDERR for error messages

### Tools Not Available

- Verify the server initialized successfully
- Check that the memory instance was created
- Review logs for initialization errors

### Connection Issues

- Ensure STDIO communication is working
- Check that JSON-RPC messages are properly formatted
- Verify the client is configured correctly

### User ID Issues

- Check if `~/.powermem/config.json` exists (auto-created on first run)
- Verify user_id is consistent across tool calls (should be automatic)
- If you need a specific user_id, you can pass it explicitly in tool arguments

## Chatbox Integration

See [CHATBOX.md](./CHATBOX.md) for detailed instructions on using powermem MCP server with Chatbox.

Quick setup:
1. Copy the configuration from [chatbox_config.json](./chatbox_config.json)
2. In Chatbox, go to Settings → MCP
3. Import from clipboard (paste the JSON)
4. Enable the powermem server

## License

This MCP server is part of powermem and is licensed under the Apache License 2.0.
