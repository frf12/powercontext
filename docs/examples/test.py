from powermem import create_memory
memory = create_memory()

# Search by category
results = memory.search(
    query="programming languages",
    user_id="user123"
)

print(results)

# Search with different limits
results = memory.search(
    query="user information",
    user_id="user123",
    limit=10
)

print(results)