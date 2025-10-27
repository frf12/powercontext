"""
Synchronous memory management implementation

This module provides the synchronous memory management interface.
"""

import logging
import hashlib
from typing import Any, Dict, List, Optional
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


def _auto_convert_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert legacy powermem config to mem0 format for compatibility.
    
    Now powermem uses mem0-style field names directly.
    
    Args:
        config: Configuration dictionary (legacy or mem0 format)
        
    Returns:
        mem0-style configuration dictionary
    """
    if not config:
        return config
    
    # Check if legacy powermem format (has database or embedding)
    if "database" in config or ("llm" in config and "embedding" in config):
        converted = {}
        
        # Convert llm
        if "llm" in config:
            converted["llm"] = config["llm"]
        
        # Convert embedding to embedder
        if "embedding" in config:
            converted["embedder"] = config["embedding"]
        
        # Convert database to vector_store
        if "database" in config:
            db_config = config["database"]
            converted["vector_store"] = {
                "provider": db_config.get("provider", "oceanbase"),
                "config": db_config.get("config", {})
            }
        else:
            converted["vector_store"] = {
                "provider": "oceanbase",
                "config": {}
            }
        
        logger.info("Converted legacy powermem config to mem0 format")
        return converted
    
    # Already in mem0 format (has embedder or vector_store)
    return config


class Memory(MemoryBase):
    """
    Synchronous memory management implementation.
    
    This class provides the main interface for synchronous memory operations.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        storage_type: Optional[str] = None,
        llm_provider: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """
        Initialize the memory manager.
        
        Compatible with both mem0 and powermem config formats.
        
        Args:
            config: Configuration dictionary containing all settings.
                   Supports both mem0 style (llm, embedder, vector_store)
                   and powermem style (database, llm, embedding)
            storage_type: Type of storage backend to use (overrides config)
            llm_provider: LLM provider to use (overrides config)
            embedding_provider: Embedding provider to use (overrides config)
            agent_id: Agent identifier for multi-agent scenarios
        
        Example:
            ```python
            # powermem style
            from powermem import Memory
            memory = Memory({
                "database": {"provider": "oceanbase", "config": {...}},
                "llm": {"provider": "qwen", "config": {...}},
            })
            
            # mem0 style (auto-converted)
            memory = Memory({
                "llm": {"provider": "openai", "config": {...}},
                "embedder": {"provider": "openai", "config": {...}},
                "vector_store": {"provider": "chroma", "config": {...}},
            })
            ```
        """
        self.config = config or {}
        
        # Auto-detect and convert mem0-style config
        self.config = _auto_convert_config(self.config)
        self.agent_id = agent_id
        
        # Extract providers from config with fallbacks
        # Use mem0-style field names: 'vector_store' instead of 'database', 'embedder' instead of 'embedding'
        self.storage_type = storage_type or self.config.get('vector_store', {}).get('provider', 'oceanbase')
        self.llm_provider = llm_provider or self.config.get('llm', {}).get('provider', 'mock')
        self.embedding_provider = embedding_provider or self.config.get('embedder', {}).get('provider', 'mock')
        
        # Initialize components
        # Extract vector_store config (mem0 naming)
        vector_store_config = self.config.get('vector_store', {}).get('config', {}) if isinstance(self.config, dict) else {}
        vector_store = VectorStoreFactory.create(self.storage_type, vector_store_config)
        
        # Extract LLM config
        llm_config = self.config.get('llm', {}).get('config', {}) if isinstance(self.config, dict) else {}
        self.llm = LLMFactory.create(self.llm_provider, llm_config)
        
        # Extract embedder config (mem0 naming)
        embedder_config = self.config.get('embedder', {}).get('config', {}) if isinstance(self.config, dict) else {}
        self.embedding = EmbedderFactory.create(self.embedding_provider, embedder_config, None)
        
        # Initialize storage adapter with embedding service
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
                logger.warning(f"Failed to initialize intelligence plugin: {e}")
                self._intelligence_plugin = None

        
        logger.info(f"Memory initialized with storage: {self.storage_type}, LLM: {self.llm_provider}, agent: {self.agent_id or 'default'}")
        self.telemetry.capture_event("memory.init", {"storage_type": self.storage_type, "llm_provider": self.llm_provider, "agent_id": self.agent_id})
    
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
            enhanced_metadata = self.intelligence.process_metadata(content, metadata)

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

            # Store in database
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
                "content": content,
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

            # Intelligent plugin lifecycle management on search
            if self._intelligence_plugin and self._intelligence_plugin.enabled:
                updates, deletes = self._intelligence_plugin.on_search(processed_results)
                for mem_id, upd in updates:
                    try:
                        self.storage.update_memory(mem_id, {**upd}, user_id, agent_id)
                    except Exception:
                        continue
                for mem_id in deletes:
                    try:
                        self.storage.delete_memory(mem_id, user_id, agent_id)
                    except Exception:
                        continue
            
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
                # Intelligent plugin lifecycle on get
                if self._intelligence_plugin and self._intelligence_plugin.enabled:
                    updates, delete_flag = self._intelligence_plugin.on_get(result)
                    try:
                        if delete_flag:
                            self.storage.delete_memory(memory_id, user_id, agent_id)
                            return None
                        if updates:
                            self.storage.update_memory(memory_id, {**updates}, user_id, agent_id)
                    except Exception:
                        pass
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