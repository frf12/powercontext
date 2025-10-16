"""
Storage configuration management

This module handles storage configuration and validation.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class StorageConfig(BaseModel):
    """Storage configuration model."""
    
    provider: str = Field(
        default="sqlite",
        description="Storage provider (sqlite, postgres, oceanbase)"
    )
    config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Provider-specific configuration"
    )
    
    class Config:
        extra = "allow"


class SQLiteConfig(BaseModel):
    """SQLite storage configuration."""
    
    database_path: str = Field(
        default="./data/smartmem.db",
        description="Path to SQLite database file"
    )
    enable_wal: bool = Field(
        default=True,
        description="Enable WAL mode for better concurrency"
    )
    timeout: int = Field(
        default=30,
        description="Connection timeout in seconds"
    )


class PostgreSQLConfig(BaseModel):
    """PostgreSQL storage configuration."""
    
    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, description="Database port")
    database: str = Field(default="smartmem", description="Database name")
    username: str = Field(default="postgres", description="Database username")
    password: str = Field(default="", description="Database password")
    ssl_mode: str = Field(default="prefer", description="SSL mode")
    pool_size: int = Field(default=10, description="Connection pool size")
    max_overflow: int = Field(default=20, description="Maximum pool overflow")


class OceanBaseConfig(BaseModel):
    """OceanBase storage configuration."""
    
    host: str = Field(default="localhost", description="OceanBase host")
    port: int = Field(default=2881, description="OceanBase port")
    user: str = Field(default="root@test", description="OceanBase user")
    password: str = Field(default="", description="OceanBase password")
    database: str = Field(default="test", description="OceanBase database")
    charset: str = Field(default="utf8mb4", description="Character set")
    pool_size: int = Field(default=10, description="Connection pool size")
