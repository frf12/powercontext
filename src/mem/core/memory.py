"""
Synchronous memory management implementation

This module provides the synchronous memory management interface.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from .base import MemoryBase
from ..storage.factory import StorageFactory
from ..intelligence.manager import IntelligenceManager
from ..integrations.llm.factory import LLMFactory
from ..integrations.embeddings.factory import EmbeddingFactory
from .telemetry import TelemetryManager
from .audit import AuditLogger

logger = logging.getLogger(__name__)


class Memory(MemoryBase):
    """
    Synchronous memory management implementation.
    
    This class provides the main interface for synchronous memory operations.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        storage_type: str = "sqlite",
        llm_provider: str = "openai",
        embedding_provider: str = "openai",
    ):
        """
        Initialize the memory manager.
        
        Args:
            config: Configuration dictionary
            storage_type: Type of storage backend to use
            llm_provider: LLM provider to use
            embedding_provider: Embedding provider to use
        """
        self.config = config or {}
        self.storage_type = storage_type
        self.llm_provider = llm_provider
        self.embedding_provider = embedding_provider
        
        # Initialize components
        self.storage = StorageFactory.create(storage_type, self.config)
        self.llm = LLMFactory.create(llm_provider, self.config)
        self.embedding = EmbeddingFactory.create(embedding_provider, self.config)
        self.intelligence = IntelligenceManager(self.config)
        self.telemetry = TelemetryManager(self.config)
        self.audit = AuditLogger(self.config)
        
        # Initialize storage
        self.storage.initialize()
        
        logger.info(f"Memory initialized with storage: {storage_type}, LLM: {llm_provider}")
        self.telemetry.capture_event("memory.init", {"storage_type": storage_type, "llm_provider": llm_provider})
    
    def add(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a new memory."""
        try:
            # Generate embedding
            embedding = self.embedding.embed(content)
            
            # Process with intelligence manager
            processed_content = self.intelligence.process_content(content, metadata)
            
            # Store in database
            memory_data = {
                "content": processed_content,
                "embedding": embedding,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": metadata or {},
                "filters": filters or {},
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            
            memory_id = self.storage.add_memory(memory_data)
            
            # Log audit event
            self.audit.log_event("memory.add", {
                "memory_id": memory_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "content_length": len(content)
            })
            
            # Capture telemetry
            self.telemetry.capture_event("memory.add", {
                "memory_id": memory_id,
                "user_id": user_id,
                "agent_id": agent_id
            })
            
            return {
                "id": memory_id,
                "content": processed_content,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": metadata,
                "created_at": memory_data["created_at"],
            }
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            self.telemetry.capture_event("memory.add.error", {"error": str(e)})
            raise
    
    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for memories."""
        try:
            # Generate query embedding
            query_embedding = self.embedding.embed(query)
            
            # Search in storage
            results = self.storage.search_memories(
                query_embedding=query_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=limit
            )
            
            # Process results with intelligence manager
            processed_results = self.intelligence.process_search_results(results, query)
            
            # Log audit event
            self.audit.log_event("memory.search", {
                "query": query,
                "user_id": user_id,
                "agent_id": agent_id,
                "results_count": len(processed_results)
            })
            
            # Capture telemetry
            self.telemetry.capture_event("memory.search", {
                "user_id": user_id,
                "agent_id": agent_id,
                "results_count": len(processed_results)
            })
            
            return processed_results
            
        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            self.telemetry.capture_event("memory.search.error", {"error": str(e)})
            raise
    
    def get(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID."""
        try:
            result = self.storage.get_memory(memory_id, user_id, agent_id)
            
            if result:
                self.audit.log_event("memory.get", {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "agent_id": agent_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            raise
    
    def update(
        self,
        memory_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory."""
        try:
            # Generate new embedding
            embedding = self.embedding.embed(content)
            
            # Process with intelligence manager
            processed_content = self.intelligence.process_content(content, metadata)
            
            # Update in storage
            update_data = {
                "content": processed_content,
                "embedding": embedding,
                "metadata": metadata,
                "updated_at": datetime.utcnow(),
            }
            
            result = self.storage.update_memory(memory_id, update_data, user_id, agent_id)
            
            # Log audit event
            self.audit.log_event("memory.update", {
                "memory_id": memory_id,
                "user_id": user_id,
                "agent_id": agent_id
            })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise
    
    def delete(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory."""
        try:
            result = self.storage.delete_memory(memory_id, user_id, agent_id)
            
            if result:
                self.audit.log_event("memory.delete", {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "agent_id": agent_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
    
    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering."""
        try:
            results = self.storage.get_all_memories(user_id, agent_id, limit, offset)
            
            self.audit.log_event("memory.get_all", {
                "user_id": user_id,
                "agent_id": agent_id,
                "limit": limit,
                "offset": offset,
                "results_count": len(results)
            })
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get all memories: {e}")
            raise
    
    def clear(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent."""
        try:
            result = self.storage.clear_memories(user_id, agent_id)
            
            if result:
                self.audit.log_event("memory.clear", {
                    "user_id": user_id,
                    "agent_id": agent_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to clear memories: {e}")
            raise
