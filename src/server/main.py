"""
FastAPI server implementation

This module provides the FastAPI server for powermem.
"""

import logging
import os
from typing import Any, Dict, List, Optional
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AsyncTokenTracker:
    """Track token usage asynchronously."""
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0
        self._lock = asyncio.Lock()

    async def track_token(self, usage):
        """Async update token counts."""
        async with self._lock:
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
            self.total_tokens += usage.total_tokens
            if hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                if hasattr(usage.prompt_tokens_details, 'cached_tokens'):
                    self.cached_tokens += usage.prompt_tokens_details.cached_tokens

    def get_token_count(self):
        return self.total_tokens

    def get_detailed_stats(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens
        }

    def reset(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0


async_token_tracker = AsyncTokenTracker()


# Load configuration from environment variables
TOKEN_COUNTING = os.getenv("TOKEN_COUNTING", "false").lower() in ("true", "1", "yes")

# Initialize a default Memory instance for demo/server use
memory_instance = None
try:
    from mem.core.memory import Memory
    
    # Try to load configuration from environment
    vector_store_config = None
    db_type = os.getenv("DB_TYPE", "")
    
    if db_type == "oceanbase":
        vector_store_config = {
            "provider": "oceanbase",
            "config": {
                "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
                "port": os.getenv("OCEANBASE_PORT", "2881"),
                "user": os.getenv("OCEANBASE_USER", "root"),
                "password": os.getenv("OCEANBASE_PASSWORD", ""),
                "db_name": os.getenv("OCEANBASE_DB", "ai_work"),
                "collection_name": os.getenv("OCEANBASE_COLLECTION", "powermem_collection"),
                "embedding_model_dims": int(os.getenv("EMBEDDING_DIMS", "1536")),
            },
        }
    elif db_type == "postgres":
        vector_store_config = {
            "provider": "pgvector",
            "config": {
                "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
                "port": os.getenv("POSTGRES_PORT", "5432"),
                "user": os.getenv("POSTGRES_USER", "postgres"),
                "password": os.getenv("POSTGRES_PASSWORD", ""),
                "dbname": os.getenv("POSTGRES_DB", "powermem"),
                "collection_name": os.getenv("POSTGRES_COLLECTION", "memories"),
            },
        }
    
    # Only initialize if we have a config
    if vector_store_config:
        config = {
            "vector_store": vector_store_config,
            "llm": {
                "provider": os.getenv("LLM_PROVIDER", "openai"),
                "config": {
                    "api_key": os.getenv("OPENAI_API_KEY", ""),
                    "model": os.getenv("LLM_MODEL", "gpt-3.5-turbo"),
                    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.2")),
                }
            },
            "embedder": {
                "provider": os.getenv("EMBEDDER_PROVIDER", "openai"),
                "config": {
                    "api_key": os.getenv("OPENAI_API_KEY", ""),
                    "model": os.getenv("EMBEDDER_MODEL", "text-embedding-ada-002"),
                }
            },
        }
        memory_instance = Memory.from_config(config)
    else:
        # Use default initialization
        memory_instance = Memory()
        
    logger.info("Memory instance initialized successfully")
    
except Exception as e:
    logger.warning(f"Failed to initialize memory instance: {e}")
    memory_instance = None

# Pydantic models
class Message(BaseModel):
    role: str
    content: str


class MemoryCreate(BaseModel):
    messages: Optional[List[Message]] = None
    content: Optional[str] = None
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
    run_id: Optional[str] = None
    limit: int = 10
    filters: Optional[Dict[str, Any]] = None

# Create FastAPI app
app = FastAPI(
    title="powermem API",
    description="Intelligent Memory Management System API",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_memory():
    """Get memory instance."""
    if memory_instance is None:
        raise HTTPException(status_code=500, detail="Memory instance not initialized")
    return memory_instance

@app.post("/api/v1/configure", summary="Configure Memory")
def set_config(config: Dict[str, Any]):
    """Set memory configuration."""
    global memory_instance
    try:
        memory_instance = Memory.from_config(config)
        return {"message": "Configuration set successfully"}
    except Exception as e:
        logger.exception("Error in set_config:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/memories", summary="Create memories")
async def create_memory(req: MemoryCreate, memory=Depends(get_memory)):
    """Create a new memory."""
    try:
        if not any([req.user_id, req.agent_id, req.run_id]):
            raise HTTPException(status_code=400, detail="At least one identifier (user_id, agent_id, run_id) is required.")
        
        if req.messages:
            # Handle messages format
            params = {k: v for k, v in req.model_dump().items() if v is not None and k not in ["messages", "content"]}
            result = memory.add(messages=[m.model_dump() for m in req.messages], **params)
        elif req.content:
            # Handle content format
            result = memory.add(
                content=req.content,
                user_id=req.user_id,
                agent_id=req.agent_id,
                run_id=req.run_id,
                metadata=req.metadata
            )
        else:
            raise HTTPException(status_code=400, detail="Either 'content' or 'messages' must be provided.")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in create_memory:")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/memories", summary="Get all memories")
async def get_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    memory=Depends(get_memory)
):
    """Retrieve stored memories."""
    try:
        if not any([user_id, run_id, agent_id]):
            raise HTTPException(status_code=400, detail="At least one identifier is required.")
        
        params = {
            k: v for k, v in {"user_id": user_id, "run_id": run_id, "agent_id": agent_id}.items() if v is not None
        }
        return memory.get_all(**params)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in get_all_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/memories/{memory_id}", summary="Get a memory")
