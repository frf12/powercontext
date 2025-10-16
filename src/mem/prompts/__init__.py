"""
Prompt templates for memory operations

This module provides prompt templates for different memory operations.
"""

from .templates import PromptTemplates
from .fact_extraction import FactExtractionPrompts
from .memory_processing import MemoryProcessingPrompts

__all__ = [
    "PromptTemplates",
    "FactExtractionPrompts",
    "MemoryProcessingPrompts",
]
