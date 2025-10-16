"""
Fact extraction prompts

This module provides prompts for fact extraction from text.
"""

import logging
from typing import Dict, Any, Optional
from .templates import PromptTemplates

logger = logging.getLogger(__name__)


class FactExtractionPrompts(PromptTemplates):
    """
    Prompts for fact extraction operations.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize fact extraction prompts.
        
        Args:
            config: Configuration dictionary
        """
        super().__init__(config)
        self._load_fact_extraction_templates()
    
    def _load_fact_extraction_templates(self) -> None:
        """Load fact extraction specific templates."""
        fact_templates = {
            "system": {
                "fact_extractor": """You are a fact extraction specialist. Your task is to identify and extract 
                important facts, preferences, and information from user input. Focus on:
                - Personal preferences and opinions
                - Important facts and data
                - Relationships and connections
                - Goals and intentions
                - Contextual information
                
                Extract only factual, verifiable information. Avoid speculation or assumptions."""
            },
            
            "user": {
                "extract_facts": """Extract important facts and information from the following text:
                
                Text: {text}
                
                Please identify and extract:
                1. Key facts and information
                2. Personal preferences or opinions
                3. Important relationships or connections
                4. Goals or intentions
                5. Contextual information
                
                Return the extracted information in JSON format with the following structure:
                {{
                    "facts": [
                        {{
                            "fact": "extracted fact",
                            "type": "preference|fact|relationship|goal|context",
                            "importance": "low|medium|high",
                            "confidence": 0.0-1.0
                        }}
                    ],
                    "summary": "brief summary of extracted information"
                }}""",
                
                "extract_preferences": """Extract personal preferences from the following text:
                
                Text: {text}
                
                Focus on:
                - Likes and dislikes
                - Preferences for products, services, or activities
                - Personal opinions and viewpoints
                - Behavioral patterns
                
                Return in JSON format:
                {{
                    "preferences": [
                        {{
                            "preference": "description of preference",
                            "category": "food|entertainment|lifestyle|work|other",
                            "strength": "weak|moderate|strong",
                            "context": "additional context if available"
                        }}
                    ]
                }}""",
                
                "extract_relationships": """Extract relationship information from the following text:
                
                Text: {text}
                
                Focus on:
                - People mentioned and their relationships
                - Professional connections
                - Family relationships
                - Social connections
                
                Return in JSON format:
                {{
                    "relationships": [
                        {{
                            "person": "name or identifier",
                            "relationship": "type of relationship",
                            "context": "additional context",
                            "importance": "low|medium|high"
                        }}
                    ]
                }}"""
            }
        }
        
        # Add fact extraction templates to existing templates
        for category, templates in fact_templates.items():
            if category not in self.templates:
                self.templates[category] = {}
            self.templates[category].update(templates)
    
    def get_fact_extraction_prompt(self, text: str) -> str:
        """
        Get fact extraction prompt for given text.
        
        Args:
            text: Text to extract facts from
            
        Returns:
            Formatted prompt
        """
        return self.format_template("user", "extract_facts", text=text)
    
    def get_preference_extraction_prompt(self, text: str) -> str:
        """
        Get preference extraction prompt for given text.
        
        Args:
            text: Text to extract preferences from
            
        Returns:
            Formatted prompt
        """
        return self.format_template("user", "extract_preferences", text=text)
    
    def get_relationship_extraction_prompt(self, text: str) -> str:
        """
        Get relationship extraction prompt for given text.
        
        Args:
            text: Text to extract relationships from
            
        Returns:
            Formatted prompt
        """
        return self.format_template("user", "extract_relationships", text=text)
    
    def get_system_prompt(self) -> str:
        """
        Get system prompt for fact extraction.
        
        Returns:
            System prompt
        """
        return self.get_template("system", "fact_extractor")
