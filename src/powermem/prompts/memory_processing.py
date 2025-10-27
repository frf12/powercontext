"""
Memory processing prompts

This module provides prompts for memory processing operations.
"""

import logging
from typing import Dict, Any, Optional
from .templates import PromptTemplates

logger = logging.getLogger(__name__)


class MemoryProcessingPrompts(PromptTemplates):
    """
    Prompts for memory processing operations.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize memory processing prompts.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self._load_memory_processing_templates()
    
    def _load_memory_processing_templates(self) -> None:
        """Load memory processing specific templates."""
        processing_templates = {
            "system": {
                "memory_processor": """You are a memory processing specialist. Your task is to:
                1. Analyze and categorize memories
                2. Determine importance and relevance
                3. Suggest memory organization strategies
                4. Identify patterns and connections
                5. Recommend retention policies
                
                Always prioritize user privacy and data security. Focus on practical, actionable insights."""
            },
            
            "user": {
                "process_memory": """Process the following memory for storage:
                
                Memory: {memory}
                Context: {context}
                Metadata: {metadata}
                
                Please analyze and provide:
                1. Importance level (low, medium, high, critical)
                2. Suggested categorization
                3. Key topics or themes
                4. Recommended retention duration
                5. Privacy considerations
                
                Return in JSON format:
                {{
                    "importance": "low|medium|high|critical",
                    "category": "personal|work|preference|fact|goal|other",
                    "topics": ["topic1", "topic2"],
                    "retention_days": number,
                    "privacy_level": "public|private|confidential",
                    "tags": ["tag1", "tag2"],
                    "summary": "brief summary"
                }}""",
                
                "categorize_memory": """Categorize the following memory:
                
                Memory: {memory}
                
                Assign it to the most appropriate category and provide reasoning:
                - personal: Personal information, preferences, experiences
                - work: Professional information, tasks, projects
                - preference: Likes, dislikes, choices, opinions
                - fact: Verifiable information, data, statistics
                - goal: Objectives, plans, aspirations
                - relationship: Information about people and connections
                - other: Anything that doesn't fit the above categories
                
                Return in JSON format:
                {{
                    "category": "category_name",
                    "confidence": 0.0-1.0,
                    "reasoning": "explanation of categorization",
                    "subcategory": "more specific subcategory if applicable"
                }}""",
                
                "evaluate_importance": """Evaluate the importance of the following memory:
                
                Memory: {memory}
                Context: {context}
                
                Consider:
                - Personal significance to the user
                - Practical utility
                - Emotional impact
                - Frequency of potential use
                - Uniqueness of information
                
                Return in JSON format:
                {{
                    "importance": "low|medium|high|critical",
                    "score": 0.0-1.0,
                    "factors": {{
                        "personal_significance": 0.0-1.0,
                        "practical_utility": 0.0-1.0,
                        "emotional_impact": 0.0-1.0,
                        "frequency_of_use": 0.0-1.0,
                        "uniqueness": 0.0-1.0
                    }},
                    "reasoning": "explanation of importance assessment"
                }}""",
                
                "suggest_retention": """Suggest retention policy for the following memory:
                
                Memory: {memory}
                Importance: {importance}
                Category: {category}
                
                Consider:
                - Type of information
                - Potential future value
                - Privacy implications
                - Storage costs
                - User preferences
                
                Return in JSON format:
                {{
                    "retention_days": number,
                    "retention_policy": "temporary|short_term|long_term|permanent",
                    "review_frequency": "daily|weekly|monthly|quarterly|yearly",
                    "auto_delete": true|false,
                    "reasoning": "explanation of retention recommendation"
                }}"""
            }
        }
        
        # Add memory processing templates to existing templates
        for category, templates in processing_templates.items():
            if category not in self.templates:
                self.templates[category] = {}
            self.templates[category].update(templates)
    
    def get_memory_processing_prompt(self, memory: str, context: str = "", metadata: Dict[str, Any] = None) -> str:
        """
        Get memory processing prompt.
        
        Args:
            memory: Memory content to process
            context: Additional context
            metadata: Memory metadata
            
        Returns:
            Formatted prompt
        """
        return self.format_template(
            "user", 
            "process_memory", 
            memory=memory, 
            context=context, 
            metadata=metadata or {}
        )
    
    def get_categorization_prompt(self, memory: str) -> str:
        """
        Get memory categorization prompt.
        
        Args:
            memory: Memory content to categorize
            
        Returns:
            Formatted prompt
        """
        return self.format_template("user", "categorize_memory", memory=memory)
    
    def get_importance_evaluation_prompt(self, memory: str, context: str = "") -> str:
        """
        Get importance evaluation prompt.
        
        Args:
            memory: Memory content to evaluate
            context: Additional context
            
        Returns:
            Formatted prompt
        """
        return self.format_template("user", "evaluate_importance", memory=memory, context=context)
    
    def get_retention_suggestion_prompt(self, memory: str, importance: str, category: str) -> str:
        """
        Get retention suggestion prompt.
        
        Args:
            memory: Memory content
            importance: Importance level
            category: Memory category
            
        Returns:
            Formatted prompt
        """
        return self.format_template(
            "user", 
            "suggest_retention", 
            memory=memory, 
            importance=importance, 
            category=category
        )
    
    def get_system_prompt(self) -> str:
        """
        Get system prompt for memory processing.
        
        Returns:
            System prompt
        """
        return self.get_template("system", "memory_processor")
