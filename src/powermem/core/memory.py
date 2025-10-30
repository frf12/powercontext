"""
Synchronous memory management implementation

This module provides the synchronous memory management interface.
"""

import logging
import hashlib
import json
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from copy import deepcopy

from .base import MemoryBase
from ..configs import MemoryConfig
from ..storage.factory import VectorStoreFactory, GraphStoreFactory
from ..storage.adapter import StorageAdapter
from ..intelligence.manager import IntelligenceManager
from ..integrations.llm.factory import LLMFactory
from ..integrations.embeddings.factory import EmbedderFactory
from .telemetry import TelemetryManager
from .audit import AuditLogger
from ..intelligence.plugin import IntelligentMemoryPlugin, EbbinghausIntelligencePlugin
from ..utils.utils import remove_code_blocks, convert_config_object_to_dict
from ..prompts.intelligent_memory_prompts import (
    FACT_RETRIEVAL_PROMPT,
    FACT_EXTRACTION_PROMPT,
    get_memory_update_prompt,
    parse_messages_for_facts
)

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

    # First, convert any ConfigObject instances to dicts
    config = convert_config_object_to_dict(config)

    # Check if legacy powermem format (has database or embedding)
    if "database" in config or ("llm" in config and "embedding" in config):
        converted = config.copy()

        # Convert llm
        if "llm" in config:
            converted["llm"] = config["llm"]
        
        # Convert embedding to embedder
        if "embedding" in config:
            converted["embedder"] = config["embedding"]
            converted.pop("embedding", None)

        # Convert database to vector_store
        if "database" in config:
            db_config = config["database"]
            converted["vector_store"] = {
                "provider": db_config.get("provider", "oceanbase"),
                "config": db_config.get("config", {})
            }
            converted.pop("database", None)
        elif "vector_store" not in converted:
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
        config: Optional[Dict[str, Any] | MemoryConfig] = None,
        storage_type: Optional[str] = None,
        llm_provider: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        """
        Initialize the memory manager.

        Compatible with both dict config and MemoryConfig object.
        Supports both mem0 and powermem config formats.

        Args:
            config: Configuration dictionary or MemoryConfig object containing all settings.
                   Dict format supports both mem0 style (llm, embedder, vector_store)
                   and powermem style (database, llm, embedding)
            storage_type: Type of storage backend to use (overrides config)
            llm_provider: LLM provider to use (overrides config)
            embedding_provider: Embedding provider to use (overrides config)
            agent_id: Agent identifier for multi-agent scenarios
        
        Example:
            ```python
            # Method 1: Using MemoryConfig object (recommended)

            config = MemoryConfig(
                vector_store=VectorStoreConfig(provider="oceanbase", config={...}),
                llm=LlmConfig(provider="qwen", config={...}),
                embedder=EmbedderConfig(provider="qwen", config={...})
            )
            memory = Memory(config)

            # Method 2: Using dict (backward compatible - powermem style)
            memory = Memory({
                "database": {"provider": "oceanbase", "config": {...}},
                "llm": {"provider": "qwen", "config": {...}},
            })

            # Method 3: Using dict (mem0 style - auto-converted)
            memory = Memory({
                "llm": {"provider": "openai", "config": {...}},
                "embedder": {"provider": "openai", "config": {...}},
                "vector_store": {"provider": "chroma", "config": {...}},
            })
            ```
        """
        # Handle MemoryConfig object or dict
        if isinstance(config, MemoryConfig):
            # Use MemoryConfig object directly
            self.memory_config = config
            # For backward compatibility, also store as dict
            self.config = config.model_dump()
        else:
            # Convert dict config
            dict_config = config or {}
            dict_config = _auto_convert_config(dict_config)
            self.config = dict_config
            # Try to create MemoryConfig from dict, fallback to dict if fails
            try:
                self.memory_config = MemoryConfig(**dict_config)
            except Exception as e:
                logger.warning(f"Could not parse config as MemoryConfig: {e}, using dict mode")
                self.memory_config = None

        self.agent_id = agent_id
        
        # Extract providers from config with fallbacks
        self.storage_type = storage_type or self._get_provider('vector_store', 'oceanbase')
        self.llm_provider = llm_provider or self._get_provider('llm', 'mock')
        self.embedding_provider = embedding_provider or self._get_provider('embedder', 'mock')

        # Initialize components
        vector_store_config = self._get_component_config('vector_store')
        vector_store = VectorStoreFactory.create(self.storage_type, vector_store_config)

        # Extract graph_store config
        self.enable_graph = self._get_graph_enabled()
        self.graph_store = None
        if self.enable_graph:
            graph_store_config = self._get_component_config('graph_store')
            self.graph_store = GraphStoreFactory.create(self.storage_type, graph_store_config)

        # Extract LLM config
        llm_config = self._get_component_config('llm')
        self.llm = LLMFactory.create(self.llm_provider, llm_config)

        # Extract embedder config
        embedder_config = self._get_component_config('embedder')
        self.embedding = EmbedderFactory.create(self.embedding_provider, embedder_config, None)
        
        # Initialize storage adapter with embedding service
        self.storage = StorageAdapter(vector_store, self.embedding)
        self.intelligence = IntelligenceManager(self.config)
        self.telemetry = TelemetryManager(self.config)
        self.audit = AuditLogger(self.config)

        # Intelligent memory plugin (pluggable)
        merged_cfg = self._get_intelligent_memory_config()

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

    def _get_provider(self, component: str, default: str) -> str:
        """
        Helper method to get component provider uniformly.

        Args:
            component: Component name ('vector_store', 'llm', 'embedder')
            default: Default provider name

        Returns:
            Provider name string
        """
        if self.memory_config:
            component_obj = getattr(self.memory_config, component, None)
            return component_obj.provider if component_obj else default
        else:
            return self.config.get(component, {}).get('provider', default)

    def _get_component_config(self, component: str) -> Dict[str, Any]:
        """
        Helper method to get component configuration uniformly.

        Args:
            component: Component name ('vector_store', 'llm', 'embedder', 'graph_store')

        Returns:
            Component configuration dictionary
        """
        if self.memory_config:
            component_obj = getattr(self.memory_config, component, None)
            return component_obj.config or {} if component_obj else {}
        else:
            return self.config.get(component, {}).get('config', {})

    def _get_graph_enabled(self) -> bool:
        """
        Helper method to get graph store enabled status.

        Returns:
            Boolean indicating whether graph store is enabled
        """
        if self.memory_config:
            return self.memory_config.graph_store.enabled if self.memory_config.graph_store else False
        else:
            return self.config.get('enabled', False)

    def _get_intelligent_memory_config(self) -> Dict[str, Any]:
        """
        Helper method to get intelligent memory configuration.
        Supports both "intelligence" and "intelligent_memory" config keys for backward compatibility.

        Returns:
            Merged intelligent memory configuration dictionary
        """
        if self.memory_config and self.memory_config.intelligent_memory:
            # Use MemoryConfig's intelligent_memory
            return self.memory_config.intelligent_memory.model_dump()
        else:
            # Fallback to dict access
            intelligence_cfg = (self.config or {}).get("intelligence", {})
            intelligent_memory_cfg = (self.config or {}).get("intelligent_memory", {})
            return {**intelligence_cfg, **intelligent_memory_cfg}

    def _extract_facts(self, messages: Any) -> List[str]:
        """
        Extract facts from messages using LLM.
        Integrates with IntelligenceManager for enhanced processing.
        
        Args:
            messages: Messages (list of dicts, single dict, or str)
            
        Returns:
            List of extracted facts
        """
        try:
            # Parse messages into conversation format
            conversation = parse_messages_for_facts(messages)
            
            # Use FACT_RETRIEVAL_PROMPT (mem0 compatible)
            system_prompt = FACT_RETRIEVAL_PROMPT
            user_prompt = f"Input:\n{conversation}"
            
            # Call LLM to extract facts
            response = self.llm.generate_response(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            # Parse response
            try:
                # Remove code blocks if present (LLM sometimes wraps JSON in code blocks)
                response = remove_code_blocks(response)
                facts_data = json.loads(response)
                facts = facts_data.get("facts", [])
                
                # Log for debugging
                logger.debug(f"Extracted {len(facts)} facts: {facts}")
                
                return facts
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse LLM response as JSON: {response}")
                return []
                
        except Exception as e:
            logger.error(f"Error extracting facts: {e}")
            return []
    
    def _decide_memory_actions(
        self, 
        new_facts: List[str], 
        existing_memories: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Use LLM to decide memory actions (ADD/UPDATE/DELETE/NONE).
        
        Args:
            new_facts: List of newly extracted facts
            existing_memories: List of existing memories with 'id' and 'text'
            user_id: User identifier
            agent_id: Agent identifier
            
        Returns:
            List of memory action dictionaries
        """
        try:
            if not new_facts:
                logger.debug("No new facts to process")
                return []
            
            # Format existing memories for prompt
            old_memory = []
            for mem in existing_memories:
                old_memory.append({
                    "id": mem.get("id", "unknown"),
                    "text": mem.get("content", "")
                })
            
            # Generate update prompt
            update_prompt = get_memory_update_prompt(old_memory, new_facts)
            
            # Call LLM
            response = self.llm.generate_response(
                messages=[{"role": "user", "content": update_prompt}],
                response_format={"type": "json_object"}
            )
            
            # Parse response
            try:
                actions_data = json.loads(response)
                actions = actions_data.get("memory", [])
                return actions
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse memory actions JSON: {e}")
                logger.debug(f"Response was: {response}")
                return []
                
        except Exception as e:
            logger.error(f"Error deciding memory actions: {e}")
            return []
    
    def add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        use_intelligent_memory: bool = True,
    ) -> Dict[str, Any]:
        """Add a new memory with optional intelligent processing."""
        try:
            # Handle messages parameter (mem0 compatibility)
            if messages is None:
                raise ValueError("messages must be provided (str, dict, or list[dict])")
            
            # Check if intelligent memory should be used
            use_intel = use_intelligent_memory and isinstance(messages, list) and len(messages) > 0
            
            # If not using intelligent memory, fall back to simple mode
            if not use_intel:
                return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            
            # Intelligent memory mode: extract facts, search similar memories, and consolidate
            return self._intelligent_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            self.telemetry.capture_event("memory.add.error", {"error": str(e)})
            raise
    
    def _simple_add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Simple add mode: direct storage without intelligence."""
        # Parse messages into content
        if isinstance(messages, str):
            content = messages
        elif isinstance(messages, dict):
            content = messages.get("content", "")
        elif isinstance(messages, list):
            content = "\n".join([msg.get("content", "") for msg in messages if isinstance(msg, dict) and msg.get("content")])
        else:
            raise ValueError("messages must be str, dict, or list[dict]")
        
        # Validate content is not empty
        if not content or not content.strip():
            logger.error(f"Cannot store empty content. Messages: {messages}")
            raise ValueError(f"Cannot create memory with empty content. Original messages: {messages}")
        
        # Generate embedding
        embedding = self.embedding.embed(content)
        
        # Disabled LLM-based importance evaluation to save tokens
        # Process with intelligence manager
        # enhanced_metadata = self.intelligence.process_metadata(content, metadata)
        enhanced_metadata = metadata  # Use original metadata without LLM evaluation

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

        # Final validation before storage
        if not content or not content.strip():
            raise ValueError(f"Refusing to store empty content. Original messages: {messages}")
        
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
        
        # Add to graph store
        if self.enable_graph:
            graph_filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
            self.graph_store.add(content, graph_filters)
        
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
            "created_at": memory_data["created_at"].isoformat() if isinstance(memory_data["created_at"], datetime) else memory_data["created_at"],
        }
    
    def _intelligent_add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Intelligent add mode: extract facts, consolidate with existing memories."""
        # Step 1: Extract facts from messages
        logger.info("Extracting facts from messages...")
        facts = self._extract_facts(messages)
        
        if not facts:
            logger.debug("No facts extracted, falling back to simple mode")
            return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
        
        logger.info(f"Extracted {len(facts)} facts: {facts}")
        
        # Step 2: Search for similar memories for each fact
        existing_memories = []
        fact_embeddings = {}
        
        for fact in facts:
            fact_embedding = self.embedding.embed(fact)
            fact_embeddings[fact] = fact_embedding
            
            # Search for similar memories with reduced limit to reduce noise
            # Pass fact text to enable hybrid search for better results
            similar = self.storage.search_memories(
                query_embedding=fact_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=5,
                query=fact  # Enable hybrid search
            )
            existing_memories.extend(similar)
        
        # Improved deduplication: prefer memories with better similarity scores
        unique_memories = {}
        for mem in existing_memories:
            mem_id = mem.get("id")
            if mem_id and mem_id not in unique_memories:
                unique_memories[mem_id] = mem
            elif mem_id:
                # If duplicate ID, keep the one with better similarity (lower distance)
                existing = unique_memories.get(mem_id)
                mem_distance = mem.get("distance", float('inf'))
                existing_distance = existing.get("distance", float('inf')) if existing else float('inf')
                if mem_distance < existing_distance:
                    unique_memories[mem_id] = mem
        
        # Limit candidates to avoid LLM prompt overload
        existing_memories = list(unique_memories.values())[:10]  # Max 10 memories
        
        logger.info(f"Found {len(existing_memories)} existing memories to consider (after dedup and limiting)")
        
        # Step 3: Let LLM decide memory actions
        actions = self._decide_memory_actions(facts, existing_memories, user_id, agent_id)
        
        logger.info(f"LLM decided on {len(actions)} memory actions")
        
        # Step 4: Execute actions
        results = []
        action_counts = {"ADD": 0, "UPDATE": 0, "DELETE": 0, "NONE": 0}
        
        if not actions:
            logger.warning("No actions returned from LLM, falling back to simple mode")
            return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
        
        for action in actions:
            action_text = action.get("text", "") or action.get("memory", "")
            event_type = action.get("event", "NONE")
            action_id = action.get("id", "")
            
            # Validate action text
            if not action_text:
                logger.warning(f"Skipping action with empty text: {action}")
                continue
            
            logger.debug(f"Processing action: {event_type} - '{action_text[:50]}...' (id: {action_id})")
            
            try:
                if event_type == "ADD":
                    # Add new memory
                    memory_id = self._create_memory(
                        content=action_text,
                        user_id=user_id,
                        agent_id=agent_id,
                        run_id=run_id,
                        metadata=metadata,
                        filters=filters,
                        existing_embeddings=fact_embeddings
                    )
                    results.append({
                        "id": memory_id,
                        "memory": action_text,
                        "event": event_type
                    })
                    action_counts["ADD"] += 1
                    
                elif event_type == "UPDATE":
                    # Find the corresponding existing memory ID
                    existing_mem = next((m for m in existing_memories if str(m.get("id")) == str(action_id)), None)
                    if existing_mem:
                        mem_id = existing_mem["id"]
                        self._update_memory(
                            memory_id=mem_id,
                            content=action_text,
                            user_id=user_id,
                            agent_id=agent_id,
                            existing_embeddings=fact_embeddings
                        )
                        results.append({
                            "id": mem_id,
                            "memory": action_text,
                            "event": event_type,
                            "old_memory": action.get("old_memory")
                        })
                        action_counts["UPDATE"] += 1
                        
                elif event_type == "DELETE":
                    # Find the corresponding existing memory ID
                    existing_mem = next((m for m in existing_memories if str(m.get("id")) == str(action_id)), None)
                    if existing_mem:
                        mem_id = existing_mem["id"]
                        self.delete(mem_id, user_id, agent_id)
                        results.append({
                            "id": mem_id,
                            "memory": action_text,
                            "event": event_type
                        })
                        action_counts["DELETE"] += 1
                        
                elif event_type == "NONE":
                    logger.debug("No action needed for memory")
                    action_counts["NONE"] += 1
                    
            except Exception as e:
                logger.error(f"Error executing memory action {event_type}: {e}")
        
        # Log audit event for intelligent add operation
        self.audit.log_event("memory.intelligent_add", {
            "user_id": user_id,
            "agent_id": agent_id,
            "facts_count": len(facts),
            "action_counts": action_counts,
            "results_count": len(results)
        })
        
        # Log and return
        if results:
            result = results[0]  # Return the first result for compatibility
        else:
            # Fallback to simple mode
            return self._simple_add(messages, user_id, agent_id, run_id, metadata, filters, scope, memory_type, prompt)
        
        return result
    
    def _create_memory(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        existing_embeddings: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a memory with optional embeddings."""
        # Validate content is not empty
        if not content or not content.strip():
            raise ValueError(f"Cannot create memory with empty content: '{content}'")
        
        # Generate or use existing embedding
        if existing_embeddings and content in existing_embeddings:
            embedding = existing_embeddings[content]
        else:
            embedding = self.embedding.embed(content)
        
        # Disabled LLM-based importance evaluation to save tokens
        # Process metadata
        # enhanced_metadata = self.intelligence.process_metadata(content, metadata)
        enhanced_metadata = metadata  # Use original metadata without LLM evaluation
        
        # Generate content hash
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        # Extract category
        category = ""
        if enhanced_metadata and isinstance(enhanced_metadata, dict):
            category = enhanced_metadata.get("category", "")
            enhanced_metadata = {k: v for k, v in enhanced_metadata.items() if k != "category"}
        
        # Create memory data
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
        
        memory_id = self.storage.add_memory(memory_data)
        
        # Add to graph store
        if self.enable_graph:
            graph_filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
            self.graph_store.add(content, graph_filters)
        
        return memory_id
    
    def _update_memory(
        self,
        memory_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        existing_embeddings: Optional[Dict[str, Any]] = None,
    ):
        """Update a memory with optional embeddings."""
        # Validate content is not empty
        if not content or not content.strip():
            raise ValueError(f"Cannot update memory with empty content: '{content}'")
        
        # Generate or use existing embedding
        if existing_embeddings and content in existing_embeddings:
            embedding = existing_embeddings[content]
        else:
            embedding = self.embedding.embed(content)
        
        # Generate content hash
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        update_data = {
            "content": content,
            "embedding": embedding,
            "hash": content_hash,  # Update hash
            "updated_at": datetime.utcnow(),
        }
        
        logger.debug(f"Updating memory {memory_id} with content: '{content[:50]}...'")
        
        self.storage.update_memory(memory_id, update_data, user_id, agent_id)
    
    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search for memories."""
        try:
            # Generate query embedding
            query_embedding = self.embedding.embed(query)
            

            # Search in storage - pass query text to enable hybrid search
            results = self.storage.search_memories(
                query_embedding=query_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=limit,
                query=query  # Pass query text for hybrid search (vector + full-text)
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

            # Search in graph store
            if self.enable_graph:
                filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
                graph_results = self.graph_store.search(query, filters, limit)
                return {"results": transformed_results, "relations": graph_results}

            # Return in benchmark expected format
            return {"results": transformed_results}
            
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
    
    def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Delete all memories for given identifiers."""
        try:
            result = self.storage.clear_memories(user_id, agent_id, run_id)
            
            if result:
                self.audit.log_event("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                })
                
                self.telemetry.capture_event("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                })

            if self.enable_graph:
                filters = {"user_id": user_id, "agent_id": agent_id, "run_id": run_id}
                self.graph_store.delete_all(filters)

            return result
            
        except Exception as e:
            logger.error(f"Failed to delete all memories: {e}")
            raise
    
    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get all memories with optional filtering."""
        try:
            results = self.storage.get_all_memories(user_id, agent_id, run_id, limit, offset)
            
            self.audit.log_event("memory.get_all", {
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "limit": limit,
                "offset": offset,
                "results_count": len(results)
            })

            # get from graph store
            if self.enable_graph:
                filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
                graph_results = self.graph_store.get_all(filters, limit + offset)
                results.extend(graph_results)
                return {"results": results, "relations": graph_results}

            return {"results": results}
            
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

            if self.enable_graph:
                filters = {"user_id": user_id, "agent_id": agent_id}
                self.graph_store.delete_all(filters)

            return result
            
        except Exception as e:
            logger.error(f"Failed to clear memories: {e}")
            raise
    
    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Create Memory instance from configuration (mem0-compatible style).
        
        Compatible with mem0's initialization pattern.
        
        Args:
            config: Configuration dictionary (mem0 or powermem format)
            **kwargs: Additional parameters
        
        Returns:
            Memory instance
            
        Example:
            ```python
            # mem0-style config
            memory = Memory.from_config({
                "llm": {"provider": "openai", "config": {"api_key": "..."}},
                "embedder": {"provider": "openai", "config": {"api_key": "..."}},
                "vector_store": {"provider": "oceanbase", "config": {...}},
            })
            ```
        """
        if config is None:
            # Use auto config from environment
            from ..config_loader import auto_config
            config = auto_config()
        
        # Convert legacy config to mem0 format if needed
        converted_config = _auto_convert_config(config)
        
        return cls(config=converted_config, **kwargs)