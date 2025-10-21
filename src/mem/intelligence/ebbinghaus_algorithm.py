"""
Ebbinghaus forgetting curve algorithm

This module implements the Ebbinghaus forgetting curve for memory management.
"""

import logging
import math
from typing import Any, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class EbbinghausAlgorithm:
    """
    Implements Ebbinghaus forgetting curve algorithm for memory management.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Ebbinghaus algorithm.
        
        Args:
            config: Algorithm configuration
        """
        self.config = config
        
        # Ebbinghaus curve parameters
        self.initial_retention = config.get("initial_retention", 1.0)
        self.decay_rate = config.get("decay_rate", 0.1)
        self.reinforcement_factor = config.get("reinforcement_factor", 0.3)
        
        # Memory type thresholds
        self.working_threshold = config.get("working_threshold", 0.3)
        self.short_term_threshold = config.get("short_term_threshold", 0.6)
        self.long_term_threshold = config.get("long_term_threshold", 0.8)
        
        # Time intervals (in hours)
        self.review_intervals = config.get("review_intervals", [1, 6, 24, 72, 168])
        
        logger.info("EbbinghausAlgorithm initialized")
    
    def process_memory(
        self,
        content: str,
        importance_score: float,
        memory_type: str
    ) -> str:
        """
        Process memory using Ebbinghaus algorithm.
        
        Args:
            content: Memory content
            importance_score: Importance score
            memory_type: Type of memory
            
        Returns:
            Processed content
        """
        try:
            # Apply importance-based processing
            processed_content = self._apply_importance_processing(content, importance_score)
            
            # Apply memory type-specific processing
            processed_content = self._apply_memory_type_processing(processed_content, memory_type)
            
            logger.debug(f"Processed memory with type: {memory_type}, importance: {importance_score}")
            
            return processed_content
            
        except Exception as e:
            logger.error(f"Failed to process memory: {e}")
            return content
    
    def calculate_decay(self, created_at) -> float:
        """
        Calculate decay factor based on time elapsed.
        
        Args:
            created_at: When the memory was created (datetime object or ISO string)
            
        Returns:
            Decay factor between 0 and 1
        """
        try:
            # Handle both datetime objects and ISO string formats
            if isinstance(created_at, str):
                if created_at:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    # If empty string, use current time
                    created_at = datetime.utcnow()
            elif created_at is None:
                # If None, use current time
                created_at = datetime.utcnow()
            
            time_elapsed = datetime.utcnow() - created_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            
            # Ebbinghaus forgetting curve: R = e^(-t/S)
            # where R is retention, t is time, S is strength
            decay_factor = math.exp(-hours_elapsed / (24 * self.decay_rate))
            
            return max(decay_factor, 0.0)
            
        except Exception as e:
            logger.error(f"Failed to calculate decay: {e}")
            return 0.5
    
    def calculate_relevance(self, memory: Dict[str, Any], query: str) -> float:
        """
        Calculate relevance score for a memory given a query.
        
        Args:
            memory: Memory data
            query: Search query
            
        Returns:
            Relevance score between 0 and 1
        """
        try:
            content = memory.get("content", "").lower()
            query_lower = query.lower()
            
            # Simple keyword matching
            query_words = query_lower.split()
            content_words = content.split()
            
            matches = 0
            for word in query_words:
                if word in content_words:
                    matches += 1
            
            relevance_score = matches / len(query_words) if query_words else 0.0
            
            return min(relevance_score, 1.0)
            
        except Exception as e:
            logger.error(f"Failed to calculate relevance: {e}")
            return 0.0
    
    def should_promote(self, memory: Dict[str, Any]) -> bool:
        """
        Determine if a memory should be promoted to a higher tier.
        
        Args:
            memory: Memory data
            
        Returns:
            True if memory should be promoted
        """
        try:
            # Check access frequency
            access_count = memory.get("access_count", 0)
            if access_count >= 3:
                return True
            
            # Check recency
            created_at = memory.get("created_at")
            if created_at:
                time_elapsed = datetime.utcnow() - created_at
                if time_elapsed > timedelta(hours=24):
                    return True
            
            # Check importance
            importance = memory.get("importance_score", 0.5)
            if importance >= self.short_term_threshold:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check promotion: {e}")
            return False
    
    def should_forget(self, memory: Dict[str, Any]) -> bool:
        """
        Determine if a memory should be forgotten.
        
        Args:
            memory: Memory data
            
        Returns:
            True if memory should be forgotten
        """
        try:
            # Check decay factor
            created_at = memory.get("created_at")
            if created_at:
                decay_factor = self.calculate_decay(created_at)
                if decay_factor < self.working_threshold:
                    return True
            
            # Check access frequency
            access_count = memory.get("access_count", 0)
            if access_count == 0:
                # Check if memory is old enough to be forgotten
                if created_at:
                    time_elapsed = datetime.utcnow() - created_at
                    if time_elapsed > timedelta(days=7):
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check forgetting: {e}")
            return False
    
    def should_archive(self, memory: Dict[str, Any]) -> bool:
        """
        Determine if a memory should be archived.
        
        Args:
            memory: Memory data
            
        Returns:
            True if memory should be archived
        """
        try:
            # Check age
            created_at = memory.get("created_at")
            if created_at:
                time_elapsed = datetime.utcnow() - created_at
                if time_elapsed > timedelta(days=30):
                    return True
            
            # Check importance
            importance = memory.get("importance_score", 0.5)
            if importance < self.working_threshold:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check archiving: {e}")
            return False
    
    def get_review_schedule(self, memory: Dict[str, Any]) -> list:
        """
        Get review schedule for a memory based on Ebbinghaus curve.
        
        Args:
            memory: Memory data
            
        Returns:
            List of review times
        """
        try:
            created_at = memory.get("created_at", datetime.utcnow())
            importance = memory.get("importance_score", 0.5)
            
            # Adjust intervals based on importance
            adjusted_intervals = []
            for interval in self.review_intervals:
                # Higher importance = shorter intervals
                adjusted_interval = interval * (1 - importance * 0.5)
                adjusted_intervals.append(adjusted_interval)
            
            # Calculate review times
            review_times = []
            for interval in adjusted_intervals:
                review_time = created_at + timedelta(hours=interval)
                review_times.append(review_time)
            
            return review_times
            
        except Exception as e:
            logger.error(f"Failed to get review schedule: {e}")
            return []
    
    def _apply_importance_processing(self, content: str, importance_score: float) -> str:
        """Apply importance-based processing to content."""
        # For now, just return the content
        # In a real implementation, this might add importance markers
        return content
    
    def _apply_memory_type_processing(self, content: str, memory_type: str) -> str:
        """Apply memory type-specific processing to content."""
        # For now, just return the content
        # In a real implementation, this might add type-specific formatting
        return content
