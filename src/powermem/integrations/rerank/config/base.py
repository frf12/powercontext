"""
Base configuration for rerank models
"""
from typing import Optional


class BaseRerankConfig:
    """Base configuration for rerank models
    
    Args:
        model (str): The rerank model to use
        api_key (Optional[str]): API key for the rerank service
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs,
    ):
        self.model = model
        self.api_key = api_key
        
        # Store any additional kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

