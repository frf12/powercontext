#!/usr/bin/env python3
"""
Benchmark script for powermem

This script runs performance benchmarks for powermem.
"""

import time
import asyncio
import logging
from typing import List, Dict, Any
from powermem import Memory, AsyncMemory

logger = logging.getLogger(__name__)

def benchmark_memory_operations():
    """Benchmark memory operations."""
    print("Running memory operations benchmark...")
    
    memory = Memory()
    results = {}
    
    # Benchmark add operations
    start_time = time.time()
    for i in range(100):
        memory.add(f"Test memory {i}", user_id="benchmark_user")
    add_time = time.time() - start_time
    results["add_100_memories"] = add_time
    
    # Benchmark search operations
    start_time = time.time()
    for i in range(10):
        memory.search("test", user_id="benchmark_user")
    search_time = time.time() - start_time
    results["search_10_queries"] = search_time
    
    # Benchmark get operations
    memories = memory.get_all(user_id="benchmark_user", limit=10).get("results", [])
    start_time = time.time()
    for memory_data in memories:
        memory.get(memory_data["id"], user_id="benchmark_user")
    get_time = time.time() - start_time
    results["get_10_memories"] = get_time
    
    # Cleanup
    memory.clear(user_id="benchmark_user")
    
    return results

async def benchmark_async_memory_operations():
    """Benchmark async memory operations."""
    print("Running async memory operations benchmark...")
    
    memory = AsyncMemory()
    await memory.initialize()
    results = {}
    
    # Benchmark add operations
    start_time = time.time()
    for i in range(100):
        await memory.add(f"Test memory {i}", user_id="benchmark_user")
    add_time = time.time() - start_time
    results["async_add_100_memories"] = add_time
    
    # Benchmark search operations
    start_time = time.time()
    for i in range(10):
        await memory.search("test", user_id="benchmark_user")
    search_time = time.time() - start_time
    results["async_search_10_queries"] = search_time
    
    # Cleanup
    await memory.clear(user_id="benchmark_user")
    
    return results

def print_results(results: Dict[str, float]):
    """Print benchmark results."""
    print("\nBenchmark Results:")
    print("=" * 50)
    for operation, duration in results.items():
        print(f"{operation}: {duration:.4f} seconds")
    print("=" * 50)

def main():
    """Main benchmark function."""
    print("powermem Performance Benchmark")
    print("=" * 50)
    
    # Run sync benchmarks
    sync_results = benchmark_memory_operations()
    print_results(sync_results)
    
    # Run async benchmarks
    async_results = asyncio.run(benchmark_async_memory_operations())
    print_results(async_results)
    
    print("Benchmark completed!")

if __name__ == "__main__":
    main()
