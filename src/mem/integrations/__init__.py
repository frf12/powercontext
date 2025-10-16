"""
Integration layer for external services

This module provides integrations with LLMs, embeddings, and other services.
"""

from .llm.factory import LLMFactory
from .embeddings.factory import EmbeddingFactory

__all__ = [
    "LLMFactory",
    "EmbeddingFactory",
]
