"""
Storage adapter for Memory class

This module provides an adapter that bridges the VectorStoreBase interface
with the interface expected by the Memory class.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

from powermem.storage.base import VectorStoreBase

logger = logging.getLogger(__name__)


class StorageAdapter:
    """Adapter that bridges VectorStoreBase interface with Memory class expectations."""
    
    def __init__(self, vector_store: VectorStoreBase, embedding_service=None):
        """Initialize the adapter with a vector store and embedding service."""
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        # get collection name from vector store attribute collection_name
        self.collection_name = getattr(vector_store, 'collection_name', 'memories')

        # Ensure collection exists (will be created with actual vector size when first vector is added)
        # self.vector_store.create_col(self.collection_name, vector_size=1536, distance="cosine")
    
    def add_memory(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to the store."""
        memory_id = memory_data.get("id", str(uuid.uuid4()))
        
        # Create vector from content using embedding service
        content = memory_data.get("content", "")
        if self.embedding_service:
            try:
                vector = self.embedding_service.embed(content, memory_action="add")
                # Create collection with actual vector size if not exists
                if not hasattr(self, '_collection_created'):
                    self.vector_store.create_col(self.collection_name, vector_size=len(vector), distance="cosine")
                    self._collection_created = True
            except Exception as e:
                logger.warning(f"Failed to generate embedding, using mock vector: {e}")
                vector = [0.1] * 1536  # Use 1536 dimensions for OceanBase compatibility
                if not hasattr(self, '_collection_created'):
                    self.vector_store.create_col(self.collection_name, vector_size=1536, distance="cosine")
                    self._collection_created = True
        else:
            # Use 1536 dimensions for OceanBase compatibility
            vector = [0.1] * 1536
            if not hasattr(self, '_collection_created'):
                self.vector_store.create_col(self.collection_name, vector_size=1536, distance="cosine")
                self._collection_created = True
        
        # Store the memory data as payload - unified format based on OceanBase
        payload = {
            "data": content,  # Unified field name for text content
            "user_id": memory_data.get("user_id", ""),
            "agent_id": memory_data.get("agent_id", ""),
            "run_id": memory_data.get("run_id", ""),
            "actor_id": memory_data.get("actor_id", ""),
            "hash": memory_data.get("hash", ""),
            "created_at": memory_data.get("created_at").isoformat() if memory_data.get("created_at") else "",
            "updated_at": memory_data.get("updated_at").isoformat() if memory_data.get("updated_at") else "",
            "category": memory_data.get("category", ""),
            "fulltext_content": content,  # For full-text search
        }
        
        # Add only user-defined metadata (not system fields)
        user_metadata = memory_data.get("metadata", {})
        payload["metadata"] = user_metadata
        
        # Add any extra fields (excluding system fields and embedding)
        excluded_fields = ["id", "content", "data", "user_id", "agent_id", "run_id", "metadata", "filters", 
                          "created_at", "updated_at", "actor_id", "hash", "category", "embedding"]
        for key, value in memory_data.items():
            if key not in excluded_fields:
                payload[key] = value
        
        self.vector_store.insert([vector], [payload], [memory_id])
        return memory_id
    
    def search_memories(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for memories."""
        # Use the provided query embedding or generate one
        if query_embedding:
            query_vector = query_embedding
        else:
            # If no query embedding provided, we can't search meaningfully
            logger.warning("No query embedding provided for search")
            return []
        
        # Unified search method - try OceanBase format first, fallback to SQLite
        try:
            # Try OceanBase format first
            results = self.vector_store.search("", vectors=[query_vector], limit=limit, filters=filters)
        except TypeError:
            # Fallback to SQLite format
            results = self.vector_store.search(query_vector, vectors=[query_vector], limit=limit)
        
        # Convert results to unified format
        memories = []
        for result in results:
            # Handle different result formats
            if hasattr(result, 'payload') and result.payload:
                # Result with payload attribute
                payload = result.payload
                memory_id = result.id
                score = getattr(result, 'score', 1.0)
            elif hasattr(result, 'payload') and isinstance(result.payload, dict):
                # Result with dict payload
                payload = result.payload
                memory_id = result.id
                score = getattr(result, 'score', 1.0)
            elif isinstance(result, dict):
                # Direct dict result
                payload = result
                memory_id = result.get("id")
                score = result.get("score", 1.0)
            else:
                continue
            
            # Extract unified fields
            # Core and promoted keys that should not be in metadata
            promoted_payload_keys = ["user_id", "agent_id", "run_id", "actor_id", "role"]
            core_and_promoted_keys = {"data", "hash", "created_at", "updated_at", "id", "metadata", *promoted_payload_keys}
            
            # Extract core fields
            content = payload.get("data", "")
            created_at = payload.get("created_at")
            updated_at = payload.get("updated_at")
            
            # Extract promoted fields
            promoted_fields = {}
            for key in promoted_payload_keys:
                if key in payload:
                    promoted_fields[key] = payload[key]
            
            # Extract user metadata from payload
            # If payload contains "metadata" field (nested user metadata), use it directly
            # Otherwise, extract additional metadata from other fields
            if "metadata" in payload:
                user_metadata = payload["metadata"]
            else:
                # Extract additional metadata (all fields not in core_and_promoted_keys)
                user_metadata = {k: v for k, v in payload.items() if k not in core_and_promoted_keys}
            
            memory = {
                "id": memory_id,
                "content": content,
                "created_at": created_at,
                "updated_at": updated_at,
                "score": score,
                **promoted_fields,  # Add promoted fields at top level
                "metadata": user_metadata if user_metadata else {},  # Add user metadata
            }
            
            # Apply filters
            if user_id and memory.get("user_id") != user_id:
                continue
            if agent_id and memory.get("agent_id") != agent_id:
                continue
            if run_id and memory.get("run_id") != run_id:
                continue
            
            memories.append(memory)
        
        return memories[:limit]
    
    def get_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID."""
        result = self.vector_store.get(memory_id)
        
        if result and result.payload:
            memory = {
                "id": result.id,
                "content": result.payload.get("content", ""),
                "user_id": result.payload.get("user_id"),
                "agent_id": result.payload.get("agent_id"),
                "run_id": result.payload.get("run_id"),
                "metadata": result.payload.get("metadata", {}),
                "created_at": result.payload.get("created_at"),
                "updated_at": result.payload.get("updated_at"),
            }
            
            # Check access control
            if user_id and memory.get("user_id") != user_id:
                return None
            if agent_id and memory.get("agent_id") != agent_id:
                return None
            
            return memory
        
        return None
    
    def update_memory(
        self,
        memory_id: str,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a memory."""
        # Get existing record from vector store directly
        existing_result = self.vector_store.get(memory_id)
        if not existing_result or not existing_result.payload:
            logger.warning(f"Memory {memory_id} not found")
            return None
        
        # Get existing payload
        existing_payload = existing_result.payload
        
        # Merge update_data into payload
        updated_payload = existing_payload.copy()
        
        # Handle content field - map to "data" in payload
        if "content" in update_data:
            updated_payload["data"] = update_data["content"]
            updated_payload["fulltext_content"] = update_data["content"]
            # Remove content from update_data to avoid confusion
            update_data = update_data.copy()
            del update_data["content"]
        
        # Update other fields
        updated_payload.update(update_data)
        
        # Update updated_at if not provided
        if "updated_at" not in updated_payload:
            from datetime import datetime
            updated_payload["updated_at"] = datetime.utcnow().isoformat()
        
        # Update in vector store with proper payload
        self.vector_store.update(memory_id, vector=update_data.get("embedding"), payload=updated_payload)
        
        return updated_payload
    
    def delete_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory."""
        # Check if memory exists and user has access
        existing = self.get_memory(memory_id, user_id, agent_id)
        if not existing:
            return False
        
        # Delete from vector store
        self.vector_store.delete(memory_id)
        return True
    
    def get_all_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering."""
        # Build filters for database-level filtering
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id
        
        # Get memories from vector store with filters (if supported)
        if filters and hasattr(self.vector_store, 'list'):
            # Pass filters to vector store's list method for database-level filtering
            # Request more records to support offset
            results = self.vector_store.list(filters=filters, limit=limit + offset)
        else:
            # Fallback: get all and filter in memory
            results = self.vector_store.list(limit=limit + offset)
        
        # OceanBase returns [memories], SQLite/PGVector return memories directly
        if results and isinstance(results[0], list):
            raw_results = results[0]
        else:
            raw_results = results
        
        # Convert to expected format and apply filters
        memories = []
        for result in raw_results:
            # Handle different result formats
            if hasattr(result, 'payload') and result.payload:
                # Result with payload attribute (e.g., from OceanBase OutputData)
                payload = result.payload
                memory_id = result.id
            elif isinstance(result, dict):
                # Direct dict result (e.g., from SQLite)
                payload = result
                memory_id = result.get("id")
            else:
                continue
            
            # Convert datetime objects to ISO format strings
            created_at = payload.get("created_at")
            if created_at is not None:
                from datetime import datetime
                if isinstance(created_at, datetime):
                    created_at = created_at.isoformat()
            
            updated_at = payload.get("updated_at")
            if updated_at is not None:
                from datetime import datetime
                if isinstance(updated_at, datetime):
                    updated_at = updated_at.isoformat()
            
            memory = {
                "id": memory_id,
                "content": payload.get("data", ""),  # Unified field name
                "user_id": payload.get("user_id"),
                "agent_id": payload.get("agent_id"),
                "run_id": payload.get("run_id"),
                "metadata": payload.get("metadata", {}),
                "created_at": created_at,
                "updated_at": updated_at,
            }
            
            # Apply filters (as double-check if database didn't filter)
            # Note: If filters were applied at database level, these will all pass
            if user_id and memory.get("user_id") != user_id:
                continue
            if agent_id and memory.get("agent_id") != agent_id:
                continue
            if run_id and memory.get("run_id") != run_id:
                continue
            
            memories.append(memory)
        
        # Apply offset and limit
        return memories[offset:offset + limit]
    
    def clear_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent or run."""
        # Build filters for database query
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id
        
        # Use batch processing to avoid timeout
        batch_size = 1000
        deleted_count = 0
        
        while True:
            # Get a batch of memories with filtering
            batch = self.get_all_memories(user_id, agent_id, run_id, limit=batch_size, offset=deleted_count)
            
            # If no more records, we're done
            if not batch:
                break
            
            # Delete each memory in the batch
            for memory in batch:
                try:
                    self.vector_store.delete(memory["id"])
                except Exception as e:
                    logger.warning(f"Failed to delete memory {memory.get('id')}: {e}")
            
            deleted_count += len(batch)
            
            # If we got fewer records than batch_size, we've reached the end
            if len(batch) < batch_size:
                break
        
        logger.info(f"Deleted {deleted_count} memories with filters: {filters}")
        return True
    
    async def get_all_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.get_all_memories, user_id, agent_id, run_id, limit, offset)
    
    async def clear_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent or run asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.clear_memories, user_id, agent_id, run_id)
    
    async def initialize_async(self):
        """Initialize storage asynchronously."""
        # No-op for now
        pass
    
    async def add_memory_async(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to the store asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.add_memory, memory_data)
    
    async def search_memories_async(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for memories asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.search_memories, query_embedding, user_id, agent_id, run_id, filters, limit)
    
    async def get_memory_async(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.get_memory, memory_id, user_id, agent_id)
    
    async def delete_memory_async(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.delete_memory, memory_id, user_id, agent_id)
    
    async def update_memory_async(
        self,
        memory_id: str,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a memory asynchronously."""
        import asyncio
        return await asyncio.to_thread(self.update_memory, memory_id, update_data, user_id, agent_id)
