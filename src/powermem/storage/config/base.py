from abc import ABC
from typing import Any, Dict

from pydantic import BaseModel, model_validator


class BaseVectorStoreConfig(BaseModel, ABC):
    """
    Base configuration class for all vector store providers.
    
    This class provides common validation logic that is shared
    across all vector store implementations.
    """