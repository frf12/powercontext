"""
Multi-agent demo for powermem

This example demonstrates multi-agent memory management.
"""

from mem import Memory

def main():
    """Multi-agent demo."""
    # Initialize memory
    memory = Memory()
    
    # Agent 1 memories
    memory.add("Customer prefers email support", user_id="user123", agent_id="support_agent")
    memory.add("Customer has premium subscription", user_id="user123", agent_id="support_agent")
    
    # Agent 2 memories
    memory.add("Customer interested in AI features", user_id="user123", agent_id="sales_agent")
    memory.add("Customer budget is $1000/month", user_id="user123", agent_id="sales_agent")
    
    # Search from support agent perspective
    support_results = memory.search("customer preferences", user_id="user123", agent_id="support_agent")
    print("Support agent view:")
    for result in support_results:
        print(f"- {result['content']}")
    
    # Search from sales agent perspective
    sales_results = memory.search("customer budget", user_id="user123", agent_id="sales_agent")
    print("\nSales agent view:")
    for result in sales_results:
        print(f"- {result['content']}")
    
    # Global search (all agents)
    global_results = memory.search("customer", user_id="user123")
    print(f"\nGlobal view ({len(global_results)} results):")
    for result in global_results:
        print(f"- {result['content']} (Agent: {result.get('agent_id', 'N/A')})")

if __name__ == "__main__":
    main()
