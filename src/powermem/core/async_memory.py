"""
Asynchronous memory management implementation

This module provides the asynchronous memory management interface.
"""

import asyncio
import logging
import hashlib
import json
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from copy import deepcopy

from .base import MemoryBase
from ..storage.factory import VectorStoreFactory, GraphStoreFactory
from ..storage.adapter import StorageAdapter
from ..intelligence.manager import IntelligenceManager
from ..integrations.llm.factory import LLMFactory
from ..integrations.embeddings.factory import EmbedderFactory
from .telemetry import TelemetryManager
from .audit import AuditLogger
from ..intelligence.plugin import IntelligentMemoryPlugin, EbbinghausIntelligencePlugin
from ..utils.utils import remove_code_blocks, parse_vision_messages
from ..prompts.intelligent_memory_prompts import (
    FACT_RETRIEVAL_PROMPT,
    FACT_EXTRACTION_PROMPT,
    get_memory_update_prompt,
    parse_messages_for_facts
)

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
        
        # Extract graph_store config (simplified version for dict config)
        graph_store_cfg = self.config.get('graph_store', {})
        self.enable_graph = graph_store_cfg.get('enabled', False) if isinstance(graph_store_cfg, dict) else False
        self.graph_store = None
        if self.enable_graph:
            graph_store_config = graph_store_cfg.get('config', {}) if isinstance(graph_store_cfg, dict) else {}
            self.graph_store = GraphStoreFactory.create(storage_type, graph_store_config)
        
        # Use StorageAdapter like Memory class
        self.storage = StorageAdapter(vector_store, self.embedding)
        
        self.intelligence = IntelligenceManager(self.config)
        self.telemetry = TelemetryManager(self.config)
        self.audit = AuditLogger(self.config)

        # Save custom prompts from config (mem0 compatible)
        self.custom_fact_extraction_prompt = self.config.get('custom_fact_extraction_prompt')
        self.custom_update_memory_prompt = self.config.get('custom_update_memory_prompt')

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
    
    async def _extract_facts(self, messages: Any) -> List[str]:
        """
        Extract facts from messages using LLM asynchronously.
        
        Args:
            messages: Messages (list of dicts, single dict, or str)
            
        Returns:
            List of extracted facts
        """
        try:
            # Parse messages into conversation format
            conversation = parse_messages_for_facts(messages)
            
            # Use custom prompt if provided, otherwise use default (mem0 compatible)
            if self.custom_fact_extraction_prompt:
                system_prompt = self.custom_fact_extraction_prompt
                user_prompt = f"Input:\n{conversation}"
            else:
                system_prompt = FACT_RETRIEVAL_PROMPT
                user_prompt = f"Input:\n{conversation}"
            
            # Call LLM to extract facts asynchronously
            try:
                response = await asyncio.to_thread(
                    self.llm.generate_response,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"}
                )
            except Exception as e:
                logger.error(f"Error in fact extraction: {e}")
                response = ""
            
            # Parse response
            try:
                # Remove code blocks if present (LLM sometimes wraps JSON in code blocks)
                response = remove_code_blocks(response)
                facts_data = json.loads(response)
                facts = facts_data.get("facts", [])
                logger.debug(f"Extracted {len(facts)} facts: {facts}")
                return facts
            except Exception as e:
                logger.error(f"Error in new_retrieved_facts: {e}")
                return []
                
        except Exception as e:
            logger.error(f"Error extracting facts: {e}")
            return []
    
    async def _decide_memory_actions(
        self, 
        new_facts: List[str], 
        existing_memories: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Use LLM to decide memory actions (ADD/UPDATE/DELETE/NONE) asynchronously.
        
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
            
            # Generate update prompt with custom prompt if provided
            custom_prompt = None
            if hasattr(self, 'custom_update_memory_prompt') and self.custom_update_memory_prompt:
                custom_prompt = self.custom_update_memory_prompt
            update_prompt = get_memory_update_prompt(old_memory, new_facts, custom_prompt)
            
            # Call LLM asynchronously
            try:
                response = await asyncio.to_thread(
                    self.llm.generate_response,
                    messages=[{"role": "user", "content": update_prompt}],
                    response_format={"type": "json_object"}
                )
            except Exception as e:
                logger.error(f"Error in new memory actions response: {e}")
                response = ""
            
            # Parse response
            try:
                response = remove_code_blocks(response)
                actions_data = json.loads(response)
                actions = actions_data.get("memory", [])
                return actions
            except Exception as e:
                logger.error(f"Invalid JSON response: {e}")
                return []
                
        except Exception as e:
            logger.error(f"Error deciding memory actions: {e}")
            return []
    
    async def add(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        infer: bool = True,
    ) -> Dict[str, Any]:
        """Add a new memory asynchronously with optional intelligent processing."""
        try:
            # Handle messages parameter (mem0 compatibility)
            if messages is None:
                raise ValueError("messages must be provided (str, dict, or list[dict])")
            
            # Normalize input format (mem0-compatible)
            if isinstance(messages, str):
                messages = [{"role": "user", "content": messages}]
            elif isinstance(messages, dict):
                messages = [messages]
            elif not isinstance(messages, list):
                raise ValueError("messages must be str, dict, or list[dict]")
            
            # Vision-aware message processing (mem0-compatible behavior)
            llm_cfg = {}
            try:
                llm_cfg = (self.config or {}).get("llm", {}).get("config", {})
            except Exception:
                llm_cfg = {}
            if llm_cfg.get("enable_vision"):
                messages = parse_vision_messages(messages, self.llm, llm_cfg.get("vision_details"))
            else:
                messages = parse_vision_messages(messages)
            
            # Check if intelligent memory should be used
            use_infer = infer and isinstance(messages, list) and len(messages) > 0
            
            # If not using intelligent memory, fall back to simple mode
            if not use_infer:
                return await self._simple_add_async(messages, user_id, agent_id, run_id, metadata, filters)
            
            # Intelligent memory mode: extract facts, search similar memories, and consolidate
            return await self._intelligent_add_async(messages, user_id, agent_id, run_id, metadata, filters)
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            self.telemetry.capture_event("memory.add.error", {"error": str(e)})
            raise
    
    async def _simple_add_async(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
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
        
        # Generate embedding asynchronously
        embedding = await asyncio.to_thread(self.embedding.embed, content, memory_action="add")
        
        # Disabled LLM-based importance evaluation to save tokens
        # Process with intelligence manager
        # enhanced_metadata = await self.intelligence.process_metadata_async(content, metadata)
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
            enhanced_metadata = {k: v for k, v in enhanced_metadata.items() if k != "category"}

        # Final validation before storage
        if not content or not content.strip():
            raise ValueError(f"Refusing to store empty content. Original messages: {messages}")
        
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
        }, user_id=user_id, agent_id=agent_id)
        
        # Capture telemetry
        self.telemetry.capture_event("memory.add", {
            "memory_id": memory_id,
            "user_id": user_id,
            "agent_id": agent_id
        })
        
        # Add to graph store and get relations (only if graph store is enabled)
        graph_result = None
        if self.enable_graph:
            graph_result = await self._add_to_graph_async(messages, filters, user_id, agent_id, run_id)
        
        result = {
            "results": [{
                "id": memory_id,
                "memory": content,
                "event": "ADD",
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": enhanced_metadata,
                "created_at": memory_data["created_at"],
            }]
        }
        if graph_result:
            result["relations"] = graph_result
        return result
    
    async def _intelligent_add_async(
        self,
        messages,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Intelligent add mode: extract facts, consolidate with existing memories."""
        # Step 1: Extract facts from messages
        logger.info("Extracting facts from messages...")
        facts = await self._extract_facts(messages)
        
        if not facts:
            logger.debug("No facts extracted, falling back to simple mode")
            return await self._simple_add_async(messages, user_id, agent_id, run_id, metadata, filters)
        
        logger.info(f"Extracted {len(facts)} facts: {facts}")
        
        # Step 2: Search for similar memories for each fact
        existing_memories = []
        fact_embeddings = {}
        
        for fact in facts:
            fact_embedding = await asyncio.to_thread(self.embedding.embed, fact, memory_action="add")
            fact_embeddings[fact] = fact_embedding
            
            # Search for similar memories with reduced limit to reduce noise
            # Pass fact text to enable hybrid search for better results
            similar = await self.storage.search_memories_async(
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
        
        # Mapping UUIDs with integers for handling UUID hallucinations (mem0 compatibility)
        temp_uuid_mapping = {}
        for idx, item in enumerate(existing_memories):
            temp_uuid_mapping[str(idx)] = item["id"]
            existing_memories[idx]["id"] = str(idx)
        
        # Step 3: Let LLM decide memory actions (only if we have new facts)
        actions = []
        if facts:
            actions = await self._decide_memory_actions(facts, existing_memories, user_id, agent_id)
            logger.info(f"LLM decided on {len(actions)} memory actions")
        else:
            logger.debug("No new facts, skipping LLM decision step")
        
        # Step 4: Execute actions
        results = []
        action_counts = {"ADD": 0, "UPDATE": 0, "DELETE": 0, "NONE": 0}
        
        if not actions:
            logger.debug("No actions to execute, returning empty results")
            return {"results": []}
        
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
                    memory_id = await self._create_memory_async(
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
                    # Use UUID mapping to get the real memory ID
                    real_memory_id = temp_uuid_mapping.get(str(action_id))
                    if real_memory_id:
                        await self._update_memory_async(
                            memory_id=real_memory_id,
                            content=action_text,
                            user_id=user_id,
                            agent_id=agent_id,
                            existing_embeddings=fact_embeddings
                        )
                        results.append({
                            "id": real_memory_id,
                            "memory": action_text,
                            "event": event_type,
                            "previous_memory": action.get("old_memory")
                        })
                        action_counts["UPDATE"] += 1
                    else:
                        logger.warning(f"Could not find real memory ID for action ID: {action_id}")
                        
                elif event_type == "DELETE":
                    # Use UUID mapping to get the real memory ID
                    real_memory_id = temp_uuid_mapping.get(str(action_id))
                    if real_memory_id:
                        await self.delete_async(real_memory_id, user_id, agent_id)
                        results.append({
                            "id": real_memory_id,
                            "memory": action_text,
                            "event": event_type
                        })
                        action_counts["DELETE"] += 1
                    else:
                        logger.warning(f"Could not find real memory ID for action ID: {action_id}")
                        
                elif event_type == "NONE":
                    logger.debug("No action needed for memory")
                    action_counts["NONE"] += 1
                    
            except Exception as e:
                logger.error(f"Error executing memory action {event_type}: {e}")
        
        # Log audit event for intelligent add operation
        await self.audit.log_event_async("memory.intelligent_add", {
            "user_id": user_id,
            "agent_id": agent_id,
            "facts_count": len(facts),
            "action_counts": action_counts,
            "results_count": len(results)
        }, user_id=user_id, agent_id=agent_id)
        
        # Add to graph store and get relations (only if graph store is enabled)
        graph_result = None
        if self.enable_graph:
            graph_result = await self._add_to_graph_async(messages, filters, user_id, agent_id, run_id)
        
        # API format: {"results": [...]}
        if results:
            result = {"results": results}
            if graph_result:
                result["relations"] = graph_result
            return result
        else:
            # Fallback to simple mode
            return await self._simple_add_async(messages, user_id, agent_id, run_id, metadata, filters)
    
    async def _add_to_graph_async(
        self,
        messages,
        filters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Add messages to graph store and return relations asynchronously.
        Matches mem0's _add_to_graph behavior.
        
        Returns:
            dict with added_entities and deleted_entities, or None if graph store is disabled
        """
        if not self.enable_graph:
            return None
        
        # Extract content from messages for graph processing (matching mem0)
        if isinstance(messages, str):
            data = messages
        elif isinstance(messages, dict):
            data = messages.get("content", "")
        elif isinstance(messages, list):
            data = "\n".join([
                msg.get("content", "") 
                for msg in messages 
                if isinstance(msg, dict) and msg.get("content") and msg.get("role") != "system"
            ])
        else:
            data = ""
        
        if not data:
            return None
        
        graph_filters = {**(filters or {}), "user_id": user_id, "agent_id": agent_id, "run_id": run_id}
        if graph_filters.get("user_id") is None:
            graph_filters["user_id"] = "user"
        
        return self.graph_store.add(data, graph_filters)
    
    async def _create_memory_async(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        existing_embeddings: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a memory asynchronously with optional embeddings."""
        # Validate content is not empty
        if not content or not content.strip():
            raise ValueError(f"Cannot create memory with empty content: '{content}'")
        
        # Generate or use existing embedding
        if existing_embeddings and content in existing_embeddings:
            embedding = existing_embeddings[content]
        else:
            embedding = await asyncio.to_thread(self.embedding.embed, content, memory_action="add")
        
        # Disabled LLM-based importance evaluation to save tokens
        # Process metadata
        # enhanced_metadata = await self.intelligence.process_metadata_async(content, metadata)
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
        
        memory_id = await self.storage.add_memory_async(memory_data)
        
        return memory_id
    
    async def _update_memory_async(
        self,
        memory_id: str,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        existing_embeddings: Optional[Dict[str, Any]] = None,
    ):
        """Update a memory asynchronously with optional embeddings."""
        # Validate content is not empty
        if not content or not content.strip():
            raise ValueError(f"Cannot update memory with empty content: '{content}'")
        
        # Generate or use existing embedding
        if existing_embeddings and content in existing_embeddings:
            embedding = existing_embeddings[content]
        else:
            embedding = await asyncio.to_thread(self.embedding.embed, content, memory_action="update")
        
        # Generate content hash
        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        
        update_data = {
            "content": content,
            "embedding": embedding,
            "hash": content_hash,  # Update hash
            "updated_at": datetime.utcnow(),
        }
        
        logger.debug(f"Updating memory {memory_id} with content: '{content[:50]}...'")
        
        await self.storage.update_memory_async(memory_id, update_data, user_id, agent_id)
    
    async def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
        threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Search for memories asynchronously."""
        try:
            # Generate query embedding asynchronously
            query_embedding = await asyncio.to_thread(self.embedding.embed, query, memory_action="search")
            

            # Search in storage asynchronously - pass query text to enable hybrid search
            results = await self.storage.search_memories_async(
                query_embedding=query_embedding,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=limit,
                query=query  # Pass query text for hybrid search (vector + full-text)
            )
            
            # Process results with intelligence manager (only if enabled to avoid unnecessary calls)
            if self.intelligence.enabled:
                processed_results = await self.intelligence.process_search_results_async(results, query)
            else:
                processed_results = results

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
                score = result.get("score", 0.0)
                # Apply threshold filtering (mem0 compatible)
                # Only include results if threshold is None or score >= threshold
                if threshold is not None and score < threshold:
                    continue
                
                transformed_result = {
                    "memory": result.get("memory", ""),  # Already in mem0 format from adapter
                    "metadata": result.get("metadata", {}),  # Keep metadata as-is from storage
                    "score": score,
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
                "results_count": len(transformed_results)
            }, user_id=user_id, agent_id=agent_id)
            
            # Capture telemetry
            self.telemetry.capture_event("memory.search", {
                "user_id": user_id,
                "agent_id": agent_id,
                "results_count": len(transformed_results),
                "threshold": threshold
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
                }, user_id=user_id, agent_id=agent_id)
            
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
            embedding = await asyncio.to_thread(self.embedding.embed, content, memory_action="update")
            
            # Disabled LLM-based importance evaluation to save tokens
            # Process with intelligence manager
            # enhanced_metadata = await self.intelligence.process_metadata_async(content, metadata)
            enhanced_metadata = metadata  # Use original metadata without LLM evaluation
            

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
            }, user_id=user_id, agent_id=agent_id)
            
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
                }, user_id=user_id, agent_id=agent_id)
            
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
            }, user_id=user_id, agent_id=agent_id)
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get all memories: {e}")
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
                }, user_id=user_id, agent_id=agent_id)
                
                self.telemetry.capture_event("memory.delete_all", {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete all memories: {e}")
            raise

    async def reset(self):
        """
        Reset the memory store asynchronously by:
            Deletes the vector store collection
            Resets the database
            Recreates the vector store with a new client
        """
        logger.warning("Resetting all memories")
        
        try:
            # Reset vector store asynchronously
            if hasattr(self.storage.vector_store, "reset"):
                await asyncio.to_thread(self.storage.vector_store.reset)
            else:
                logger.warning("Vector store does not support reset. Skipping.")
                await asyncio.to_thread(self.storage.vector_store.delete_col)
                # Recreate vector store
                from ..storage.factory import VectorStoreFactory
                vector_store_config = self._get_component_config('vector_store')
                self.storage.vector_store = VectorStoreFactory.create(self.storage_type, vector_store_config)
                # Update storage adapter
                self.storage = StorageAdapter(self.storage.vector_store, self.embedding)
            
            # Reset graph store if enabled
            if self.enable_graph and hasattr(self.graph_store, "reset"):
                await asyncio.to_thread(self.graph_store.reset)
            
            # Log telemetry event
            self.telemetry.capture_event("memory.reset", {"sync_type": "async"})
            
            logger.info("Memory store reset completed successfully")
            
        except Exception as e:
            logger.error(f"Failed to reset memory store: {e}")
            raise

    # No internal helpers are needed in core now; logic resides in plugin
