"""
Intelligence manager

This module provides the main intelligence management interface.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from .intelligent_memory_manager import IntelligentMemoryManager

logger = logging.getLogger(__name__)


class IntelligenceManager:
    """
    Main intelligence manager for memory processing.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize intelligence manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.intelligent_memory_manager = IntelligentMemoryManager(self.config)
        
        logger.info("IntelligenceManager initialized")
    
    def process_content(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Process content with intelligence.
        
        Args:
            content: Content to process
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Processed content
        """
        return self.intelligent_memory_manager.process_content(content, metadata, context)
    
    async def process_content_async(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Process content with intelligence asynchronously.
        
        Args:
            content: Content to process
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Processed content
        """
        return await self.intelligent_memory_manager.process_content_async(content, metadata, context)
    
    def process_search_results(
        self,
        results: List[Dict[str, Any]],
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Process search results with intelligence.
        
        Args:
            results: Search results
            query: Original query
            
        Returns:
            Processed and ranked results
        """
        return self.intelligent_memory_manager.process_search_results(results, query)
    
    async def process_search_results_async(
        self,
        results: List[Dict[str, Any]],
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Process search results with intelligence asynchronously.
        
        Args:
            results: Search results
            query: Original query
            
        Returns:
            Processed and ranked results
        """
        return await self.intelligent_memory_manager.process_search_results_async(results, query)
    
    def optimize_memories(self) -> Dict[str, Any]:
        """
        Optimize memory storage.
        
        Returns:
            Optimization results
        """
        return self.intelligent_memory_manager.optimize_memories()
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get memory statistics.
        
        Returns:
            Memory statistics
        """
        return self.intelligent_memory_manager.get_memory_stats()
