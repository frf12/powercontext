"""
OceanBase graph storage implementation

This module provides OceanBase-based graph storage for memory data.
"""

import logging
import json
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

try:
    from pyobvector import ObVecClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
except ImportError as e:
    raise ImportError(f"Required dependencies not found: {e}. Please install pyobvector and sqlalchemy.")

from ..base import StorageBase

logger = logging.getLogger(__name__)


class OceanBaseGraphStorage(StorageBase):
    """
    OceanBase-based graph storage implementation.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize OceanBase graph storage.
        
        Args:
            config: Storage configuration
        """
        super().__init__(config)
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 2881)
        self.user = config.get("user", "root@test")
        self.password = config.get("password", "")
        self.database = config.get("database", "test")
        self.charset = config.get("charset", "utf8mb4")
        self.pool_size = config.get("pool_size", 10)
        
        # Create database connection
        self.engine = create_engine(
            f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}",
            pool_size=self.pool_size,
            pool_recycle=3600,
            echo=False
        )
        
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.ob_client = None
    
    def initialize(self) -> None:
        """Initialize OceanBase graph database."""
        try:
            # Initialize OceanBase vector client
            self.ob_client = ObVecClient(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
            
            # Create graph tables
            self._create_graph_tables()
            
            logger.info(f"OceanBase graph storage initialized: {self.host}:{self.port}/{self.database}")
            
        except Exception as e:
            logger.error(f"Failed to initialize OceanBase graph storage: {e}")
            raise
    
    async def initialize_async(self) -> None:
        """Initialize OceanBase graph database asynchronously."""
        # OceanBase doesn't support async operations natively
        self.initialize()
    
    def _create_graph_tables(self) -> None:
        """Create graph database tables."""
        session = self.SessionLocal()
        try:
            # Create nodes table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id VARCHAR(36) PRIMARY KEY,
                    type VARCHAR(50) NOT NULL,
                    properties TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """))
            
            # Create edges table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id VARCHAR(36) PRIMARY KEY,
                    source_id VARCHAR(36) NOT NULL,
                    target_id VARCHAR(36) NOT NULL,
                    relationship VARCHAR(50) NOT NULL,
                    properties TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES graph_nodes(id),
                    FOREIGN KEY (target_id) REFERENCES graph_nodes(id)
                )
            """))
            
            # Create indexes
            session.execute(text("CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(type)"))
            session.execute(text("CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_id)"))
            session.execute(text("CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_id)"))
            session.execute(text("CREATE INDEX IF NOT EXISTS idx_edges_relationship ON graph_edges(relationship)"))
            
            session.commit()
            
        finally:
            session.close()
    
    def add_memory(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to graph storage."""
        try:
            memory_id = str(uuid.uuid4())
            
            session = self.SessionLocal()
            try:
                # Create memory node
                session.execute(text("""
                    INSERT INTO graph_nodes (id, type, properties, created_at, updated_at)
                    VALUES (:id, 'memory', :properties, :created_at, :updated_at)
                """), {
                    "id": memory_id,
                    "properties": json.dumps({
                        "content": memory_data["content"],
                        "user_id": memory_data.get("user_id"),
                        "agent_id": memory_data.get("agent_id"),
                        "run_id": memory_data.get("run_id"),
                        "metadata": memory_data.get("metadata", {}),
                        "filters": memory_data.get("filters", {}),
                    }),
                    "created_at": memory_data.get("created_at", datetime.utcnow()),
                    "updated_at": memory_data.get("updated_at", datetime.utcnow()),
                })
                
                # Create relationships
                if memory_data.get("user_id"):
                    self._create_relationship(session, memory_id, memory_data["user_id"], "belongs_to")
                
                if memory_data.get("agent_id"):
                    self._create_relationship(session, memory_id, memory_data["agent_id"], "created_by")
                
                session.commit()
                
                return memory_id
                
            finally:
                session.close()
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            raise
    
    def _create_relationship(self, session, source_id: str, target_id: str, relationship: str) -> None:
        """Create a relationship between nodes."""
        # Ensure target node exists
        session.execute(text("""
            INSERT IGNORE INTO graph_nodes (id, type, properties, created_at, updated_at)
            VALUES (:id, :type, '{}', NOW(), NOW())
        """), {
            "id": target_id,
            "type": "user" if relationship == "belongs_to" else "agent"
        })
        
        # Create edge
        edge_id = str(uuid.uuid4())
        session.execute(text("""
            INSERT INTO graph_edges (id, source_id, target_id, relationship, created_at, updated_at)
            VALUES (:id, :source_id, :target_id, :relationship, NOW(), NOW())
        """), {
            "id": edge_id,
            "source_id": source_id,
            "target_id": target_id,
            "relationship": relationship
        })
    
    async def add_memory_async(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to graph storage asynchronously."""
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
        """Search for memories using graph traversal."""
        try:
            session = self.SessionLocal()
            try:
                # Build query with graph traversal
                query = """
                    SELECT n.id, n.properties, n.created_at, n.updated_at
                    FROM graph_nodes n
                    WHERE n.type = 'memory'
                """
                params = {}
                
                if user_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :user_id AND e.relationship = 'belongs_to'
                        )
                    """
                    params["user_id"] = user_id
                
                if agent_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :agent_id AND e.relationship = 'created_by'
                        )
                    """
                    params["agent_id"] = agent_id
                
                query += " ORDER BY n.created_at DESC LIMIT :limit"
                params["limit"] = limit
                
                result = session.execute(text(query), params)
                rows = result.fetchall()
                
                # Convert to list of dictionaries
                results = []
                for row in rows:
                    properties = json.loads(row[1])
                    memory = {
                        "id": row[0],
                        "content": properties.get("content", ""),
                        "user_id": properties.get("user_id"),
                        "agent_id": properties.get("agent_id"),
                        "run_id": properties.get("run_id"),
                        "metadata": properties.get("metadata", {}),
                        "filters": properties.get("filters", {}),
                        "created_at": row[2],
                        "updated_at": row[3],
                    }
                    results.append(memory)
                
                return results
                
            finally:
                session.close()
            
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
        """Search for memories using graph traversal asynchronously."""
        return self.search_memories(query_embedding, user_id, agent_id, run_id, filters, limit)
    
    def get_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID."""
        try:
            session = self.SessionLocal()
            try:
                query = """
                    SELECT n.id, n.properties, n.created_at, n.updated_at
                    FROM graph_nodes n
                    WHERE n.id = :memory_id AND n.type = 'memory'
                """
                params = {"memory_id": memory_id}
                
                if user_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :user_id AND e.relationship = 'belongs_to'
                        )
                    """
                    params["user_id"] = user_id
                
                if agent_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :agent_id AND e.relationship = 'created_by'
                        )
                    """
                    params["agent_id"] = agent_id
                
                result = session.execute(text(query), params)
                row = result.fetchone()
                
                if row:
                    properties = json.loads(row[1])
                    return {
                        "id": row[0],
                        "content": properties.get("content", ""),
                        "user_id": properties.get("user_id"),
                        "agent_id": properties.get("agent_id"),
                        "run_id": properties.get("run_id"),
                        "metadata": properties.get("metadata", {}),
                        "filters": properties.get("filters", {}),
                        "created_at": row[2],
                        "updated_at": row[3],
                    }
                
                return None
                
            finally:
                session.close()
            
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
            session = self.SessionLocal()
            try:
                # Get current memory
                current_memory = self.get_memory(memory_id, user_id, agent_id)
                if not current_memory:
                    raise ValueError(f"Memory {memory_id} not found")
                
                # Update properties
                properties = {
                    "content": update_data.get("content", current_memory["content"]),
                    "user_id": current_memory["user_id"],
                    "agent_id": current_memory["agent_id"],
                    "run_id": current_memory["run_id"],
                    "metadata": update_data.get("metadata", current_memory["metadata"]),
                    "filters": current_memory["filters"],
                }
                
                session.execute(text("""
                    UPDATE graph_nodes
                    SET properties = :properties, updated_at = NOW()
                    WHERE id = :memory_id AND type = 'memory'
                """), {
                    "memory_id": memory_id,
                    "properties": json.dumps(properties)
                })
                
                session.commit()
                
                # Return updated memory
                return self.get_memory(memory_id, user_id, agent_id)
                
            finally:
                session.close()
            
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
            session = self.SessionLocal()
            try:
                # Check if memory exists and user has access
                memory = self.get_memory(memory_id, user_id, agent_id)
                if not memory:
                    return False
                
                # Delete edges first
                session.execute(text("""
                    DELETE FROM graph_edges
                    WHERE source_id = :memory_id OR target_id = :memory_id
                """), {"memory_id": memory_id})
                
                # Delete node
                session.execute(text("""
                    DELETE FROM graph_nodes
                    WHERE id = :memory_id AND type = 'memory'
                """), {"memory_id": memory_id})
                
                session.commit()
                
                return True
                
            finally:
                session.close()
            
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
            session = self.SessionLocal()
            try:
                query = """
                    SELECT n.id, n.properties, n.created_at, n.updated_at
                    FROM graph_nodes n
                    WHERE n.type = 'memory'
                """
                params = {}
                
                if user_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :user_id AND e.relationship = 'belongs_to'
                        )
                    """
                    params["user_id"] = user_id
                
                if agent_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :agent_id AND e.relationship = 'created_by'
                        )
                    """
                    params["agent_id"] = agent_id
                
                query += " ORDER BY n.created_at DESC LIMIT :limit OFFSET :offset"
                params["limit"] = limit
                params["offset"] = offset
                
                result = session.execute(text(query), params)
                rows = result.fetchall()
                
                results = []
                for row in rows:
                    properties = json.loads(row[1])
                    memory = {
                        "id": row[0],
                        "content": properties.get("content", ""),
                        "user_id": properties.get("user_id"),
                        "agent_id": properties.get("agent_id"),
                        "run_id": properties.get("run_id"),
                        "metadata": properties.get("metadata", {}),
                        "filters": properties.get("filters", {}),
                        "created_at": row[2],
                        "updated_at": row[3],
                    }
                    results.append(memory)
                
                return results
                
            finally:
                session.close()
            
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
            session = self.SessionLocal()
            try:
                # Get memory IDs to delete
                query = """
                    SELECT n.id
                    FROM graph_nodes n
                    WHERE n.type = 'memory'
                """
                params = {}
                
                if user_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :user_id AND e.relationship = 'belongs_to'
                        )
                    """
                    params["user_id"] = user_id
                
                if agent_id:
                    query += """
                        AND EXISTS (
                            SELECT 1 FROM graph_edges e
                            WHERE e.source_id = n.id AND e.target_id = :agent_id AND e.relationship = 'created_by'
                        )
                    """
                    params["agent_id"] = agent_id
                
                result = session.execute(text(query), params)
                memory_ids = [row[0] for row in result.fetchall()]
                
                if not memory_ids:
                    return False
                
                # Delete edges
                for memory_id in memory_ids:
                    session.execute(text("""
                        DELETE FROM graph_edges
                        WHERE source_id = :memory_id OR target_id = :memory_id
                    """), {"memory_id": memory_id})
                
                # Delete nodes
                for memory_id in memory_ids:
                    session.execute(text("""
                        DELETE FROM graph_nodes
                        WHERE id = :memory_id AND type = 'memory'
                    """), {"memory_id": memory_id})
                
                session.commit()
                
                return True
                
            finally:
                session.close()
            
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
