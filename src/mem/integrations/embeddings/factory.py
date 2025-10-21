"""
Embedding factory for creating embedding instances

This module provides a factory for creating different embedding instances.
"""

import importlib
from typing import Optional

from mem.integrations.embeddings.config.base import BaseEmbedderConfig
from mem.integrations.embeddings.mock import MockEmbeddings


def load_class(class_type):
    module_path, class_name = class_type.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class EmbedderFactory:
    provider_to_class = {
        "openai": "mem.integrations.embeddings.openai.OpenAIEmbedding",
        "ollama": "mem.integrations.embeddings.ollama.OllamaEmbedding",
        "huggingface": "mem.integrations.embeddings.huggingface.HuggingFaceEmbedding",
        "azure_openai": "mem.integrations.embeddings.azure_openai.AzureOpenAIEmbedding",
        "gemini": "mem.integrations.embeddings.gemini.GoogleGenAIEmbedding",
        "vertexai": "mem.integrations.embeddings.vertexai.VertexAIEmbedding",
        "together": "mem.integrations.embeddings.together.TogetherEmbedding",
        "lmstudio": "mem.integrations.embeddings.lmstudio.LMStudioEmbedding",
        "langchain": "mem.integrations.embeddings.langchain.LangchainEmbedding",
        "aws_bedrock": "mem.integrations.embeddings.aws_bedrock.AWSBedrockEmbedding",
        "qwen": "mem.integrations.embeddings.qwen.QwenEmbedding",
    }

    @classmethod
    def create(cls, provider_name, config, vector_config: Optional[dict]):
        if provider_name == "upstash_vector" and vector_config and vector_config.enable_embeddings:
            return MockEmbeddings()
        class_type = cls.provider_to_class.get(provider_name)
        if class_type:
            embedder_instance = load_class(class_type)
            base_config = BaseEmbedderConfig(**config)
            return embedder_instance(base_config)
        else:
            raise ValueError(f"Unsupported Embedder provider: {provider_name}")
