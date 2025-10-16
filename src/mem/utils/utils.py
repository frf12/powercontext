"""
Utility functions and classes

This module provides utility functions and helper classes.
"""

import logging
from typing import Any, Dict, List, Optional
import json
import hashlib
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_memory_id(content: str, user_id: Optional[str] = None) -> str:
    """
    Generate a unique memory ID based on content and user.
    
    Args:
        content: Memory content
        user_id: User ID
        
    Returns:
        Unique memory ID
    """
    data = f"{content}:{user_id}:{datetime.utcnow().isoformat()}"
    return hashlib.md5(data.encode()).hexdigest()


def validate_memory_data(data: Dict[str, Any]) -> bool:
    """
    Validate memory data structure.
    
    Args:
        data: Memory data to validate
        
    Returns:
        True if valid, False otherwise
    """
    required_fields = ["content"]
    
    for field in required_fields:
        if field not in data:
            logger.error(f"Missing required field: {field}")
            return False
    
    if not isinstance(data["content"], str) or not data["content"].strip():
        logger.error("Content must be a non-empty string")
        return False
    
    return True


def sanitize_content(content: str) -> str:
    """
    Sanitize memory content.
    
    Args:
        content: Content to sanitize
        
    Returns:
        Sanitized content
    """
    # Remove excessive whitespace
    content = " ".join(content.split())
    
    # Remove control characters
    content = "".join(char for char in content if ord(char) >= 32 or char in "\n\t")
    
    return content.strip()


def format_memory_for_display(memory: Dict[str, Any]) -> str:
    """
    Format memory for display.
    
    Args:
        memory: Memory data
        
    Returns:
        Formatted memory string
    """
    content = memory.get("content", "")
    created_at = memory.get("created_at", "")
    metadata = memory.get("metadata", {})
    
    formatted = f"Content: {content}\n"
    if created_at:
        formatted += f"Created: {created_at}\n"
    if metadata:
        formatted += f"Metadata: {json.dumps(metadata, indent=2)}\n"
    
    return formatted


def merge_memories(memories: List[Dict[str, Any]]) -> str:
    """
    Merge multiple memories into a single string.
    
    Args:
        memories: List of memory data
        
    Returns:
        Merged memory content
    """
    if not memories:
        return ""
    
    merged_content = []
    for memory in memories:
        content = memory.get("content", "")
        if content:
            merged_content.append(content)
    
    return "\n\n".join(merged_content)


def calculate_similarity(text1: str, text2: str) -> float:
    """
    Calculate similarity between two texts.
    
    Args:
        text1: First text
        text2: Second text
        
    Returns:
        Similarity score between 0 and 1
    """
    # Simple word-based similarity
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 and not words2:
        return 1.0
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union)


def extract_keywords(text: str, max_keywords: int = 10) -> List[str]:
    """
    Extract keywords from text.
    
    Args:
        text: Text to extract keywords from
        max_keywords: Maximum number of keywords
        
    Returns:
        List of keywords
    """
    # Simple keyword extraction
    words = text.lower().split()
    
    # Remove common stop words
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "is", "are", "was", "were", "be", "been", "have",
        "has", "had", "do", "does", "did", "will", "would", "could", "should"
    }
    
    keywords = [word for word in words if word not in stop_words and len(word) > 2]
    
    # Count frequency
    word_count = {}
    for word in keywords:
        word_count[word] = word_count.get(word, 0) + 1
    
    # Sort by frequency
    sorted_keywords = sorted(word_count.items(), key=lambda x: x[1], reverse=True)
    
    return [word for word, count in sorted_keywords[:max_keywords]]


def format_timestamp(timestamp: datetime) -> str:
    """
    Format timestamp for display.
    
    Args:
        timestamp: Timestamp to format
        
    Returns:
        Formatted timestamp string
    """
    return timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    Parse timestamp string.
    
    Args:
        timestamp_str: Timestamp string to parse
        
    Returns:
        Parsed datetime object or None if invalid
    """
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        logger.error(f"Failed to parse timestamp: {timestamp_str}")
        return None
