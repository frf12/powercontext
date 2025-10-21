"""
Storage configuration management

This module handles storage configuration and validation.
"""

from typing import Dict, Optional, Union

from pydantic import BaseModel, Field, model_validator, field_validator

from mem.integrations.llm.configs import LLMConfig
from mem.storage.config.oceanbase import OceanBaseGraphConfig


class VectorStorageConfig(BaseModel):
    provider: str = Field(
        description="Provider of the vector store (e.g., 'oceanbase', 'pgvector')",
        default="oceanbase",
    )
    config: Optional[Dict] = Field(description="Configuration for the specific vector store", default=None)

    _provider_configs: Dict[str, str] = {
        "oceanbase": "OceanBaseConfig",
        "pgvector": "PGVectorConfig",
    }

    @model_validator(mode="after")
    def validate_and_create_config(self) -> "VectorStorageConfig":
        provider = self.provider
        config = self.config

        if provider not in self._provider_configs:
            raise ValueError(f"Unsupported vector store provider: {provider}")

        module = __import__(
            f"mem.storage.config.{provider}",
            fromlist=[self._provider_configs[provider]],
        )
        config_class = getattr(module, self._provider_configs[provider])

        if config is None:
            config = {}

        if not isinstance(config, dict):
            if not isinstance(config, config_class):
                raise ValueError(f"Invalid config type for provider {provider}")
            return self

        # also check if path in allowed kays for pydantic model, and whether config extra fields are allowed
        if "path" not in config and "path" in config_class.__annotations__:
            config["path"] = f"/tmp/{provider}"

        self.config = config_class(**config)
        return self

class GraphStoreConfig(BaseModel):
    provider: str = Field(
        description="Provider of the data store (e.g., 'oceanbase')",
        default="oceanbase",
    )
    config: Union[OceanBaseGraphConfig] = Field(
        description="Configuration for the specific data store", default=None
    )
    llm: Optional[LLMConfig] = Field(description="LLM configuration for querying the graph store", default=None)
    custom_prompt: Optional[str] = Field(
        description="Custom prompt to fetch entities from the given text", default=None
    )

    @field_validator("config")
    def validate_config(cls, v, values):
        provider = values.data.get("provider")
        if provider == "oceanbase":
            return OceanBaseGraphConfig(**v.model_dump())
        else:
            raise ValueError(f"Unsupported graph store provider: {provider}")