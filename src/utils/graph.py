"""This file contains the graph utilities for the application."""

from typing import Any

from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
)
from langchain_core.messages import trim_messages as _trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from src.config.settings import settings
from src.system.logs import logger


def dump_messages(messages: list) -> list[dict]:
    """Dump the messages to a list of dictionaries.

    Args:
        messages (list): The messages to dump (API Message models or LangChain messages).

    Returns:
        list[dict]: The dumped messages.
    """
    return [message.model_dump() for message in messages]


def message_text(content: Any) -> str:
    """Return the text of a message's content.

    Most models return a string. Some (GPT-5 models through the Responses API, for one)
    return a list of content blocks like:
    [
        {'id': '...', 'summary': [], 'type': 'reasoning'},
        {'type': 'text', 'text': 'actual response'}
    ]
    Only the text blocks are meant for the user.

    Args:
        content: A message's content, or a streamed chunk's.

    Returns:
        str: The text.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def process_llm_response(response: BaseMessage) -> BaseMessage:
    """Process LLM response to handle structured content blocks (e.g., from GPT-5 models).

    Args:
        response: The raw response from the LLM

    Returns:
        BaseMessage with processed content
    """
    if isinstance(response.content, list):
        for block in response.content:
            # Log reasoning blocks for debugging
            if isinstance(block, dict) and block.get("type") == "reasoning":
                logger.debug(
                    "reasoning_block_received",
                    reasoning_id=block.get("id"),
                    has_summary=bool(block.get("summary")),
                )

        block_count = len(response.content)
        response.content = message_text(response.content)
        logger.debug("processed_structured_content", block_count=block_count, extracted_length=len(response.content))

    return response


def prepare_messages(messages: list, system_prompt: str) -> list:
    """Prepare the messages for the LLM: trim the history and put the system prompt first.

    The history is cut to MAX_CONTEXT_TOKENS, keeping the latest messages and starting on a
    user message. Tokens are estimated (about four characters each) instead of counted with a
    model's tokenizer, so this works the same for every model, downloads nothing, and does
    not fail on content blocks a tokenizer does not know.

    Args:
        messages (list): The conversation so far.
        system_prompt (str): The system prompt to use.

    Returns:
        list: The prepared messages.
    """
    trimmed_messages = _trim_messages(
        dump_messages(messages),
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=settings.MAX_CONTEXT_TOKENS,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )

    return [SystemMessage(content=system_prompt)] + trimmed_messages
