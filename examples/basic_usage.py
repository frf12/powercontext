"""
Basic usage example for powermem

This example demonstrates basic memory operations.

Setup:
1. Copy examples/configs/env.example to examples/configs/.env
2. Add your API keys to examples/configs/.env
3. Run this script

Or simply run without config - it will use mock providers for demonstration.
"""

import os
from powermem import create_memory


def main():
    """Basic usage example."""
    print("=" * 60)
    print("Powermem Basic Usage Example")
    print("=" * 60)
    
    # Check if .env exists
    env_path = os.path.join(os.path.dirname(__file__), "configs", ".env")
    env_example_path = os.path.join(os.path.dirname(__file__), "configs", "env.example")
    
    if not os.path.exists(env_path):
        print(f"\n No .env file found at: {env_path}")
        print(f"To add your API keys:")
        print(f"   1. Copy: cp {env_example_path} {env_path}")
        print(f"   2. Edit {env_path} and add your API keys")
        print(f"\n  For now, using mock providers for demonstration...")
    else:
        print(f"Found .env file")
    
    print("\nInitializing memory...")
    
    # Simplest way: create_memory() automatically loads from .env
    # If no API keys are provided, it will use mock providers
    memory = create_memory()
    
    print("✓ Memory initialized successfully!\n")
    
    # Add some memories
    print("Adding memories...")
    memory.add("User likes coffee", user_id="user123")
    memory.add("User prefers Python over Java", user_id="user123")
    memory.add("User works as a software engineer", user_id="user123")
    print("✓ Memories added!\n")
    
    # Search memories
    print("Searching memories...")
    search_response = memory.search("user preferences", user_id="user123")
    results = search_response.get('results', [])
    print(f"Found {len(results)} results:")
    for result in results:
        # Search returns {"memory": "content", "metadata": {...}, "score": ...}
        content = result.get('memory', result.get('content', 'No content'))
        print(f"- {content}")
    
    # Get all memories
    all_memories = memory.get_all(user_id="user123")
    print(f"\n✓ Total memories: {len(all_memories)}")

if __name__ == "__main__":
    main()
