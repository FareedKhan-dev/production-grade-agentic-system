"""LLM service for managing LLM calls with retries and fallback mechanisms."""

import logging
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAIError,
    RateLimitError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config.settings import (
    Environment,
    settings,
)
from src.system.logs import logger

# Errors worth retrying on the same model: the request may succeed a few seconds later.
# Anything else (a bad request, a missing model, a wrong key) goes straight to the next model.
RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


class LLMRegistry:
    """Registry of available LLM models with pre-initialized instances.

    This class maintains a list of LLM configurations and provides
    methods to retrieve them by name with optional argument overrides.
    """

    # Class-level variable containing all available LLM models
    LLMS: List[Dict[str, Any]] = [
        {
            "name": "gpt-5-mini",
            "llm": ChatOpenAI(
                model="gpt-5-mini",
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                max_tokens=settings.MAX_TOKENS,
                reasoning={"effort": "low"},
            ),
        },
        {
            "name": "gpt-5",
            "llm": ChatOpenAI(
                model="gpt-5",
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                max_tokens=settings.MAX_TOKENS,
                reasoning={"effort": "medium"},
            ),
        },
        {
            "name": "gpt-5-nano",
            "llm": ChatOpenAI(
                model="gpt-5-nano",
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                max_tokens=settings.MAX_TOKENS,
                reasoning={"effort": "minimal"},
            ),
        },
        {
            "name": "gpt-4o",
            "llm": ChatOpenAI(
                model="gpt-4o",
                temperature=settings.DEFAULT_LLM_TEMPERATURE,
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                max_tokens=settings.MAX_TOKENS,
                top_p=0.95 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.8,
                presence_penalty=0.1 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.0,
                frequency_penalty=0.1 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.0,
            ),
        },
        {
            "name": "gpt-4o-mini",
            "llm": ChatOpenAI(
                model="gpt-4o-mini",
                temperature=settings.DEFAULT_LLM_TEMPERATURE,
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                max_tokens=settings.MAX_TOKENS,
                top_p=0.9 if settings.ENVIRONMENT == Environment.PRODUCTION else 0.8,
            ),
        },
    ]

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides.

        A name that is not in LLMS gets a plain ChatOpenAI instance, so any model served by
        the configured endpoint (OPENAI_BASE_URL) can be used.

        Args:
            model_name: Name of the model to retrieve
            **kwargs: Optional arguments to override default model configuration

        Returns:
            BaseChatModel instance
        """
        model_entry = next((entry for entry in cls.LLMS if entry["name"] == model_name), None)

        if model_entry is None or kwargs:
            logger.debug("creating_llm_instance", model_name=model_name, custom_args=list(kwargs.keys()))
            options = {"temperature": settings.DEFAULT_LLM_TEMPERATURE, "max_tokens": settings.MAX_TOKENS, **kwargs}
            return ChatOpenAI(
                model=model_name, api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_BASE_URL, **options
            )

        # Return the default instance
        logger.debug("using_default_llm_instance", model_name=model_name)
        return model_entry["llm"]

    @classmethod
    def get_all_names(cls) -> List[str]:
        """Get all registered LLM names in order.

        Returns:
            List of LLM names
        """
        return [entry["name"] for entry in cls.LLMS]


class LLMService:
    """Service for managing LLM calls with retries and circular fallback.

    This service handles all LLM interactions with automatic retry logic,
    rate limit handling, and circular fallback through the configured models:
    DEFAULT_LLM_MODEL first, then LLM_FALLBACK_MODELS (by default the registry).
    """

    def __init__(self):
        """Initialize the LLM service."""
        fallback = settings.LLM_FALLBACK_MODELS
        if fallback is None:
            fallback = LLMRegistry.get_all_names()
        # The default model first, then each fallback once
        self._model_names: List[str] = list(dict.fromkeys([settings.DEFAULT_LLM_MODEL, *fallback]))
        self._current_model_index: int = 0
        self._tools: List = []
        self._llm: Optional[BaseChatModel] = LLMRegistry.get(self._model_names[0])

        logger.info(
            "llm_service_initialized",
            default_model=settings.DEFAULT_LLM_MODEL,
            models=self._model_names,
            environment=settings.ENVIRONMENT.value,
        )

    @property
    def model_name(self) -> str:
        """The name of the model the next call will use."""
        return self._model_names[self._current_model_index]

    def _use_model(self, index: int, **model_kwargs) -> None:
        """Make the model at `index` the current one, with the agent's tools bound to it."""
        llm = LLMRegistry.get(self._model_names[index], **model_kwargs)
        self._llm = llm.bind_tools(self._tools) if self._tools else llm
        self._current_model_index = index

    def _get_next_model_index(self) -> int:
        """Get the next model index in circular fashion.

        Returns:
            Next model index (wraps around to 0 if at end)
        """
        return (self._current_model_index + 1) % len(self._model_names)

    def _switch_to_next_model(self) -> bool:
        """Switch to the next model in the list (circular), keeping the tools bound.

        Returns:
            True if successfully switched, False otherwise
        """
        try:
            next_index = self._get_next_model_index()

            logger.warning(
                "switching_to_next_model",
                from_model=self.model_name,
                to_model=self._model_names[next_index],
            )

            self._use_model(next_index)

            logger.info("model_switched", new_model=self.model_name, new_index=next_index)
            return True
        except Exception as e:
            logger.error("model_switch_failed", error=str(e))
            return False

    @retry(
        stop=stop_after_attempt(settings.MAX_LLM_CALL_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(RETRYABLE_ERRORS),
        # The level must be the number: the logger looks it up by number, and the string
        # "WARNING" raised KeyError at the first retry instead of retrying
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _call_llm_with_retry(self, messages: List[BaseMessage]) -> BaseMessage:
        """Call the LLM with automatic retry logic.

        Args:
            messages: List of messages to send to the LLM

        Returns:
            BaseMessage response from the LLM

        Raises:
            OpenAIError: If all retries fail
        """
        if not self._llm:
            raise RuntimeError("llm not initialized")

        try:
            response = await self._llm.ainvoke(messages)
            logger.debug("llm_call_successful", message_count=len(messages))
            return response
        except RETRYABLE_ERRORS as e:
            logger.warning(
                "llm_call_failed_retrying",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise
        except OpenAIError as e:
            logger.error(
                "llm_call_failed",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

    async def call(
        self,
        messages: List[BaseMessage],
        model_name: Optional[str] = None,
        **model_kwargs,
    ) -> BaseMessage:
        """Call the LLM with the specified messages and circular fallback.

        Args:
            messages: List of messages to send to the LLM
            model_name: Optional specific model to use. If None, uses current model.
            **model_kwargs: Optional kwargs to override default model configuration

        Returns:
            BaseMessage response from the LLM

        Raises:
            RuntimeError: If all models fail after retries
        """
        # If the caller asks for a specific model, make it the current one
        if model_name:
            if model_name not in self._model_names:
                self._model_names.append(model_name)
            self._use_model(self._model_names.index(model_name), **model_kwargs)
            logger.info("using_requested_model", model_name=model_name, has_custom_kwargs=bool(model_kwargs))

        # Track which models we've tried to prevent infinite loops
        total_models = len(self._model_names)
        models_tried = 0
        starting_model = self.model_name
        last_error = None

        while models_tried < total_models:
            try:
                return await self._call_llm_with_retry(messages)
            except OpenAIError as e:
                last_error = e
                models_tried += 1

                logger.error(
                    "llm_call_failed_after_retries",
                    model=self.model_name,
                    models_tried=models_tried,
                    total_models=total_models,
                    error=str(e),
                )

                # If we've tried all models, give up
                if models_tried >= total_models:
                    logger.error("all_models_failed", models_tried=models_tried, starting_model=starting_model)
                    break

                # Switch to next model in circular fashion
                if not self._switch_to_next_model():
                    logger.error("failed_to_switch_to_next_model")
                    break

                # Continue loop to try next model

        # All models failed
        raise RuntimeError(
            f"failed to get response from llm after trying {models_tried} models. last error: {str(last_error)}"
        )

    def get_llm(self) -> Optional[BaseChatModel]:
        """Get the current LLM instance.

        Returns:
            Current BaseChatModel instance or None if not initialized
        """
        return self._llm

    def bind_tools(self, tools: List) -> "LLMService":
        """Bind tools to the current LLM, and to every model switched to later.

        Args:
            tools: List of tools to bind

        Returns:
            Self for method chaining
        """
        self._tools = list(tools)
        self._use_model(self._current_model_index)
        logger.debug("tools_bound_to_llm", tool_count=len(self._tools))
        return self


# Create global LLM service instance
llm_service = LLMService()
