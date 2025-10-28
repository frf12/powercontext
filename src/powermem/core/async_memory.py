"""
Asynchronous memory management implementation

This module provides the asynchronous memory management interface.
"""

import asyncio
import logging
import hashlib
from typing import Any, Dict, List, Optional, Union
from datetime import datetime

from .base import MemoryBase
from ..storage.factory import VectorStoreFactory, GraphStoreFactory
from ..storage.adapter import StorageAdapter
from ..intelligence.manager import IntelligenceManager
from ..integrations.llm.factory import LLMFactory
from ..integrations.embeddings.factory import EmbedderFactory
from .telemetry import TelemetryManager
from .audit import AuditLogger
from ..intelligence.plugin import IntelligentMemoryPlugin, EbbinghausIntelligencePlugin

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
        vector_store = VectorStoreFactory.create(storage_type, self.config)
        self.llm = LLMFactory.create(llm_provider, self.config)
        self.embedding = EmbedderFactory.create(embedding_provider, self.config)
        
        # Use StorageAdapter like Memory class
        self.storage = StorageAdapter(vector_store, self.embedding)
        
        self.intelligence = IntelligenceManager(self.config)
        self.telemetry = TelemetryManager(self.config)
        self.audit = AuditLogger(self.config)

        # Intelligent memory plugin (pluggable)
        # Support both "intelligence" and "intelligent_memory" config keys for backward compatibility
        intelligence_cfg = (self.config or {}).get("intelligence", {})
        intelligent_memory_cfg = (self.config or {}).get("intelligent_memory", {})
        
        # Merge configurations, with intelligent_memory taking precedence
        merged_cfg = {**intelligence_cfg, **intelligent_memory_cfg}
        
        plugin_type = merged_cfg.get("plugin", "ebbinghaus")
        self._intelligence_plugin: Optional[IntelligentMemoryPlugin] = None
        if merged_cfg.get("enabled", False):
            try:
                if plugin_type == "ebbinghaus":
                    self._intelligence_plugin = EbbinghausIntelligencePlugin(merged_cfg)
                else:
                    logger.warning(f"Unknown intelligence plugin: {plugin_type}")
            except Exception as e:
                logger.warning(f"Failed to initialize intelligence plugin (async): {e}")
                self._intelligence_plugin = None

        
        logger.info(f"AsyncMemory initialized with storage: {storage_type}, LLM: {llm_provider}")
        self.telemetry.capture_event("async_memory.init", {"storage_type": storage_type, "llm_provider": llm_provider})
    
    async def initialize(self):
        """Initialize async components."""
        await self.storage.initialize_async()
    
    async def add(
        self,
        messages=None,
        content: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a new memory asynchronously."""
        try:
            # Handle messages parameter (mem0 compatibility)
            if messages is not None:
                if isinstance(messages, str):
                    # Convert string to message format
                    content = messages
                elif isinstance(messages, dict):
                    # Single message dict
                    content = messages.get("content", "")
                elif isinstance(messages, list):
                    # List of messages - extract content
                    content = "\n".join([msg.get("content", "") for msg in messages if isinstance(msg, dict) and msg.get("content")])
                else:
                    raise ValueError("messages must be str, dict, or list[dict]")
            elif content is None:
                raise ValueError("Either 'content' or 'messages' must be provided")
            
            # Generate embedding asynchronously
            embedding = await self.embedding.embed_async(content)
            
            # Process with intelligence manager
            enhanced_metadata = await self.intelligence.process_metadata_async(content, metadata)

            # Intelligent plugin annotations
            extra_fields = {}
            if self._intelligence_plugin and self._intelligence_plugin.enabled:
                extra_fields = self._intelligence_plugin.on_add(content=content, metadata=enhanced_metadata)
            

            # Generate content hash for deduplication
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

            # Extract category from enhanced metadata if present
            category = ""
            if enhanced_metadata and isinstance(enhanced_metadata, dict):
                category = enhanced_metadata.get("category", "")
                # Remove category from metadata to avoid duplication
                enhanced_metadata = {k: v for k, v in enhanced_metadata.items() if k != "category"}

            # Store in database asynchronously
            memory_data = {
                "content": content,
                "embedding": embedding,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "hash": content_hash,
                "category": category,
                "metadata": enhanced_metadata or {},
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
                "content": content,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": enhanced_metadata,
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
    ) -> Dict[str, Any]:
        """Search for memories asynchronously."""
        try:
            # Generate query embedding asynchronously
            query_embedding = await self.embedding.embed_async(query)
            

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
            
            # Transform results to match benchmark expected format
            # Benchmark expects: {"results": [{"memory": ..., "metadata": {...}, "score": ...}], "relations": [...]}
            # Map "content" to "memory" field to match mem0 format
            transformed_results = []
            for result in processed_results:
                transformed_result = {
                    "memory": result.get("content", ""),  # Map "content" to "memory"
                    "metadata": result.get("metadata", {}),  # Keep metadata as-is from storage
                    "score": result.get("score", 0.0),
                }
                # Preserve other fields if needed
                for key in ["id", "created_at", "updated_at", "user_id", "agent_id", "run_id"]:
                    if key in result:
                        transformed_result[key] = result[key]
                transformed_results.append(transformed_result)
            
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
            
            # Return in benchmark expected format
            return {"results": transformed_results}
            
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
            enhanced_metadata = await self.intelligence.process_metadata_async(content, metadata)
            

            # Update in storage asynchronously
            update_data = {
                "content": content,
                "embedding": embedding,
                "metadata": enhanced_metadata,
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
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering asynchronously."""
        try:
            results = await self.storage.get_all_memories_async(user_id, agent_id, run_id, limit, offset)
            
            await self.audit.log_event_async("memory.get_all", {
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
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
    
    async def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Delete all memories for given identifiers asynchronously."""
        try:
            result = await self.storage.clear_memories_async(user_id, agent_id, run_id)
            
            if result:
                await self.audit.log_event_async("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                })
                
                self.telemetry.capture_event("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete all memories: {e}")
            raise

    # No internal helpers are needed in core now; logic resides in plugin
