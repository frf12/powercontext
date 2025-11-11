# Scenario 7: Multimodal Capability

This scenario demonstrates PowerMem's multimodal capability - storing and retrieving images, audio, and other multimedia content.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- Multimodal LLM API support

## Configuration

### Create `.env` File

1. Copy the example configuration file:
   ```bash
   cp configs/minienv.example .env
   ```

2. Edit the `.env` file and configure multimodal parameters

> **Note:** Multimodal functionality requires vision-capable LLM models such as `gpt-4o`, `gpt-4-vision-preview`, or `qwen-vl-plus`.

## What is Multimodal Capability?

Multimodal capability allows PowerMem to process more than just text:
- **Images**: Extract information from images and generate text descriptions
- **Image URLs**: Process online image links
- **Mixed Content**: Handle composite messages with both text and images

PowerMem automatically converts image content to text descriptions, stores them as memories, making the image content searchable and retrievable.

## Step 1: Add Image Memory Using OpenAI Multimodal Format

Add image-containing memories using the standard OpenAI multimodal message format:

```python
# multimodal_example.py
import os
from powermem import Memory

config = {
    "llm": {
        "provider": "openai",  # Use OpenAI-compatible multimodal model
        "config": {
            "model": "qwen-vl-plus",
            "enable_vision": True,  # Key: Enable vision processing
            "vision_details": "auto",  # Image analysis precision: auto/low/high
            "api_key": "your-api-key-here",
            "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    },
    "vector_store": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "simple_test",
            "embedding_model_dims": 1536,
            "host": os.getenv("OCEANBASE_HOST", "6.12.233.170"),
            "port": int(os.getenv("OCEANBASE_PORT", "2881")),
            "user": os.getenv("OCEANBASE_USER", "root@test_tenant"),
            "password": os.getenv("OCEANBASE_PASSWORD", "123456"),
            "db_name": os.getenv("OCEANBASE_DB", "test_hybrid"),
        }
    },
    "version": "v1.1",
    "embedding": {
        "provider": "qwen",
        "config": {
            "model": "text-embedding-v4",
            "embedding_dims": 1536,
            "api_key": "your-api-key-here",
        }
    },
}

memory = Memory(config=config)

# OpenAI multimodal message format
messages_multimodal = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "This is Bob's favorite working state"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.com/workspace.jpg",
                    "detail": "auto"  # Optional: auto/low/high
                }
            },
        ]
    }
]

# Add image memory
result = memory.add(
    messages=messages_multimodal,
    user_id="test_user",
    metadata={"type": "workspace_photo", "source": "user_upload"}
)

print(f"✅ Successfully added image memory")
print(f"   Memory ID: {result.get('id')}")
print(f"   Processed content: {result}")
```

**Run this code:**
```bash
python multimodal_example.py
```

**Expected output:**
```
✅ Successfully added image memory
   Memory ID: xxxxxx
   Processed content: {...}
```

## Step 2: Search Image-Related Memories

Search for previously added image memories:

```python
# multimodal_example.py
from powermem import Memory

memory = Memory(config=config)

# Search for image-related memories
query = "Bob's favorite working state"

print(f"\nQuery: '{query}'")
results = memory.search(query=query, user_id="test_user", limit=3)

if results.get("results"):
    for idx, mem in enumerate(results["results"], 1):
        print(f"  Result {idx}: {mem.get('memory', '')[:100]}...")
        print(f"    Similarity: {mem.get('score', 0):.4f}")
        print(f"    Metadata: {mem.get('metadata', {})}")
else:
    print("  No related memories found")
```

**Run this code:**
```bash
python multimodal_example.py
```

**Expected output:**
```
Query: 'Bob's favorite working state'
  Result 1: This is Bob's favorite working state...
    Similarity: 0.8934
    Metadata: {'type': 'workspace_photo', 'source': 'user_upload'}
```

## Configuration Options

### Vision-Related Configuration

```python
config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "gpt-4o",  # Or gpt-4-vision-preview, qwen-vl-plus
            "enable_vision": True,  # Required: Enable vision processing
            "vision_details": "auto",  # Optional: Image analysis precision
            # auto: Automatic selection (recommended)
            # low: Lower precision, faster
            # high: Higher precision, more detailed but slower
        }
    }
}
```

### Supported Models

- **OpenAI**: `gpt-4o`, `gpt-4-vision-preview`, `gpt-4-turbo`
- **Qwen**: `qwen-vl-plus`, `qwen-vl-max`
- **Others**: Any model supporting OpenAI vision API format

## Next Steps

- **Scenario 2**: Learn about intelligent memory features
- **Scenario 4**: Explore async operations
- **API Reference**: See [Memory API](../api/0001-memory.md)
- **Configuration Guide**: See [Configuration Guide](../guides/0002-configuration.md)

