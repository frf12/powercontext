"""
powermem - Intelligent Memory System

An AI-powered intelligent memory management system that provides a persistent memory layer for LLM applications.
"""

import importlib.metadata

__version__ = importlib.metadata.version("powermem")

# Import core classes
from .core.memory import Memory
from .core.async_memory import AsyncMemory
from .core.base import MemoryBase

__all__ = [
    "Memory",
    "AsyncMemory", 
    "MemoryBase",
]
