from typing import Optional

from powermem.integrations.llm.config.base import BaseLLMConfig


class QwenASRConfig(BaseLLMConfig):
    """
    Configuration class for Qwen ASR-specific parameters.
    Inherits from BaseLLMConfig and adds ASR-specific settings.
    """

    def __init__(
            self,
            # Base parameters (only model and api_key are used for ASR)
            model: Optional[str] = None,
            api_key: Optional[str] = None,
            # ASR-specific parameters
            dashscope_base_url: Optional[str] = None,
            asr_options: Optional[dict] = None,
            result_format: str = "message",
    ):
        """
        Initialize Qwen ASR configuration.

        Args:
            model: Qwen ASR model to use, defaults to "qwen3-asr-flash"
            api_key: DashScope API key, defaults to None
            dashscope_base_url: DashScope API base URL, defaults to None
            asr_options: ASR-specific options (e.g., language, enable_itn), defaults to {"enable_itn": True}
            result_format: Result format for ASR response, defaults to "message"
        """
        # Initialize base parameters with defaults (ASR doesn't use these parameters)
        super().__init__(
            model=model,
            api_key=api_key,
        )

        # ASR-specific parameters
        self.dashscope_base_url = dashscope_base_url
        # Default asr_options with enable_itn enabled
        if asr_options is None:
            self.asr_options = {"enable_itn": True}
        else:
            self.asr_options = asr_options
        self.result_format = result_format

