"""
Basic usage example for smartmem

This example demonstrates basic memory operations.
"""

from mem import Memory

def main():
    """Basic usage example."""
    # Initialize memory
    memory = Memory()
    
    # Add some memories
    memory.add("User likes coffee", user_id="user123")
    memory.add("User prefers Python over Java", user_id="user123")
    memory.add("User works as a software engineer", user_id="user123")
    
    # Search memories
    results = memory.search("user preferences", user_id="user123")
    print("Search results:")
    for result in results:
        print(f"- {result['content']}")
    
    # Get all memories
    all_memories = memory.get_all(user_id="user123")
    print(f"\nTotal memories: {len(all_memories)}")

if __name__ == "__main__":
    main()
