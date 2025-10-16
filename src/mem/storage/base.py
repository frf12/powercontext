"""
Abstract base class for storage implementations

This module defines the storage interface that all implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import asyncio


class StorageBase(ABC):
    """
    Abstract base class for storage implementations.
    
    This class defines the interface that all storage backends must implement.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize storage backend.
        
        Args:
            config: Storage configuration
        """
        self.config = config
    
    @abstractmethod
    def initialize(self) -> None:
        """Initialize the storage backend."""
        pass
    
    @abstractmethod
    async def initialize_async(self) -> None:
        """Initialize the storage backend asynchronously."""
        pass
    
    @abstractmethod
    def add_memory(self, memory_data: Dict[str, Any]) -> str:
        """
        Add a memory to storage.
        
        Args:
            memory_data: Memory data dictionary
            
        Returns:
            Memory ID
        """
        pass
    
    @abstractmethod
    async def add_memory_async(self, memory_data: Dict[str, Any]) -> str:
        """
        Add a memory to storage asynchronously.
        
        Args:
            memory_data: Memory data dictionary
            
        Returns:
            Memory ID
        """
        pass
    
    @abstractmethod
    def search_memories(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search for memories using vector similarity.
        
        Args:
            query_embedding: Query vector embedding
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            filters: Additional filters
            limit: Maximum number of results
            
        Returns:
            List of matching memories
        """
        pass
    
    @abstractmethod
    async def search_memories_async(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search for memories using vector similarity asynchronously.
        
        Args:
            query_embedding: Query vector embedding
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            filters: Additional filters
            limit: Maximum number of results
            
        Returns:
            List of matching memories
        """
        pass
    
    @abstractmethod
    def get_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific memory by ID.
        
        Args:
            memory_id: Memory ID
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Memory data if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def get_memory_async(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific memory by ID asynchronously.
        
        Args:
            memory_id: Memory ID
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Memory data if found, None otherwise
        """
        pass
    
    @abstractmethod
    def update_memory(
        self,
        memory_id: str,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing memory.
        
        Args:
            memory_id: Memory ID
            update_data: Data to update
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Updated memory data
        """
        pass
    
    @abstractmethod
    async def update_memory_async(
        self,
        memory_id: str,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing memory asynchronously.
        
        Args:
            memory_id: Memory ID
            update_data: Data to update
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Updated memory data
        """
        pass
    
    @abstractmethod
    def delete_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Delete a memory.
        
        Args:
            memory_id: Memory ID
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            True if deleted successfully, False otherwise
        """
        pass
    
    @abstractmethod
    async def delete_memory_async(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Delete a memory asynchronously.
        
        Args:
            memory_id: Memory ID
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            True if deleted successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def get_all_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get all memories with optional filtering.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of memories
        """
        pass
    
    @abstractmethod
    async def get_all_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get all memories with optional filtering asynchronously.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of memories
        """
        pass
    
    @abstractmethod
    def clear_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Clear all memories for a user or agent.
        
        Args:
            user_id: Clear memories for specific user
            agent_id: Clear memories for specific agent
            
        Returns:
            True if cleared successfully, False otherwise
        """
        pass
    
    @abstractmethod
    async def clear_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Clear all memories for a user or agent asynchronously.
        
        Args:
            user_id: Clear memories for specific user
            agent_id: Clear memories for specific agent
            
        Returns:
            True if cleared successfully, False otherwise
        """
        pass
