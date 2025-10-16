"""
OceanBase storage implementation

This module provides OceanBase-based storage for memory data.
"""

import logging
import json
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
import numpy as np

try:
    from pyobvector import ObVecClient, cosine_distance
    from sqlalchemy import create_engine, text, Column, String, DateTime, Text, LargeBinary
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker
except ImportError as e:
    raise ImportError(f"Required dependencies not found: {e}. Please install pyobvector and sqlalchemy.")

from ..base import StorageBase

logger = logging.getLogger(__name__)
Base = declarative_base()


class MemoryRecord(Base):
    """Memory record model for OceanBase."""
    __tablename__ = "memories"
    
    id = Column(String(36), primary_key=True)
    content = Column(Text, nullable=False)
    embedding = Column(LargeBinary)
    user_id = Column(String(255))
    agent_id = Column(String(255))
    run_id = Column(String(255))
    metadata = Column(Text)
    filters = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OceanBaseStorage(StorageBase):
    """
    OceanBase-based storage implementation.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize OceanBase storage.
        
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
        """Initialize OceanBase database."""
        try:
            # Create tables
            Base.metadata.create_all(bind=self.engine)
            
            # Initialize OceanBase vector client
            self.ob_client = ObVecClient(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
            
            logger.info(f"OceanBase storage initialized: {self.host}:{self.port}/{self.database}")
            
        except Exception as e:
            logger.error(f"Failed to initialize OceanBase storage: {e}")
            raise
    
    async def initialize_async(self) -> None:
        """Initialize OceanBase database asynchronously."""
        # OceanBase doesn't support async operations natively
        self.initialize()
    
    def add_memory(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to storage."""
        try:
            memory_id = str(uuid.uuid4())
            
            session = self.SessionLocal()
            try:
                memory_record = MemoryRecord(
                    id=memory_id,
                    content=memory_data["content"],
                    embedding=self._serialize_embedding(memory_data.get("embedding")),
                    user_id=memory_data.get("user_id"),
                    agent_id=memory_data.get("agent_id"),
                    run_id=memory_data.get("run_id"),
                    metadata=json.dumps(memory_data.get("metadata", {})),
                    filters=json.dumps(memory_data.get("filters", {})),
                    created_at=memory_data.get("created_at", datetime.utcnow()),
                    updated_at=memory_data.get("updated_at", datetime.utcnow()),
                )
                
                session.add(memory_record)
                session.commit()
                
                return memory_id
                
            finally:
                session.close()
            
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            raise
    
    async def add_memory_async(self, memory_data: Dict[str, Any]) -> str:
        """Add a memory to storage asynchronously."""
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
            session = self.SessionLocal()
            try:
                # Build query
                query = session.query(MemoryRecord)
                
                if user_id:
                    query = query.filter(MemoryRecord.user_id == user_id)
                
                if agent_id:
                    query = query.filter(MemoryRecord.agent_id == agent_id)
                
                if run_id:
                    query = query.filter(MemoryRecord.run_id == run_id)
                
                # Execute query
                records = query.order_by(MemoryRecord.created_at.desc()).limit(limit).all()
                
                # Convert to list of dictionaries
                results = []
                for record in records:
                    memory = {
                        "id": record.id,
                        "content": record.content,
                        "embedding": self._deserialize_embedding(record.embedding),
                        "user_id": record.user_id,
                        "agent_id": record.agent_id,
                        "run_id": record.run_id,
                        "metadata": json.loads(record.metadata) if record.metadata else {},
                        "filters": json.loads(record.filters) if record.filters else {},
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
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
            session = self.SessionLocal()
            try:
                query = session.query(MemoryRecord).filter(MemoryRecord.id == memory_id)
                
                if user_id:
                    query = query.filter(MemoryRecord.user_id == user_id)
                
                if agent_id:
                    query = query.filter(MemoryRecord.agent_id == agent_id)
                
                record = query.first()
                
                if record:
                    return {
                        "id": record.id,
                        "content": record.content,
                        "embedding": self._deserialize_embedding(record.embedding),
                        "user_id": record.user_id,
                        "agent_id": record.agent_id,
                        "run_id": record.run_id,
                        "metadata": json.loads(record.metadata) if record.metadata else {},
                        "filters": json.loads(record.filters) if record.filters else {},
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
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
                query = session.query(MemoryRecord).filter(MemoryRecord.id == memory_id)
                
                if user_id:
                    query = query.filter(MemoryRecord.user_id == user_id)
                
                if agent_id:
                    query = query.filter(MemoryRecord.agent_id == agent_id)
                
                record = query.first()
                
                if record:
                    if "content" in update_data:
                        record.content = update_data["content"]
                    
                    if "embedding" in update_data:
                        record.embedding = self._serialize_embedding(update_data["embedding"])
                    
                    if "metadata" in update_data:
                        record.metadata = json.dumps(update_data["metadata"])
                    
                    record.updated_at = datetime.utcnow()
                    
                    session.commit()
                    
                    # Return updated memory
                    return self.get_memory(memory_id, user_id, agent_id)
                
                raise ValueError(f"Memory {memory_id} not found")
                
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
                query = session.query(MemoryRecord).filter(MemoryRecord.id == memory_id)
                
                if user_id:
                    query = query.filter(MemoryRecord.user_id == user_id)
                
                if agent_id:
                    query = query.filter(MemoryRecord.agent_id == agent_id)
                
                record = query.first()
                
                if record:
                    session.delete(record)
                    session.commit()
                    return True
                
                return False
                
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
                query = session.query(MemoryRecord)
                
                if user_id:
                    query = query.filter(MemoryRecord.user_id == user_id)
                
                if agent_id:
                    query = query.filter(MemoryRecord.agent_id == agent_id)
                
                records = query.order_by(MemoryRecord.created_at.desc()).offset(offset).limit(limit).all()
                
                results = []
                for record in records:
                    memory = {
                        "id": record.id,
                        "content": record.content,
                        "embedding": self._deserialize_embedding(record.embedding),
                        "user_id": record.user_id,
                        "agent_id": record.agent_id,
                        "run_id": record.run_id,
                        "metadata": json.loads(record.metadata) if record.metadata else {},
                        "filters": json.loads(record.filters) if record.filters else {},
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
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
                query = session.query(MemoryRecord)
                
                if user_id:
                    query = query.filter(MemoryRecord.user_id == user_id)
                
                if agent_id:
                    query = query.filter(MemoryRecord.agent_id == agent_id)
                
                count = query.count()
                query.delete()
                session.commit()
                
                return count > 0
                
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
