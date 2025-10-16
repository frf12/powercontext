"""
LLM factory for creating LLM instances

This module provides a factory for creating different LLM instances.
"""

import logging
from typing import Any, Dict, Type
from .base import LLMBase

logger = logging.getLogger(__name__)


class LLMFactory:
    """
    Factory for creating LLM instances.
    """
    
    _llm_registry: Dict[str, Type[LLMBase]] = {}
    
    @classmethod
    def register_llm(cls, name: str, llm_class: Type[LLMBase]) -> None:
        """
        Register an LLM implementation.
        
        Args:
            name: LLM name
            llm_class: LLM implementation class
        """
        cls._llm_registry[name] = llm_class
        logger.info(f"Registered LLM: {name}")
    
    @classmethod
    def create(cls, llm_type: str, config: Dict[str, Any]) -> LLMBase:
        """
        Create an LLM instance.
        
        Args:
            llm_type: Type of LLM to create
            config: LLM configuration
            
        Returns:
            LLM instance
            
        Raises:
            ValueError: If LLM type is not supported
        """
        if llm_type not in cls._llm_registry:
            raise ValueError(f"Unsupported LLM type: {llm_type}")
        
        llm_class = cls._llm_registry[llm_type]
        return llm_class(config)
    
    @classmethod
    def get_supported_llms(cls) -> list:
        """
        Get list of supported LLM types.
        
        Returns:
            List of supported LLM types
        """
        return list(cls._llm_registry.keys())


# Register built-in LLM implementations
def register_builtin_llms():
    """Register built-in LLM implementations."""
    try:
        from .openai import OpenAILLM
        LLMFactory.register_llm("openai", OpenAILLM)
    except ImportError:
        logger.warning("OpenAI LLM not available")
    
    try:
        from .anthropic import AnthropicLLM
        LLMFactory.register_llm("anthropic", AnthropicLLM)
    except ImportError:
        logger.warning("Anthropic LLM not available")
    
    try:
        from .ollama import OllamaLLM
        LLMFactory.register_llm("ollama", OllamaLLM)
    except ImportError:
        logger.warning("Ollama LLM not available")


# Auto-register built-in LLMs
register_builtin_llms()
