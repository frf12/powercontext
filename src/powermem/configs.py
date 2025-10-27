"""
Configuration classes for the memory system.

This module provides configuration classes for different components
of the memory system.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel


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
    
    agent_memory: Optional[AgentMemoryConfig] = None
    
    def __init__(self, **data):
        super().__init__(**data)
        if self.agent_memory is None:
            self.agent_memory = AgentMemoryConfig()
