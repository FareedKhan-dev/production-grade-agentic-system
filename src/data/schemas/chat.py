"""This file contains the chat schema for the application."""

import re
from typing import (
    List,
    Literal,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)


class Message(BaseModel):
    """A message sent by the client.

    The server keeps the conversation (LangGraph's checkpointer), so a request only carries
    the new user turn. Accepting "system" or "assistant" here would let any client replace
    the system prompt or put words in the assistant's mouth.

    Attributes:
        role: Always "user".
        content: The content of the message.
    """

    model_config = {"extra": "ignore"}

    role: Literal["user"] = Field(..., description="The role of the message sender (only user messages are accepted)")
    content: str = Field(..., description="The content of the message", min_length=1, max_length=3000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate the message content.

        Args:
            v: The content to validate

        Returns:
            str: The validated content

        Raises:
            ValueError: If the content contains disallowed patterns
        """
        # Check for potentially harmful content
        if re.search(r"<script.*?>.*?</script>", v, re.IGNORECASE | re.DOTALL):
            raise ValueError("Content contains potentially harmful script tags")

        # Check for null bytes
        if "\0" in v:
            raise ValueError("Content contains null bytes")

        return v


class ChatMessage(BaseModel):
    """A message of the conversation as the API returns it.

    No length limit: the 3000 characters apply to what clients send, and the assistant's
    answers are often longer.

    Attributes:
        role: The role of the message sender (user or assistant).
        content: The content of the message.
    """

    role: Literal["user", "assistant"] = Field(..., description="The role of the message sender")
    content: str = Field(..., description="The content of the message")


class ChatRequest(BaseModel):
    """Request model for chat endpoint.

    Attributes:
        messages: List of messages in the conversation.
    """

    messages: List[Message] = Field(
        ...,
        description="List of messages in the conversation",
        min_length=1,
    )


class ChatResponse(BaseModel):
    """Response model for chat endpoint.

    Attributes:
        messages: List of messages in the conversation.
    """

    messages: List[ChatMessage] = Field(..., description="List of messages in the conversation")


class StreamResponse(BaseModel):
    """Response model for streaming chat endpoint.

    Attributes:
        content: The content of the current chunk.
        done: Whether the stream is complete.
        error: Set on the last event when the answer could not be completed.
    """

    content: str = Field(default="", description="The content of the current chunk")
    done: bool = Field(default=False, description="Whether the stream is complete")
    error: Optional[str] = Field(default=None, description="Why the stream ended early, if it did")
