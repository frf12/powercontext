"""
Asynchronous memory management implementation

This module provides the asynchronous memory management interface.
"""

import asyncio
import logging
import hashlib
from typing import Any, Dict, List, Optional
from datetime import datetime

from .base import MemoryBase
from ..storage.factory import VectorStoreFactory, GraphStoreFactory
from ..intelligence.manager import IntelligenceManager
from ..integrations.llm.factory import LLMFactory
from ..integrations.embeddings.factory import EmbedderFactory
from .telemetry import TelemetryManager
from .audit import AuditLogger
from ..intelligence.plugin import IntelligentMemoryPlugin, EbbinghausIntelligencePlugin
from ..agent.plugin import AgentPlugin, FactoryBackedAgentPlugin

logger = logging.getLogger(__name__)


class AsyncMemory(MemoryBase):
    """
    Asynchronous memory management implementation.
    
    This class provides the main interface for asynchronous memory operations.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        storage_type: str = "sqlite",
        llm_provider: str = "openai",
        embedding_provider: str = "openai",
    ):
        """
        Initialize the async memory manager.
        
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
        self.storage = VectorStoreFactory.create(storage_type, self.config)
        self.llm = LLMFactory.create(llm_provider, self.config)
        self.embedding = EmbedderFactory.create(embedding_provider, self.config)
        self.intelligence = IntelligenceManager(self.config)
        self.telemetry = TelemetryManager(self.config)
        self.audit = AuditLogger(self.config)

        # Intelligent memory plugin (pluggable)
        intelligence_cfg = (self.config or {}).get("intelligence", {})
        plugin_type = intelligence_cfg.get("plugin", "ebbinghaus")
        self._intelligence_plugin: Optional[IntelligentMemoryPlugin] = None
        if intelligence_cfg.get("enabled", False):
            try:
                if plugin_type == "ebbinghaus":
                    self._intelligence_plugin = EbbinghausIntelligencePlugin(intelligence_cfg)
                else:
                    logger.warning(f"Unknown intelligence plugin: {plugin_type}")
            except Exception as e:
                logger.warning(f"Failed to initialize intelligence plugin (async): {e}")
                self._intelligence_plugin = None

        # Agent orchestration plugin
        agent_cfg = (self.config or {}).get("agent", {})
        self._agent_plugin: Optional[AgentPlugin] = None
        if agent_cfg.get("enabled", False):
            try:
                self._agent_plugin = FactoryBackedAgentPlugin(agent_cfg)
            except Exception:
                self._agent_plugin = AgentPlugin(agent_cfg)
        
        logger.info(f"AsyncMemory initialized with storage: {storage_type}, LLM: {llm_provider}")
        self.telemetry.capture_event("async_memory.init", {"storage_type": storage_type, "llm_provider": llm_provider})
    
    async def initialize(self):
        """Initialize async components."""
        await self.storage.initialize_async()
    
    async def add(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a new memory asynchronously."""
        try:
            # Generate embedding asynchronously
            embedding = await self.embedding.embed_async(content)
            
            # Process with intelligence manager
            processed_content = await self.intelligence.process_content_async(content, metadata)

            # Intelligent plugin annotations
            extra_fields = {}
            if self._intelligence_plugin and self._intelligence_plugin.enabled:
                extra_fields = self._intelligence_plugin.on_add(content=content, metadata=metadata)
            
            # Agent plugin can massage routing identifiers/filters
            if self._agent_plugin and self._agent_plugin.enabled:
                user_id, agent_id, run_id, metadata, filters = self._agent_plugin.before_add(
                    user_id=user_id, agent_id=agent_id, run_id=run_id, metadata=metadata, filters=filters
                )

            # Generate content hash for deduplication
            content_hash = hashlib.md5(processed_content.encode('utf-8')).hexdigest()

            # Extract category from metadata if present
            category = ""
            if metadata and isinstance(metadata, dict):
                category = metadata.get("category", "")
                # Remove category from metadata to avoid duplication
                metadata = {k: v for k, v in metadata.items() if k != "category"}

            # Store in database asynchronously
            memory_data = {
                "content": processed_content,
                "embedding": embedding,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "hash": content_hash,
                "category": category,
                "metadata": metadata or {},
                "filters": filters or {},
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }

            if extra_fields:
                memory_data.update(extra_fields)
            
            memory_id = await self.storage.add_memory_async(memory_data)
            
            # Log audit event
            await self.audit.log_event_async("memory.add", {
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
    
    async def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for memories asynchronously."""
        try:
            # Generate query embedding asynchronously
            query_embedding = await self.embedding.embed_async(query)
            
            # Agent plugin can adjust query scoping
            if self._agent_plugin and self._agent_plugin.enabled:
                user_id, agent_id, run_id, filters = self._agent_plugin.before_search(
                    user_id=user_id, agent_id=agent_id, run_id=run_id, filters=filters
                )

            # Search in storage asynchronously
            results = await self.storage.search_memories_async(
                query_embedding=query_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=limit
            )
            
            # Process results with intelligence manager
            processed_results = await self.intelligence.process_search_results_async(results, query)

            # Intelligent plugin lifecycle management on search
            if self._intelligence_plugin and self._intelligence_plugin.enabled:
                updates, deletes = self._intelligence_plugin.on_search(processed_results)
                for mem_id, upd in updates:
                    try:
                        await self.storage.update_memory_async(mem_id, {**upd}, user_id, agent_id)
                    except Exception:
                        continue
                for mem_id in deletes:
                    try:
                        await self.storage.delete_memory_async(mem_id, user_id, agent_id)
                    except Exception:
                        continue
            
            # Log audit event
            await self.audit.log_event_async("memory.search", {
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
    
    async def get(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID asynchronously."""
        try:
            if self._agent_plugin and self._agent_plugin.enabled:
                user_id, agent_id = self._agent_plugin.before_get(user_id=user_id, agent_id=agent_id)
            result = await self.storage.get_memory_async(memory_id, user_id, agent_id)
            
            if result:
                if self._intelligence_plugin and self._intelligence_plugin.enabled:
                    updates, delete_flag = self._intelligence_plugin.on_get(result)
                    try:
                        if delete_flag:
                            await self.storage.delete_memory_async(memory_id, user_id, agent_id)
                            return None
                        if updates:
                            await self.storage.update_memory_async(memory_id, {**updates}, user_id, agent_id)
                    except Exception:
                        pass
                await self.audit.log_event_async("memory.get", {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "agent_id": agent_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            raise
    
    async def update(
        self,
        memory_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory asynchronously."""
        try:
            # Generate new embedding asynchronously
            embedding = await self.embedding.embed_async(content)
            
            # Process with intelligence manager
            processed_content = await self.intelligence.process_content_async(content, metadata)
            
            if self._agent_plugin and self._agent_plugin.enabled:
                user_id, agent_id, metadata = self._agent_plugin.before_update(
                    user_id=user_id, agent_id=agent_id, metadata=metadata
                )

            # Update in storage asynchronously
            update_data = {
                "content": processed_content,
                "embedding": embedding,
                "metadata": metadata,
                "updated_at": datetime.utcnow(),
            }
            
            result = await self.storage.update_memory_async(memory_id, update_data, user_id, agent_id)
            
            # Log audit event
            await self.audit.log_event_async("memory.update", {
                "memory_id": memory_id,
                "user_id": user_id,
                "agent_id": agent_id
            })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise
    
    async def delete(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory asynchronously."""
        try:
            if self._agent_plugin and self._agent_plugin.enabled:
                user_id, agent_id = self._agent_plugin.before_delete(user_id=user_id, agent_id=agent_id)
            result = await self.storage.delete_memory_async(memory_id, user_id, agent_id)
            
            if result:
                await self.audit.log_event_async("memory.delete", {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "agent_id": agent_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
    
    async def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering asynchronously."""
        try:
            results = await self.storage.get_all_memories_async(user_id, agent_id, limit, offset)
            
            await self.audit.log_event_async("memory.get_all", {
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
    
    async def clear(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent asynchronously."""
        try:
            result = await self.storage.clear_memories_async(user_id, agent_id)
            
            if result:
                await self.audit.log_event_async("memory.clear", {
                    "user_id": user_id,
                    "agent_id": agent_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to clear memories: {e}")
            raise

    # No internal helpers are needed in core now; logic resides in plugin
