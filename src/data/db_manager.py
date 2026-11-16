"""This file contains the database service for the application."""

import asyncio
from typing import (
    List,
    Optional,
)

from fastapi import HTTPException
from sqlalchemy import (
    URL,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool
from sqlmodel import (
    Session,
    SQLModel,
    create_engine,
    select,
)

from src.config.settings import (
    Environment,
    settings,
)
from src.data.models.session import Session as ChatSession
from src.data.models.user import User
from src.system.logs import logger
from src.system.telemetry import db_connections


class DatabaseService:
    """Service class for database operations.

    This class handles all database operations for Users, Sessions, and Messages.
    It uses SQLModel for ORM operations and maintains a connection pool.

    SQLModel sessions are synchronous, so every public method runs its queries in a worker
    thread (asyncio.to_thread). Calling them directly from an async endpoint would block the
    event loop, and with it every other request, for the length of each query.
    """

    def __init__(self):
        """Initialize database service with connection pool."""
        # Configure environment-specific database connection pool settings
        pool_size = settings.POSTGRES_POOL_SIZE
        max_overflow = settings.POSTGRES_MAX_OVERFLOW

        # URL.create escapes the credentials, so passwords containing @, / or : work
        connection_url = URL.create(
            "postgresql+psycopg",
            username=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB,
        )

        self.engine = create_engine(
            connection_url,
            pool_pre_ping=True,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=30,  # Connection timeout (seconds)
            pool_recycle=1800,  # Recycle connections after 30 minutes
        )

        # Report the connections in use when Prometheus scrapes /metrics
        db_connections.set_function(self.engine.pool.checkedout)

        try:
            # Create tables (only if they don't exist)
            SQLModel.metadata.create_all(self.engine)

            logger.info(
                "database_initialized",
                environment=settings.ENVIRONMENT.value,
                pool_size=pool_size,
                max_overflow=max_overflow,
            )
        except SQLAlchemyError as e:
            logger.error("database_initialization_error", error=str(e), environment=settings.ENVIRONMENT.value)
            # In production, don't raise - allow app to start even with DB issues
            if settings.ENVIRONMENT != Environment.PRODUCTION:
                raise

        try:
            # pgvector, for long-term memory. mem0 runs CREATE EXTENSION IF NOT EXISTS from
            # several connections when its first requests arrive together, and two of those at
            # once collide on a fresh database. Created here, before any request, it exists.
            with self.engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except SQLAlchemyError as e:
            # Some managed databases let only an administrator create extensions
            logger.warning("pgvector_extension_not_created", error=str(e))

    async def create_user(self, email: str, password: str) -> User:
        """Create a new user.

        Args:
            email: User's email address
            password: Hashed password

        Returns:
            User: The created user
        """

        def _create() -> User:
            with Session(self.engine) as session:
                user = User(email=email, hashed_password=password)
                session.add(user)
                session.commit()
                session.refresh(user)
                return user

        user = await asyncio.to_thread(_create)
        logger.info("user_created", email=email)
        return user

    async def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID.

        Args:
            user_id: The ID of the user to retrieve

        Returns:
            Optional[User]: The user if found, None otherwise
        """

        def _get() -> Optional[User]:
            with Session(self.engine) as session:
                return session.get(User, user_id)

        return await asyncio.to_thread(_get)

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email.

        Args:
            email: The email of the user to retrieve

        Returns:
            Optional[User]: The user if found, None otherwise
        """

        def _get() -> Optional[User]:
            with Session(self.engine) as session:
                return session.exec(select(User).where(User.email == email)).first()

        return await asyncio.to_thread(_get)

    async def delete_user_by_email(self, email: str) -> bool:
        """Delete a user by email.

        Args:
            email: The email of the user to delete

        Returns:
            bool: True if deletion was successful, False if user not found
        """

        def _delete() -> bool:
            with Session(self.engine) as session:
                user = session.exec(select(User).where(User.email == email)).first()
                if not user:
                    return False
                session.delete(user)
                session.commit()
                return True

        deleted = await asyncio.to_thread(_delete)
        if deleted:
            logger.info("user_deleted", email=email)
        return deleted

    async def create_session(self, session_id: str, user_id: int, name: str = "") -> ChatSession:
        """Create a new chat session.

        Args:
            session_id: The ID for the new session
            user_id: The ID of the user who owns the session
            name: Optional name for the session (defaults to empty string)

        Returns:
            ChatSession: The created session
        """

        def _create() -> ChatSession:
            with Session(self.engine) as session:
                chat_session = ChatSession(id=session_id, user_id=user_id, name=name)
                session.add(chat_session)
                session.commit()
                session.refresh(chat_session)
                return chat_session

        chat_session = await asyncio.to_thread(_create)
        logger.info("session_created", session_id=session_id, user_id=user_id, name=name)
        return chat_session

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and the conversation LangGraph stored for it.

        Args:
            session_id: The ID of the session to delete

        Returns:
            bool: True if deletion was successful, False if session not found
        """

        def _delete() -> bool:
            with Session(self.engine) as session:
                chat_session = session.get(ChatSession, session_id)
                if not chat_session:
                    return False
                session.delete(chat_session)
                # The checkpointer keys the conversation by thread_id, which is the session ID.
                # Its tables only exist once the agent has run, hence to_regclass.
                connection = session.connection()
                for table in settings.CHECKPOINT_TABLES:
                    if connection.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar():
                        connection.execute(text(f"DELETE FROM {table} WHERE thread_id = :id"), {"id": session_id})
                session.commit()
                return True

        deleted = await asyncio.to_thread(_delete)
        if deleted:
            logger.info("session_deleted", session_id=session_id)
        return deleted

    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Get a session by ID.

        Args:
            session_id: The ID of the session to retrieve

        Returns:
            Optional[ChatSession]: The session if found, None otherwise
        """

        def _get() -> Optional[ChatSession]:
            with Session(self.engine) as session:
                return session.get(ChatSession, session_id)

        return await asyncio.to_thread(_get)

    async def get_user_sessions(self, user_id: int) -> List[ChatSession]:
        """Get all sessions for a user.

        Args:
            user_id: The ID of the user

        Returns:
            List[ChatSession]: List of user's sessions
        """

        def _get() -> List[ChatSession]:
            with Session(self.engine) as session:
                statement = select(ChatSession).where(ChatSession.user_id == user_id).order_by(ChatSession.created_at)
                return list(session.exec(statement).all())

        return await asyncio.to_thread(_get)

    async def update_session_name(self, session_id: str, name: str) -> ChatSession:
        """Update a session's name.

        Args:
            session_id: The ID of the session to update
            name: The new name for the session

        Returns:
            ChatSession: The updated session

        Raises:
            HTTPException: If session is not found
        """

        def _update() -> Optional[ChatSession]:
            with Session(self.engine) as session:
                chat_session = session.get(ChatSession, session_id)
                if not chat_session:
                    return None
                chat_session.name = name
                session.add(chat_session)
                session.commit()
                session.refresh(chat_session)
                return chat_session

        chat_session = await asyncio.to_thread(_update)
        if chat_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        logger.info("session_name_updated", session_id=session_id, name=name)
        return chat_session

    def get_session_maker(self):
        """Get a session maker for creating database sessions.

        Returns:
            Session: A SQLModel session maker
        """
        return Session(self.engine)

    async def health_check(self) -> bool:
        """Check database connection health.

        Returns:
            bool: True if database is healthy, False otherwise
        """

        def _ping() -> None:
            with Session(self.engine) as session:
                # Execute a simple query to check connection
                session.exec(select(1)).first()

        try:
            await asyncio.to_thread(_ping)
            return True
        except Exception as e:
            logger.error("database_health_check_failed", error=str(e))
            return False


# Create a singleton instance
db_manager = DatabaseService()
