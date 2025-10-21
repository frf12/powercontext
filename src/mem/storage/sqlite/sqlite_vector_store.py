"""
SQLite vector store implementation

This module provides a simple SQLite-based vector store for development and testing.
"""

import json
import logging
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from mem.storage.base import VectorStoreBase, OutputData

logger = logging.getLogger(__name__)


class SQLiteVectorStore(VectorStoreBase):
    """Simple SQLite-based vector store implementation."""
    
    def __init__(self, database_path: str = ":memory:", collection_name: str = "memories", **kwargs):
        """
        Initialize SQLite vector store.
        
        Args:
            database_path: Path to SQLite database file
            collection_name: Name of the collection/table
        """
        self.db_path = database_path
        self.collection_name = collection_name
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self._lock = threading.Lock()
        
        # Create the table
        self.create_col()
        
        logger.info(f"SQLiteVectorStore initialized with db_path: {database_path}")
    
    def create_col(self, name=None, vector_size=None, distance=None) -> None:
        """Create a new collection (table in SQLite)."""
        table_name = name or self.collection_name
        
        with self._lock:
            self.connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id TEXT PRIMARY KEY,
                    vector TEXT,  -- Store as JSON string
                    payload TEXT,  -- Store as JSON string
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.connection.commit()
    
    def insert(self, vectors: List[List[float]], payloads=None, ids=None) -> None:
        """Insert vectors into the collection."""
        if not vectors:
            return
            
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in vectors]
        
        if payloads is None:
            payloads = [{} for _ in vectors]
        
        with self._lock:
            for i, (vector, payload, vector_id) in enumerate(zip(vectors, payloads, ids)):
                self.connection.execute(f"""
                    INSERT OR REPLACE INTO {self.collection_name} 
                    (id, vector, payload) VALUES (?, ?, ?)
                """, (vector_id, json.dumps(vector), json.dumps(payload)))
            
            self.connection.commit()
    
    def search(self, query: str, vectors: List[List[float]] = None, limit: int = 5, filters=None) -> List[OutputData]:
        """Search for similar vectors using simple cosine similarity."""
        results = []
        
        # Extract query vector from vectors parameter (OceanBase format)
        if vectors and len(vectors) > 0:
            query_vector = vectors[0]
        else:
            # Fallback for backward compatibility
            query_vector = query if isinstance(query, list) else [0.1] * 10
        
        with self._lock:
            cursor = self.connection.execute(f"""
                SELECT id, vector, payload FROM {self.collection_name}
            """)
            
            for row in cursor.fetchall():
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)
                
                # Calculate cosine similarity
                similarity = self._cosine_similarity(query_vector, vector)
                
                results.append(OutputData(
                    id=vector_id,
                    score=similarity,
                    payload=payload
                ))
        
        # Sort by similarity (descending) and return top results
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]
    
    def delete(self, vector_id: str) -> None:
        """Delete a vector by ID."""
        with self._lock:
            self.connection.execute(f"""
                DELETE FROM {self.collection_name} WHERE id = ?
            """, (vector_id,))
            self.connection.commit()
    
    def update(self, vector_id: str, vector=None, payload=None) -> None:
        """Update a vector and its payload."""
        updates = []
        values = []
        
        if vector is not None:
            updates.append("vector = ?")
            values.append(json.dumps(vector))
        
        if payload is not None:
            updates.append("payload = ?")
            values.append(json.dumps(payload))
        
        if updates:
            values.append(vector_id)
            with self._lock:
                self.connection.execute(f"""
                    UPDATE {self.collection_name} 
                    SET {', '.join(updates)}
                    WHERE id = ?
                """, values)
                self.connection.commit()
    
    def get(self, vector_id: str) -> Optional[OutputData]:
        """Retrieve a vector by ID."""
        with self._lock:
            cursor = self.connection.execute(f"""
                SELECT id, vector, payload FROM {self.collection_name} WHERE id = ?
            """, (vector_id,))
            
            row = cursor.fetchone()
            if row:
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)
                
                return OutputData(
                    id=vector_id,
                    score=1.0,  # Exact match
                    payload=payload
                )
        
        return None
    
    def list_cols(self) -> List[str]:
        """List all collections (tables)."""
        with self._lock:
            cursor = self.connection.execute("""
                SELECT name FROM sqlite_master WHERE type='table'
            """)
            return [row[0] for row in cursor.fetchall()]
    
    def delete_col(self) -> None:
        """Delete the collection (table)."""
        with self._lock:
            self.connection.execute(f"DROP TABLE IF EXISTS {self.collection_name}")
            self.connection.commit()
    
    def col_info(self) -> Dict[str, Any]:
        """Get information about the collection."""
        with self._lock:
            cursor = self.connection.execute(f"""
                SELECT COUNT(*) FROM {self.collection_name}
            """)
            count = cursor.fetchone()[0]
            
            return {
                "name": self.collection_name,
                "count": count,
                "db_path": self.db_path
            }
    
    def list(self, filters=None, limit=None) -> List[OutputData]:
        """List all memories."""
        query = f"SELECT id, vector, payload FROM {self.collection_name}"
        
        if limit:
            query += f" LIMIT {limit}"
        
        results = []
        with self._lock:
            cursor = self.connection.execute(query)
            
            for row in cursor.fetchall():
                vector_id, vector_str, payload_str = row
                vector = json.loads(vector_str)
                payload = json.loads(payload_str)
                
                results.append(OutputData(
                    id=vector_id,
                    score=1.0,
                    payload=payload
                ))
        
        return results
    
    def reset(self) -> None:
        """Reset by deleting and recreating the collection."""
        self.delete_col()
        self.create_col()
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return dot_product / (magnitude1 * magnitude2)
    
    def close(self) -> None:
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
    
    def __del__(self):
        self.close()
