import logging
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

class LLMFactory:
    """
    Factory class to dynamically load LLM providers based on model name prefixes.
    """

    @staticmethod
    def _determine_provider(model_name: str) -> str:
        """
        Determine the provider based on the model name prefix.
        """
        model_name_lower = model_name.lower()
        if model_name_lower.startswith("gpt"):
            return "openai"
        elif model_name_lower.startswith("claude"):
            return "anthropic"
        elif model_name_lower.startswith(("gemini", "google", "models/")):
            return "google_genai"
        elif model_name_lower.startswith(("grok", "xai")):
            return "xai"
        elif model_name_lower.startswith("openai/gpt-oss-120b"):
            return "groq"
        # Default to openai for unrecognized prefixes
        return "openai"

    @classmethod
    def get_llm(cls, model_name: str, **kwargs) -> BaseChatModel:
        """
        Initialize and return a chat model.

        Args:
            model_name: The name of the model (e.g., 'gpt-4o', 'claude-3-5-sonnet').
            **kwargs: Additional configuration parameters.
        """
        provider = cls._determine_provider(model_name)
        
        # Handle provider-specific arguments safely
        # init_chat_model will pass through relevant kwargs
        
        # For OpenAI, we often use 'use_responses_api'
        # For non-OpenAI, we should strip it to avoid errors if the underlying class doesn't support it
        if provider != "openai":
            kwargs.pop("use_responses_api", None)
            kwargs.pop("reasoning", None)

        logger.info(f"Initializing LLM: model={model_name}, provider={provider}")
        
        return init_chat_model(
            model=model_name,
            model_provider=provider,
            **kwargs
        )
    @classmethod
    def get_embeddings(cls, model_name: str = "text-embedding-3-small", **kwargs):
        """
        Initialize and return an embeddings model.
        """
        provider = cls._determine_provider(model_name)
        
        if provider == "openai":
            from langchain_openai import OpenAIEmbeddings
            logger.info(f"Initializing OpenAIEmbeddings: model={model_name}")
            return OpenAIEmbeddings(model=model_name, **kwargs)
        elif provider == "google_genai":
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            logger.info(f"Initializing GoogleGenerativeAIEmbeddings: model={model_name}")
            return GoogleGenerativeAIEmbeddings(model=model_name, **kwargs)
        
        # Default fallback
        from langchain_openai import OpenAIEmbeddings
        logger.warning(f"Unsupported embeddings provider for {model_name}, falling back to OpenAI.")
        return OpenAIEmbeddings(model="text-embedding-3-small", **kwargs)
