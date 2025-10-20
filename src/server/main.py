"""
FastAPI server implementation

This module provides the FastAPI server for smartmem.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Initialize a default Memory instance for demo/server use
try:
    from mem.core.memory import Memory
    memory_instance = Memory()
except Exception:  # If initialization fails due to missing deps, keep None
    memory_instance = None

logger = logging.getLogger(__name__)

# Pydantic models
class MemoryCreate(BaseModel):
    content: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class MemoryUpdate(BaseModel):
    content: str
    metadata: Optional[Dict[str, Any]] = None

class MemorySearch(BaseModel):
    query: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    limit: int = 10

# Create FastAPI app
app = FastAPI(
    title="smartmem API",
    description="Intelligent Memory Management System API",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global memory instance (would be injected in real implementation)

def get_memory():
    """Get memory instance."""
    if memory_instance is None:
        raise HTTPException(status_code=500, detail="Memory instance not initialized")
    return memory_instance

@app.post("/api/v1/memories")
async def create_memory(req: MemoryCreate, memory=Depends(get_memory)):
    """Create a new memory."""
    try:
        result = memory.add(
            content=req.content,
            user_id=req.user_id,
            agent_id=req.agent_id,
            run_id=req.run_id,
            metadata=req.metadata
        )
        return result
    except Exception as e:
        logger.error(f"Failed to create memory: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/memories/search")
async def search_memories(
    query: str,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 10,
    memory=Depends(get_memory)
):
    """Search memories."""
    try:
        results = memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            limit=limit
        )
        return results
    except Exception as e:
        logger.error(f"Failed to search memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/memories/{memory_id}")
async def get_memory_by_id(memory_id: str, memory=Depends(get_memory)):
    """Get a specific memory by ID."""
    try:
        result = memory.get(memory_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get memory {memory_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    memory_update: MemoryUpdate,
    memory=Depends(get_memory)
):
    """Update a memory."""
    try:
        result = memory.update(
            memory_id=memory_id,
            content=memory_update.content,
            metadata=memory_update.metadata
        )
        return result
    except Exception as e:
        logger.error(f"Failed to update memory {memory_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/memories/{memory_id}")
async def delete_memory(memory_id: str, memory=Depends(get_memory)):
    """Delete a memory."""
    try:
        result = memory.delete(memory_id)
        if not result:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"message": "Memory deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete memory {memory_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "smartmem API", "version": "0.1.0"}
