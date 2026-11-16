"""Chatbot API endpoints for handling chat interactions.

This module provides endpoints for chat interactions, including regular chat,
streaming chat, message history management, and chat history clearing.
"""

import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse

from src.agent.workflow import LangGraphAgent
from src.config.settings import settings
from src.data.models.session import Session
from src.data.schemas.chat import (
    ChatRequest,
    ChatResponse,
    StreamResponse,
)
from src.interface.auth import get_current_session
from src.system.logs import logger
from src.system.rate_limit import limiter
from src.system.telemetry import llm_stream_duration_seconds

router = APIRouter()
agent = LangGraphAgent()

# What clients see when something fails. The details (which can include provider error
# messages, hostnames or partial keys) go to the logs only.
CHAT_FAILED = "The assistant could not answer. Please try again later."


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request using LangGraph.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        ChatResponse: The processed chat response.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    try:
        logger.info(
            "chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        result = await agent.get_response(chat_request.messages, session.id, user_id=session.user_id)

        logger.info("chat_request_processed", session_id=session.id)

        return ChatResponse(messages=result)
    except Exception as e:
        logger.error("chat_request_failed", session_id=session.id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=CHAT_FAILED)


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request using LangGraph with streaming response.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        StreamingResponse: A streaming response of the chat completion.
    """
    logger.info(
        "stream_chat_request_received",
        session_id=session.id,
        message_count=len(chat_request.messages),
    )

    async def event_generator():
        """Generate streaming events.

        Yields:
            str: Server-sent events in JSON format.
        """
        try:
            with llm_stream_duration_seconds.labels(model=agent.llm_service.model_name).time():
                async for chunk in agent.get_stream_response(
                    chat_request.messages, session.id, user_id=session.user_id
                ):
                    response = StreamResponse(content=chunk, done=False)
                    yield f"data: {json.dumps(response.model_dump())}\n\n"

            # Send final message indicating completion
            final_response = StreamResponse(content="", done=True)
            yield f"data: {json.dumps(final_response.model_dump())}\n\n"

        except Exception as e:
            logger.error(
                "stream_chat_request_failed",
                session_id=session.id,
                error=str(e),
                exc_info=True,
            )
            error_response = StreamResponse(content="", done=True, error=CHAT_FAILED)
            yield f"data: {json.dumps(error_response.model_dump())}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        # Proxies such as nginx buffer responses by default, which would hold the tokens back
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/messages", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_session_messages(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Get all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        ChatResponse: All messages in the session.

    Raises:
        HTTPException: If there's an error retrieving the messages.
    """
    try:
        messages = await agent.get_chat_history(session.id)
        return ChatResponse(messages=messages)
    except Exception as e:
        logger.error("get_messages_failed", session_id=session.id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Could not load the messages. Please try again later.")


@router.delete("/messages")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_chat_history(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Clear all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        dict: A message indicating the chat history was cleared.
    """
    try:
        await agent.clear_chat_history(session.id)
        return {"message": "Chat history cleared successfully"}
    except Exception as e:
        logger.error("clear_chat_history_failed", session_id=session.id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Could not clear the messages. Please try again later.")
