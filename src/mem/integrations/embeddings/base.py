"""
Abstract base class for embedding implementations

This module defines the embedding interface that all implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class EmbeddingBase(ABC):
    """
    Abstract base class for embedding implementations.
    
    This class defines the interface that all embedding providers must implement.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize embedding provider.
        
        Args:
            config: Embedding configuration
        """
        self.config = config
    
    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        pass
    
    @abstractmethod
    async def embed_async(self, text: str) -> List[float]:
        """
        Generate embedding for a single text asynchronously.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        pass
    
    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        pass
    
    @abstractmethod
    async def embed_batch_async(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts asynchronously.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        pass
    
    @abstractmethod
    def get_dimensions(self) -> int:
        """
        Get the dimensionality of the embeddings.
        
        Returns:
            Number of dimensions
        """
        pass