async def get_memory_by_id(memory_id: str, memory=Depends(get_memory)):
    """Retrieve a specific memory by ID."""
    try:
        return memory.get(memory_id)
    except Exception as e:
        logger.exception("Error in get_memory_by_id:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/search", summary="Search memories")
async def search_memories(search_req: MemorySearch, memory=Depends(get_memory)):
    """Search for memories based on a query."""
    try:
        params = {k: v for k, v in search_req.model_dump().items() if v is not None and k != "query"}
        return memory.search(query=search_req.query, **params)
    except Exception as e:
        logger.exception("Error in search_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/memories/{memory_id}", summary="Update a memory")
async def update_memory(
    memory_id: str,
    updated_memory: Dict[str, Any],
    memory=Depends(get_memory)
):
    """Update an existing memory with new content."""
    try:
        return memory.update(memory_id=memory_id, data=updated_memory)
    except Exception as e:
        logger.exception("Error in update_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/memories/{memory_id}/history", summary="Get memory history")
async def memory_history(memory_id: str, memory=Depends(get_memory)):
    """Retrieve memory history."""
    try:
        return memory.history(memory_id=memory_id)
    except Exception as e:
        logger.exception("Error in memory_history:")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/memories/{memory_id}", summary="Delete a memory")
async def delete_memory(memory_id: str, memory=Depends(get_memory)):
    """Delete a specific memory by ID."""
    try:
        memory.delete(memory_id=memory_id)
        return {"message": "Memory deleted successfully"}
    except Exception as e:
        logger.exception("Error in delete_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/memories", summary="Delete all memories")
async def delete_all_memories(
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    memory=Depends(get_memory)
):
    """Delete all memories for a given identifier."""
    try:
        if not any([user_id, run_id, agent_id]):
            raise HTTPException(status_code=400, detail="At least one identifier is required.")
        
        params = {
            k: v for k, v in {"user_id": user_id, "run_id": run_id, "agent_id": agent_id}.items() if v is not None
        }
        memory.delete_all(**params)
        return {"message": "All relevant memories deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in delete_all_memories:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/reset", summary="Reset all memories")
async def reset_memory(memory=Depends(get_memory)):
    """Completely reset stored memories."""
    try:
        memory.reset()
        return {"message": "All memories reset"}
    except Exception as e:
        logger.exception("Error in reset_memory:")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}


@app.get("/api/v1/token_count", summary="Get token count")
async def get_token_count_endpoint():
    """Get token count."""
    return {"token_count": async_token_tracker.get_detailed_stats()}


@app.post("/api/v1/reset_token_count", summary="Reset token count")
async def reset_token_count():
    """Reset token count."""
    async_token_tracker.reset()
    return {"message": "Token count reset"}


@app.get("/", summary="Redirect to the OpenAPI documentation", include_in_schema=False)
async def root():
    """Redirect to the OpenAPI documentation."""
    return RedirectResponse(url="/docs")
