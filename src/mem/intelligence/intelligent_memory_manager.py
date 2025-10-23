"""
Intelligent memory manager

This module implements the main intelligent memory management system.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from .importance_evaluator import ImportanceEvaluator
from .ebbinghaus_algorithm import EbbinghausAlgorithm
from mem.integrations.llm.factory import LLMFactory

logger = logging.getLogger(__name__)


class IntelligentMemoryManager:
    """
    Intelligent Memory Manager
    
    Implements complete memory management process:
    1. New information input -> Working memory
    2. Importance evaluation -> Determine storage type
    3. Decay and reinforcement based on Ebbinghaus curve
    4. Automatic cleanup and optimization
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize intelligent memory manager.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.intelligent_config = self.config.get("intelligent_memory", {})
        
        # Initialize components
        self.importance_evaluator = ImportanceEvaluator(
            self.intelligent_config,
            self.config.get("llm", {})
        )
        self.ebbinghaus_algorithm = EbbinghausAlgorithm(self.intelligent_config)
        
        # Initialize LLM for importance evaluation
        self._initialize_llm()
        
        # Memory storage
        self.working_memories: Dict[str, Dict] = {}
        self.short_term_memories: Dict[str, Dict] = {}
        self.long_term_memories: Dict[str, Dict] = {}
        
        logger.info("IntelligentMemoryManager initialized")
    
    def _initialize_llm(self):
        """
        Initialize LLM for importance evaluation.
        """
        try:
            llm_config = self.config.get("llm", {})
            if llm_config:
                llm_provider = llm_config.get("provider", "openai")
                llm_instance = LLMFactory.create(llm_provider, llm_config.get("config", {}))
                self.importance_evaluator.set_llm(llm_instance)
                logger.info(f"LLM initialized for importance evaluation: {llm_provider}")
            else:
                logger.warning("No LLM configuration found, using rule-based evaluation only")
        except Exception as e:
            logger.error(f"Failed to initialize LLM for importance evaluation: {e}")
            logger.warning("Falling back to rule-based evaluation only")
    
    def process_content(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Process content with intelligent memory management.
        
        Args:
            content: Content to process
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Processed content
        """
        try:
            # Evaluate importance
            importance_score = self.importance_evaluator.evaluate_importance(
                content, metadata, context
            )
            
            # Determine memory type based on importance
            if importance_score >= 0.8:
                memory_type = "long_term"
            elif importance_score >= 0.5:
                memory_type = "short_term"
            else:
                memory_type = "working"
            
            # Process with Ebbinghaus algorithm
            processed_content = self.ebbinghaus_algorithm.process_memory(
                content, importance_score, memory_type
            )
            
            logger.debug(f"Processed content with importance: {importance_score}, type: {memory_type}")
            
            return processed_content
            
        except Exception as e:
            logger.error(f"Failed to process content: {e}")
            return content
    
    async def process_content_async(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Process content with intelligent memory management asynchronously.
        
        Args:
            content: Content to process
            metadata: Additional metadata
            context: Additional context
            
        Returns:
            Processed content
        """
        # For now, just call the sync version
        # In a real implementation, this would use async LLM calls
        return self.process_content(content, metadata, context)
    
    def process_search_results(
        self,
        results: List[Dict[str, Any]],
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Process search results with intelligent ranking.
        
        Args:
            results: Search results
            query: Original query
            
        Returns:
            Processed and ranked results
        """
        try:
            # Apply Ebbinghaus decay to results
            processed_results = []
            for result in results:
                # Calculate relevance score
                relevance_score = self.ebbinghaus_algorithm.calculate_relevance(
                    result, query
                )
                
                # Apply decay based on age
                decay_factor = self.ebbinghaus_algorithm.calculate_decay(
                    result.get("created_at", datetime.utcnow())
                )
                
                # Update result with processed information
                processed_result = result.copy()
                processed_result["relevance_score"] = relevance_score
                processed_result["decay_factor"] = decay_factor
                processed_result["final_score"] = relevance_score * decay_factor
                
                processed_results.append(processed_result)
            
            # Sort by final score
            processed_results.sort(key=lambda x: x["final_score"], reverse=True)
            
            logger.debug(f"Processed {len(processed_results)} search results")
            
            return processed_results
            
        except Exception as e:
            logger.error(f"Failed to process search results: {e}")
            return results
    
    async def process_search_results_async(
        self,
        results: List[Dict[str, Any]],
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Process search results with intelligent ranking asynchronously.
        
        Args:
            results: Search results
            query: Original query
            
        Returns:
            Processed and ranked results
        """
        # For now, just call the sync version
        return self.process_search_results(results, query)
    
    def optimize_memories(self) -> Dict[str, Any]:
        """
        Optimize memory storage based on usage patterns.
        
        Returns:
            Optimization results
        """
        try:
            optimization_results = {
                "working_to_short": 0,
                "short_to_long": 0,
                "long_to_archive": 0,
                "deleted": 0
            }
            
            # Process working memories
            for memory_id, memory in list(self.working_memories.items()):
                if self.ebbinghaus_algorithm.should_promote(memory):
                    # Promote to short-term
                    self.short_term_memories[memory_id] = memory
                    del self.working_memories[memory_id]
                    optimization_results["working_to_short"] += 1
            
            # Process short-term memories
            for memory_id, memory in list(self.short_term_memories.items()):
                if self.ebbinghaus_algorithm.should_promote(memory):
                    # Promote to long-term
                    self.long_term_memories[memory_id] = memory
                    del self.short_term_memories[memory_id]
                    optimization_results["short_to_long"] += 1
                elif self.ebbinghaus_algorithm.should_forget(memory):
                    # Delete forgotten memory
                    del self.short_term_memories[memory_id]
                    optimization_results["deleted"] += 1
            
            # Process long-term memories
            for memory_id, memory in list(self.long_term_memories.items()):
                if self.ebbinghaus_algorithm.should_archive(memory):
                    # Archive old memory
                    del self.long_term_memories[memory_id]
                    optimization_results["long_to_archive"] += 1
            
            logger.info(f"Memory optimization completed: {optimization_results}")
            
            return optimization_results
            
        except Exception as e:
            logger.error(f"Failed to optimize memories: {e}")
            return {}
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get memory statistics.
        
        Returns:
            Memory statistics
        """
        return {
            "working_memories": len(self.working_memories),
            "short_term_memories": len(self.short_term_memories),
            "long_term_memories": len(self.long_term_memories),
            "total_memories": (
                len(self.working_memories) +
                len(self.short_term_memories) +
                len(self.long_term_memories)
            )
        }
