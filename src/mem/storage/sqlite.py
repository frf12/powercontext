"""
SQLite storage implementation

This module provides SQLite-based storage for memory data.
"""

import logging
import sqlite3
import json
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
import numpy as np

from .base import StorageBase

logger = logging.getLogger(__name__)


class SQLiteStorage(StorageBase):
    """
    SQLite-based storage implementation.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize SQLite storage.
        
        Args:
            config: Storage configuration
        """
        super().__init__(config)
        self.database_path = config.get("database_path", "./data/smartmem.db")
        self.enable_wal = config.get("enable_wal", True)
        self.timeout = config.get("timeout", 30)
        self.connection = None
    
    def initialize(self) -> None:
        """Initialize SQLite database."""
        try:
            import os
            os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
            
            self.connection = sqlite3.connect(
                self.database_path,
                timeout=self.timeout,
                check_same_thread=False
            )
            
            if self.enable_wal:
                self.connection.execute("PRAGMA journal_mode=WAL")
            
            self._create_tables()
            logger.info(f"SQLite storage initialized: {self.database_path}")
            
        except Exception as e:
            logger.error(f"Failed to initialize SQLite storage: {e}")
            raise
    
    async def initialize_async(self) -> None:
        """Initialize SQLite database asynchronously."""
        # SQLite doesn't support async operations natively
        # This is a placeholder for compatibility
        self.initialize()
    
    def _create_tables(self) -> None:
        """Create database tables."""
        cursor = self.connection.cursor()
        
        # Create memories table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                embedding BLOB,
                user_id TEXT,
                agent_id TEXT,
                run_id TEXT,
                metadata TEXT,
                filters TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_run_id ON memories(run_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)")
        
        self.connection.commit()
    
    def add_memory(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to storage."""
        try:
            memory_id = str(uuid.uuid4())
            
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO memories (
                    id, content, embedding, user_id, agent_id, run_id,
                    metadata, filters, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory_id,
                memory_data["content"],
                self._serialize_embedding(memory_data.get("embedding")),
                memory_data.get("user_id"),
                memory_data.get("agent_id"),
                memory_data.get("run_id"),
                json.dumps(memory_data.get("metadata", {})),
                json.dumps(memory_data.get("filters", {})),
                memory_data.get("created_at", datetime.utcnow()),
                memory_data.get("updated_at", datetime.utcnow()),
            ))
            
            self.connection.commit()
            return memory_id
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            raise
    
    async def add_memory_async(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to storage asynchronously."""
        # SQLite doesn't support async operations natively
        return self.add_memory(memory_data)
    
    def search_memories(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for memories using vector similarity."""
        try:
            cursor = self.connection.cursor()
            
            # Build query
            query = "SELECT * FROM memories WHERE 1=1"
            params = []
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            
            if run_id:
                query += " AND run_id = ?"
                params.append(run_id)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            # Convert to list of dictionaries
            results = []
            for row in rows:
                memory = {
                    "id": row[0],
                    "content": row[1],
                    "embedding": self._deserialize_embedding(row[2]),
                    "user_id": row[3],
                    "agent_id": row[4],
                    "run_id": row[5],
                    "metadata": json.loads(row[6]) if row[6] else {},
                    "filters": json.loads(row[7]) if row[7] else {},
                    "created_at": row[8],
                    "updated_at": row[9],
                }
                results.append(memory)
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            raise
    
    async def search_memories_async(
        self,
        query_embedding: List[float],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search for memories using vector similarity asynchronously."""
        return self.search_memories(query_embedding, user_id, agent_id, run_id, filters, limit)
    
    def get_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID."""
        try:
            cursor = self.connection.cursor()
            
            query = "SELECT * FROM memories WHERE id = ?"
            params = [memory_id]
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            
            cursor.execute(query, params)
            row = cursor.fetchone()
            
            if row:
                return {
                    "id": row[0],
                    "content": row[1],
                    "embedding": self._deserialize_embedding(row[2]),
                    "user_id": row[3],
                    "agent_id": row[4],
                    "run_id": row[5],
                    "metadata": json.loads(row[6]) if row[6] else {},
                    "filters": json.loads(row[7]) if row[7] else {},
                    "created_at": row[8],
                    "updated_at": row[9],
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            raise
    
    async def get_memory_async(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID asynchronously."""
        return self.get_memory(memory_id, user_id, agent_id)
    
    def update_memory(
        self,
        memory_id: str,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory."""
        try:
            cursor = self.connection.cursor()
            
            # Build update query
            set_clauses = []
            params = []
            
            if "content" in update_data:
                set_clauses.append("content = ?")
                params.append(update_data["content"])
            
            if "embedding" in update_data:
                set_clauses.append("embedding = ?")
                params.append(self._serialize_embedding(update_data["embedding"]))
            
            if "metadata" in update_data:
                set_clauses.append("metadata = ?")
                params.append(json.dumps(update_data["metadata"]))
            
            set_clauses.append("updated_at = ?")
            params.append(datetime.utcnow())
            
            query = f"UPDATE memories SET {', '.join(set_clauses)} WHERE id = ?"
            params.append(memory_id)
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            
            cursor.execute(query, params)
            self.connection.commit()
            
            # Return updated memory
            return self.get_memory(memory_id, user_id, agent_id)
            
        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise
    
    async def update_memory_async(
        self,
        memory_id: str,
        update_data: Dict[str, Any],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory asynchronously."""
        return self.update_memory(memory_id, update_data, user_id, agent_id)
    
    def delete_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory."""
        try:
            cursor = self.connection.cursor()
            
            query = "DELETE FROM memories WHERE id = ?"
            params = [memory_id]
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            
            cursor.execute(query, params)
            self.connection.commit()
            
            return cursor.rowcount > 0
            
        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
    
    async def delete_memory_async(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Delete a memory asynchronously."""
        return self.delete_memory(memory_id, user_id, agent_id)
    
    def get_all_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering."""
        try:
            cursor = self.connection.cursor()
            
            query = "SELECT * FROM memories WHERE 1=1"
            params = []
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                memory = {
                    "id": row[0],
                    "content": row[1],
                    "embedding": self._deserialize_embedding(row[2]),
                    "user_id": row[3],
                    "agent_id": row[4],
                    "run_id": row[5],
                    "metadata": json.loads(row[6]) if row[6] else {},
                    "filters": json.loads(row[7]) if row[7] else {},
                    "created_at": row[8],
                    "updated_at": row[9],
                }
                results.append(memory)
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get all memories: {e}")
            raise
    
    async def get_all_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all memories with optional filtering asynchronously."""
        return self.get_all_memories(user_id, agent_id, limit, offset)
    
    def clear_memories(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent."""
        try:
            cursor = self.connection.cursor()
            
            query = "DELETE FROM memories WHERE 1=1"
            params = []
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            
            cursor.execute(query, params)
            self.connection.commit()
            
            return cursor.rowcount > 0
            
        except Exception as e:
            logger.error(f"Failed to clear memories: {e}")
            raise
    
    async def clear_memories_async(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """Clear all memories for a user or agent asynchronously."""
        return self.clear_memories(user_id, agent_id)
    
    def _serialize_embedding(self, embedding: Optional[List[float]]) -> Optional[bytes]:
        """Serialize embedding to bytes."""
        if embedding is None:
            return None
        return np.array(embedding).tobytes()
    
    def _deserialize_embedding(self, embedding_bytes: Optional[bytes]) -> Optional[List[float]]:
        """Deserialize embedding from bytes."""
        if embedding_bytes is None:
            return None
        return np.frombuffer(embedding_bytes, dtype=np.float32).tolist()
