"""
Configuration classes for the memory system.

This module provides configuration classes for different components
of the memory system.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from powermem.integrations.embeddings.configs import EmbedderConfig
from powermem.integrations.llm import LlmConfig
from powermem.storage.configs import VectorStoreConfig, GraphStoreConfig


class AgentMemoryConfig(BaseModel):
    """Configuration for agent memory management."""
    
    mode: str = "multi_agent"
    enabled: bool = True
    default_scope: str = "private"
    enable_collaboration: bool = True


class MultiAgentMemoryConfig(BaseModel):
    """Configuration for multi-agent memory management."""
    
    enabled: bool = True
    default_scope: str = "private"
    enable_collaboration: bool = True


class MultiUserConfig(BaseModel):
    """Configuration for multi-user memory management."""
    
    enabled: bool = True
    default_scope: str = "private"
    enable_collaboration: bool = True


class HybridConfig(BaseModel):
    """Configuration for hybrid memory management."""
    
    enabled: bool = True
    primary_mode: str = "multi_user"
    fallback_mode: str = "multi_agent"
    auto_switch_threshold: float = 0.8


class MemoryConfig(BaseModel):
    """Main memory configuration class."""

    vector_store: VectorStoreConfig = Field(
        description="Configuration for the vector store",
        default_factory=VectorStoreConfig,
    )
    llm: LlmConfig = Field(
        description="Configuration for the language model",
        default_factory=LlmConfig,
    )
    embedder: EmbedderConfig = Field(
        description="Configuration for the embedding model",
        default_factory=EmbedderConfig,
    )
    graph_store: GraphStoreConfig = Field(
        description="Configuration for the graph",
        default_factory=GraphStoreConfig,
    )
    version: str = Field(
        description="The version of the API",
        default="v1.1",
    )
    custom_fact_extraction_prompt: Optional[str] = Field(
        description="Custom prompt for the fact extraction",
        default=None,
    )
    custom_update_memory_prompt: Optional[str] = Field(
        description="Custom prompt for the update memory",
        default=None,
    )
    agent_memory: Optional[AgentMemoryConfig] = Field(
        description="Configuration for agent memory management",
        default=None,
    )

    
    def __init__(self, **data):
        super().__init__(**data)
        if self.agent_memory is None:
            self.agent_memory = AgentMemoryConfig()
