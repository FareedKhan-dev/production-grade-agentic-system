"""This file contains the LangGraph Agent/workflow and interactions with the LLM."""

import asyncio
from typing import (
    Any,
    AsyncGenerator,
    Optional,
)
from urllib.parse import quote_plus

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import (
    END,
    StateGraph,
)
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import (
    Command,
    StateSnapshot,
)
from mem0 import AsyncMemory
from psycopg_pool import AsyncConnectionPool

from src.agent.prompts import load_system_prompt
from src.agent.tools import tools
from src.config.settings import (
    Environment,
    settings,
)
from src.data.schemas import (
    ChatMessage,
    GraphState,
    Message,
)
from src.services.llm_provider import llm_service
from src.system.logs import logger
from src.system.telemetry import llm_inference_duration_seconds
from src.utils import (
    dump_messages,
    message_text,
    prepare_messages,
    process_llm_response,
)


class LangGraphAgent:
    """Manages the LangGraph Agent/workflow and interactions with the LLM.

    This class handles the creation and management of the LangGraph workflow,
    including LLM interactions, database connections, and response processing.
    """

    def __init__(self):
        """Initialize the LangGraph Agent with necessary components."""
        # Use the LLM service with tools bound
        self.llm_service = llm_service
        self.llm_service.bind_tools(tools)
        self.tools_by_name = {tool.name: tool for tool in tools}
        self._connection_pool: Optional[AsyncConnectionPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        self.memory: Optional[AsyncMemory] = None
        # The graph and the memory client are built by the first request that needs them. On a
        # fresh server many requests arrive together; the locks make them share one build
        # instead of each opening its own connection pool.
        self._graph_lock = asyncio.Lock()
        self._memory_lock = asyncio.Lock()
        # asyncio keeps only a weak reference to a task, so one nobody holds can be garbage
        # collected before it finishes. Background memory updates are held here until done.
        self._background_tasks: set[asyncio.Task] = set()
        logger.info(
            "langgraph_agent_initialized",
            model=settings.DEFAULT_LLM_MODEL,
            environment=settings.ENVIRONMENT.value,
        )

    async def _long_term_memory(self) -> AsyncMemory:
        """Initialize the long term memory (once; later calls reuse the client)."""
        if self.memory is not None:
            return self.memory
        async with self._memory_lock:
            if self.memory is not None:
                return self.memory
            llm_config = {
                "model": settings.LONG_TERM_MEMORY_MODEL,
                "api_key": settings.OPENAI_API_KEY,
                "openai_base_url": settings.OPENAI_BASE_URL,
            }
            embedder_config = {
                "model": settings.LONG_TERM_MEMORY_EMBEDDER_MODEL,
                "api_key": settings.OPENAI_API_KEY,
                "openai_base_url": settings.OPENAI_BASE_URL,
                "embedding_dims": settings.LONG_TERM_MEMORY_EMBEDDER_DIMS,
            }

            config = {
                "vector_store": {
                    "provider": "pgvector",
                    "config": {
                        "collection_name": settings.LONG_TERM_MEMORY_COLLECTION_NAME,
                        "embedding_model_dims": settings.LONG_TERM_MEMORY_EMBEDDER_DIMS,
                        "dbname": settings.POSTGRES_DB,
                        "user": settings.POSTGRES_USER,
                        "password": settings.POSTGRES_PASSWORD,
                        "host": settings.POSTGRES_HOST,
                        "port": settings.POSTGRES_PORT,
                    },
                },
                "llm": {"provider": "openai", "config": llm_config},
                "embedder": {"provider": "openai", "config": embedder_config},
                # mem0's "custom_instructions" option can steer what gets remembered
            }
            # mem0 2 builds the client synchronously (it connects to pgvector and creates its
            # tables), so build it in a worker thread instead of blocking the event loop.
            # mem0 1 made from_config a coroutine; awaiting it under mem0 2 raises TypeError.
            self.memory = await asyncio.to_thread(self._build_memory_client, config)
        return self.memory

    @staticmethod
    def _build_memory_client(config: dict) -> AsyncMemory:
        """Create the mem0 client, including the parts it would otherwise create on first use."""
        memory = AsyncMemory.from_config(config)
        # mem0 creates its entity store lazily and without a lock. When the first searches
        # arrive together, several threads create it at once and their CREATE EXTENSION and
        # CREATE TABLE statements collide in PostgreSQL, so create it here, once.
        getattr(memory, "entity_store", None)
        return memory

    async def _get_connection_pool(self) -> AsyncConnectionPool:
        """Get a PostgreSQL connection pool using environment-specific settings.

        Returns:
            AsyncConnectionPool: A connection pool for PostgreSQL database.
        """
        if self._connection_pool is None:
            try:
                # Configure pool size based on environment
                max_size = settings.POSTGRES_POOL_SIZE

                connection_url = (
                    "postgresql://"
                    f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                )

                self._connection_pool = AsyncConnectionPool(
                    connection_url,
                    open=False,
                    max_size=max_size,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 5,
                        "prepare_threshold": None,
                    },
                )
                await self._connection_pool.open()
                logger.info("connection_pool_created", max_size=max_size, environment=settings.ENVIRONMENT.value)
            except Exception as e:
                logger.error("connection_pool_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                # In production, we might want to degrade gracefully
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_connection_pool", environment=settings.ENVIRONMENT.value)
                    return None
                raise e
        return self._connection_pool

    async def _get_relevant_memory(self, user_id: str, query: str) -> str:
        """Get the relevant memory for the user and query.

        Args:
            user_id (str): The user ID.
            query (str): The query to search for.

        Returns:
            str: The relevant memory.
        """
        try:
            memory = await self._long_term_memory()
            # mem0 2 takes the user inside filters; a user_id keyword raises ValueError there
            results = await memory.search(query=query, filters={"user_id": str(user_id)})
            return "\n".join([f"* {result['memory']}" for result in results["results"]])
        except Exception as e:
            logger.error("failed_to_get_relevant_memory", error=str(e), user_id=user_id)
            return ""

    async def _update_long_term_memory(self, user_id: str, messages: list[dict], metadata: dict = None) -> None:
        """Update the long term memory.

        Args:
            user_id (str): The user ID.
            messages (list[dict]): The messages to update the long term memory with.
            metadata (dict): Optional metadata to include.
        """
        try:
            memory = await self._long_term_memory()
            await memory.add(messages, user_id=str(user_id), metadata=metadata)
            logger.info("long_term_memory_updated_successfully", user_id=user_id)
        except Exception as e:
            logger.exception(
                "failed_to_update_long_term_memory",
                user_id=user_id,
                error=str(e),
            )

    def _update_long_term_memory_in_background(self, user_id: str, messages: list[BaseMessage], session_id: str):
        """Start a memory update without making the user wait for it."""
        metadata = {"session_id": session_id, "environment": settings.ENVIRONMENT.value}
        task = asyncio.create_task(
            self._update_long_term_memory(user_id, convert_to_openai_messages(messages), metadata)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        """Process the chat state and generate a response.

        Args:
            state (GraphState): The current state of the conversation.
            config (RunnableConfig): The run configuration (thread ID, callbacks).

        Returns:
            Command: Command object with updated state and next node to execute.
        """
        model_name = self.llm_service.model_name

        SYSTEM_PROMPT = load_system_prompt(long_term_memory=state.long_term_memory)

        # Prepare messages with system prompt
        messages = prepare_messages(state.messages, SYSTEM_PROMPT)

        try:
            # Use LLM service with automatic retries and circular fallback
            with llm_inference_duration_seconds.labels(model=model_name).time():
                response_message = await self.llm_service.call(dump_messages(messages))

            # Process response to handle structured content blocks
            response_message = process_llm_response(response_message)

            logger.info(
                "llm_response_generated",
                session_id=config["configurable"]["thread_id"],
                model=self.llm_service.model_name,
                environment=settings.ENVIRONMENT.value,
            )

            # Determine next node based on whether there are tool calls
            if response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END

            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.error(
                "llm_call_failed_all_models",
                session_id=config["configurable"]["thread_id"],
                error=str(e),
                environment=settings.ENVIRONMENT.value,
            )
            raise Exception(f"failed to get llm response after trying all models: {str(e)}")

    # Define our tool node
    async def _tool_call(self, state: GraphState) -> Command:
        """Process tool calls from the last message.

        Args:
            state: The current agent state containing messages and tool calls.

        Returns:
            Command: Command object with updated messages and routing back to chat.
        """
        outputs = []
        for tool_call in state.messages[-1].tool_calls:
            tool = self.tools_by_name.get(tool_call["name"])
            logger.info("tool_called", tool=tool_call["name"], known=tool is not None)
            if tool is None:
                # Models sometimes call a tool that does not exist; tell it instead of crashing
                tool_result = f"Error: there is no tool named {tool_call['name']!r}."
            else:
                tool_result = await tool.ainvoke(tool_call["args"])
            outputs.append(
                ToolMessage(
                    content=tool_result,
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
        return Command(update={"messages": outputs}, goto="chat")

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Create and configure the LangGraph workflow.

        Returns:
            Optional[CompiledStateGraph]: The configured LangGraph instance or None if init fails
        """
        if self._graph is not None:
            return self._graph
        async with self._graph_lock:
            if self._graph is not None:
                return self._graph
            try:
                graph_builder = StateGraph(GraphState)
                graph_builder.add_node("chat", self._chat, destinations=("tool_call", END))
                graph_builder.add_node("tool_call", self._tool_call, destinations=("chat",))
                graph_builder.set_entry_point("chat")
                graph_builder.set_finish_point("chat")

                # Get connection pool (may be None in production if DB unavailable)
                connection_pool = await self._get_connection_pool()
                if connection_pool:
                    checkpointer = AsyncPostgresSaver(connection_pool)
                    await checkpointer.setup()
                else:
                    # In production, proceed without checkpointer if needed
                    checkpointer = None
                    if settings.ENVIRONMENT != Environment.PRODUCTION:
                        raise Exception("Connection pool initialization failed")

                self._graph = graph_builder.compile(
                    checkpointer=checkpointer, name=f"{settings.PROJECT_NAME} Agent ({settings.ENVIRONMENT.value})"
                )

                logger.info(
                    "graph_created",
                    graph_name=f"{settings.PROJECT_NAME} Agent",
                    environment=settings.ENVIRONMENT.value,
                    has_checkpointer=checkpointer is not None,
                )
            except Exception as e:
                logger.error("graph_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                # In production, we don't want to crash the app
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_graph")
                    return None
                raise e

        return self._graph

    def _run_config(self, session_id: str, user_id: Optional[str]) -> dict[str, Any]:
        """Build the run configuration: the conversation's thread and the Langfuse trace attributes.

        Langfuse 3 reads the trace's user and session from these metadata keys; its
        CallbackHandler no longer takes them as arguments.
        """
        return {
            "configurable": {"thread_id": session_id},
            "callbacks": [CallbackHandler()],
            "metadata": {
                "langfuse_user_id": str(user_id) if user_id is not None else None,
                "langfuse_session_id": session_id,
                "langfuse_tags": [settings.ENVIRONMENT.value],
                "user_id": user_id,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
            },
        }

    async def get_response(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[str] = None,
    ) -> list[ChatMessage]:
        """Get a response from the LLM.

        Args:
            messages (list[Message]): The messages to send to the LLM.
            session_id (str): The session ID for Langfuse tracking.
            user_id (Optional[str]): The user ID for Langfuse tracking.

        Returns:
            list[ChatMessage]: The conversation, ending with the new answer.

        Raises:
            Exception: If the graph could not be built or the LLM did not answer.
        """
        if self._graph is None:
            self._graph = await self.create_graph()
        config = self._run_config(session_id, user_id)
        relevant_memory = (
            await self._get_relevant_memory(user_id, messages[-1].content)
        ) or "No relevant memory found."

        response = await self._graph.ainvoke(
            input={"messages": dump_messages(messages), "long_term_memory": relevant_memory},
            config=config,
        )
        # Run memory update in background without blocking the response
        self._update_long_term_memory_in_background(user_id, response["messages"], session_id)
        return self._process_messages(response["messages"])

    async def get_stream_response(
        self, messages: list[Message], session_id: str, user_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Get a stream response from the LLM.

        Args:
            messages (list[Message]): The messages to send to the LLM.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.

        Yields:
            str: Pieces of the answer's text.
        """
        config = self._run_config(session_id, user_id)
        if self._graph is None:
            self._graph = await self.create_graph()

        relevant_memory = (
            await self._get_relevant_memory(user_id, messages[-1].content)
        ) or "No relevant memory found."

        try:
            async for chunk, metadata in self._graph.astream(
                {"messages": dump_messages(messages), "long_term_memory": relevant_memory},
                config,
                stream_mode="messages",
            ):
                # "messages" mode also emits the messages other nodes add to the state, such as
                # the raw search results of the tool node. Only the model's text is the answer.
                if metadata.get("langgraph_node") != "chat" or not isinstance(chunk, AIMessage):
                    continue
                text = message_text(chunk.content)
                if text:
                    yield text

            # After streaming completes, get final state and update memory in background
            state: StateSnapshot = await self._graph.aget_state(config=config)
            if state.values and "messages" in state.values:
                self._update_long_term_memory_in_background(user_id, state.values["messages"], session_id)
        except Exception as stream_error:
            logger.error("Error in stream processing", error=str(stream_error), session_id=session_id)
            raise stream_error

    async def get_chat_history(self, session_id: str) -> list[ChatMessage]:
        """Get the chat history for a given thread ID.

        Args:
            session_id (str): The session ID for the conversation.

        Returns:
            list[ChatMessage]: The chat history.
        """
        if self._graph is None:
            self._graph = await self.create_graph()

        state: StateSnapshot = await self._graph.aget_state(config={"configurable": {"thread_id": session_id}})
        return self._process_messages(state.values["messages"]) if state.values else []

    def _process_messages(self, messages: list[BaseMessage]) -> list[ChatMessage]:
        openai_style_messages = convert_to_openai_messages(messages)
        # keep just assistant and user messages
        return [
            ChatMessage(role=message["role"], content=message_text(message["content"]))
            for message in openai_style_messages
            if message["role"] in ["assistant", "user"] and message_text(message["content"])
        ]

    async def clear_chat_history(self, session_id: str) -> None:
        """Clear all chat history for a given thread ID.

        Args:
            session_id: The ID of the session to clear history for.

        Raises:
            Exception: If there's an error clearing the chat history.
        """
        try:
            # Make sure the pool is initialized in the current event loop
            conn_pool = await self._get_connection_pool()

            # Use a new connection for this specific operation
            async with conn_pool.connection() as conn:
                for table in settings.CHECKPOINT_TABLES:
                    try:
                        await conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (session_id,))
                        logger.info(f"Cleared {table} for session {session_id}")
                    except Exception as e:
                        logger.error(f"Error clearing {table}", error=str(e))
                        raise

        except Exception as e:
            logger.error("Failed to clear chat history", error=str(e))
            raise
