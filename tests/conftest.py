"""Shared setup for the tests.

The tests run the real application (FastAPI, LangGraph, the Postgres checkpointer) against a
real PostgreSQL server, the pgvector image docker-compose.yml uses. Only the LLM, the web
search tool and long-term memory are replaced, so no API key or internet access is needed.

Point the tests at a database with POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER
and POSTGRES_PASSWORD. The defaults match the CI service: localhost:5432, agent_test,
postgres/postgres.
"""

import json
import os
import secrets
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any

# Settings are read once, at import time, so the environment must be ready before any
# application module is imported.
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "agent_test")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("OPENAI_API_KEY", "not-used-by-the-tests")
os.environ["LOG_DIR"] = tempfile.mkdtemp(prefix="agent-test-logs-")
# One model and no fallback: the tests replace it with a scripted one
os.environ["LLM_FALLBACK_MODELS"] = ""
# The default per-client limits (10 registrations an hour...) would stop the suite early
for endpoint in ("REGISTER", "LOGIN", "CHAT", "CHAT_STREAM", "MESSAGES", "ROOT", "HEALTH"):
    os.environ[f"RATE_LIMIT_{endpoint}"] = "100000 per minute"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    AIMessageChunk,
)
from langchain_core.outputs import (  # noqa: E402
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
)
from langchain_core.tools import tool  # noqa: E402
from pydantic import Field  # noqa: E402

# Passes the password rules, and contains every character html.escape changes
PASSWORD = "Str0ng&Pass\"'<>"


class ScriptedChatModel(BaseChatModel):
    """A chat model that answers from a script: each call takes the next item.

    An item is an AIMessage to return or an exception to raise. Streaming splits a text
    answer into small pieces, so the tests see what a real model's stream looks like.
    """

    script: list = Field(default_factory=list)
    received: list = Field(default_factory=list)
    bound_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = list(tools)
        return self

    def _next(self, messages) -> AIMessage:
        self.received.append(messages)
        if not self.script:
            raise AssertionError("the model was called more often than the test scripted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next(messages)
        if isinstance(message.content, str):
            text = message.content
            for start in range(0, len(text), 7):
                yield ChatGenerationChunk(message=AIMessageChunk(content=text[start : start + 7]))
        else:
            for index, block in enumerate(message.content):
                yield ChatGenerationChunk(message=AIMessageChunk(content=[{**block, "index": index}]))
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": i}
                        for i, call in enumerate(message.tool_calls)
                    ],
                )
            )


@pytest.fixture(scope="session")
def app():
    from src.main import app

    return app


@pytest.fixture(scope="session")
def agent():
    from src.interface.interaction import agent

    return agent


@pytest.fixture(scope="session")
def client(app, agent):
    # Long-term memory would call an embedding API; the tests check everything else
    patcher = pytest.MonkeyPatch()

    async def no_memory(user_id, query):
        return ""

    async def no_memory_update(user_id, messages, metadata=None):
        return None

    patcher.setattr(agent, "_get_relevant_memory", no_memory)
    patcher.setattr(agent, "_update_long_term_memory", no_memory_update)

    # One client, and so one event loop, for the whole run: the agent's connection pool
    # belongs to the loop it was created in.
    with TestClient(app) as test_client:
        yield test_client
    patcher.undo()


@pytest.fixture
def llm(monkeypatch, agent):
    """Replace the LLM with a scripted one; set `llm.script` before chatting."""
    from src.agent.tools import tools
    from src.services.llm_provider import LLMRegistry

    fake = ScriptedChatModel()
    monkeypatch.setattr(LLMRegistry, "get", classmethod(lambda cls, model_name, **kwargs: fake))
    agent.llm_service.bind_tools(tools)
    return fake


@pytest.fixture
def search(monkeypatch, agent):
    """Replace the web search tool with one that records its queries."""
    name = next(iter(agent.tools_by_name))
    calls = []
    result = "SEARCH RESULT: the population of Lisbon is 545,000"

    @tool(name)
    def fake_search(query: str) -> str:
        """Search the web."""
        calls.append(query)
        return result

    monkeypatch.setitem(agent.tools_by_name, name, fake_search)
    return SimpleNamespace(name=name, calls=calls, result=result)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str | None = None, password: str = PASSWORD) -> dict[str, Any]:
    """Register a new user; return the response with the email added."""
    email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {**response.json(), "email": email}


def new_session(client) -> dict[str, Any]:
    """Register a user and open a chat session; return the session with its auth header."""
    user = register(client)
    response = client.post("/api/v1/auth/session", headers=bearer(user["token"]["access_token"]))
    assert response.status_code == 200, response.text
    session = response.json()
    return {**session, "user": user, "headers": bearer(session["token"]["access_token"])}
