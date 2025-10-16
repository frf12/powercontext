"""
Embedding factory for creating embedding instances

This module provides a factory for creating different embedding instances.
"""

import logging
from typing import Any, Dict, Type
from .base import EmbeddingBase

logger = logging.getLogger(__name__)


class EmbeddingFactory:
    """
    Factory for creating embedding instances.
    """
    
    _embedding_registry: Dict[str, Type[EmbeddingBase]] = {}
    
    @classmethod
    def register_embedding(cls, name: str, embedding_class: Type[EmbeddingBase]) -> None:
        """
        Register an embedding implementation.
        
        Args:
            name: Embedding name
            embedding_class: Embedding implementation class
        """
        cls._embedding_registry[name] = embedding_class
        logger.info(f"Registered embedding: {name}")
    
    @classmethod
    def create(cls, embedding_type: str, config: Dict[str, Any]) -> EmbeddingBase:
        """
        Create an embedding instance.
        
        Args:
            embedding_type: Type of embedding to create
            config: Embedding configuration
            
        Returns:
            Embedding instance
            
        Raises:
            ValueError: If embedding type is not supported
        """
        if embedding_type not in cls._embedding_registry:
            raise ValueError(f"Unsupported embedding type: {embedding_type}")
        
        embedding_class = cls._embedding_registry[embedding_type]
        return embedding_class(config)
    
    @classmethod
    def get_supported_embeddings(cls) -> list:
        """
        Get list of supported embedding types.
        
        Returns:
            List of supported embedding types
        """
        return list(cls._embedding_registry.keys())


# Register built-in embedding implementations
def register_builtin_embeddings():
    """Register built-in embedding implementations."""
    try:
        from .openai import OpenAIEmbedding
        EmbeddingFactory.register_embedding("openai", OpenAIEmbedding)
    except ImportError:
        logger.warning("OpenAI embedding not available")
    
    try:
        from .huggingface import HuggingFaceEmbedding
        EmbeddingFactory.register_embedding("huggingface", HuggingFaceEmbedding)
    except ImportError:
        logger.warning("HuggingFace embedding not available")


# Auto-register built-in embeddings
register_builtin_embeddings()
