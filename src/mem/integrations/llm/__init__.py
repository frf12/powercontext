"""
LLM integration module

This module provides LLM integrations and factory.
"""
from .configs import LLMConfig
from .factory import LLMFactory

# provider alias name 
LlmFactory = LLMFactory
LlmConfig = LLMConfig

__all__ = [
    "LlmFactory",
    "LlmConfig"
]
