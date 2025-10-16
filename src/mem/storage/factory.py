"""
Storage factory for creating storage instances

This module provides a factory for creating different storage backends.
"""

import logging
from typing import Any, Dict, Type
from .base import StorageBase
from .config import StorageConfig

logger = logging.getLogger(__name__)


class StorageFactory:
    """
    Factory for creating storage instances.
    """
    
    _storage_registry: Dict[str, Type[StorageBase]] = {}
    
    @classmethod
    def register_storage(cls, name: str, storage_class: Type[StorageBase]) -> None:
        """
        Register a storage implementation.
        
        Args:
            name: Storage name
            storage_class: Storage implementation class
        """
        cls._storage_registry[name] = storage_class
        logger.info(f"Registered storage: {name}")
    
    @classmethod
    def create(cls, storage_type: str, config: Dict[str, Any]) -> StorageBase:
        """
        Create a storage instance.
        
        Args:
            storage_type: Type of storage to create
            config: Storage configuration
            
        Returns:
            Storage instance
            
        Raises:
            ValueError: If storage type is not supported
        """
        if storage_type not in cls._storage_registry:
            raise ValueError(f"Unsupported storage type: {storage_type}")
        
        storage_class = cls._storage_registry[storage_type]
        return storage_class(config)
    
    @classmethod
    def get_supported_storages(cls) -> list:
        """
        Get list of supported storage types.
        
        Returns:
            List of supported storage types
        """
        return list(cls._storage_registry.keys())


# Register built-in storage implementations
def register_builtin_storages():
    """Register built-in storage implementations."""
    try:
        from .sqlite import SQLiteStorage
        StorageFactory.register_storage("sqlite", SQLiteStorage)
    except ImportError:
        logger.warning("SQLite storage not available")
    
    try:
        from .postgres.postgres import PostgreSQLStorage
        StorageFactory.register_storage("postgres", PostgreSQLStorage)
    except ImportError:
        logger.warning("PostgreSQL storage not available")
    
    try:
        from .oceanbase.oceanbase import OceanBaseStorage
        StorageFactory.register_storage("oceanbase", OceanBaseStorage)
    except ImportError:
        logger.warning("OceanBase storage not available")


# Auto-register built-in storages
register_builtin_storages()
