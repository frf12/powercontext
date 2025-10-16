"""
Abstract base class for LLM implementations

This module defines the LLM interface that all implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LLMBase(ABC):
    """
    Abstract base class for LLM implementations.
    
    This class defines the interface that all LLM providers must implement.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize LLM provider.
        
        Args:
            config: LLM configuration
        """
        self.config = config
    
    @abstractmethod
    def generate(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Generate text using the LLM.
        
        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional parameters
            
        Returns:
            Generated text
        """
        pass
    
    @abstractmethod
    async def generate_async(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Generate text using the LLM asynchronously.
        
        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional parameters
            
        Returns:
            Generated text
        """
        pass
    
    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Generate chat response using the LLM.
        
        Args:
            messages: List of chat messages
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional parameters
            
        Returns:
            Generated response
        """
        pass
    
    @abstractmethod
    async def chat_async(
        self,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """
        Generate chat response using the LLM asynchronously.
        
        Args:
            messages: List of chat messages
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional parameters
            
        Returns:
            Generated response
        """
        pass
