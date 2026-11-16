"""Authentication and authorization endpoints for the API.

This module provides endpoints for user registration, login, session management,
and token verification.
"""

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import List

import bcrypt
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy.exc import IntegrityError

from src.config.settings import settings
from src.data.db_manager import db_manager
from src.data.models.session import Session
from src.data.models.user import (
    User,
    password_matches,
)
from src.data.schemas.auth import (
    SessionResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from src.system.logs import (
    bind_context,
    logger,
)
from src.system.rate_limit import limiter
from src.utils.auth import (
    create_access_token,
    verify_token,
)
from src.utils.sanitization import (
    sanitize_email,
    validate_password_strength,
)

router = APIRouter()
security = HTTPBearer()
# One connection pool for the whole application
db_service = db_manager


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A bcrypt hash to check unknown emails against, so they take as long as known ones."""
    return bcrypt.hashpw(b"no such user", bcrypt.gensalt()).decode("utf-8")


# bcrypt spends about a quarter of a second of CPU on every password, on purpose. In asyncio's
# default thread pool it would share threads with the database queries, and a burst of logins
# would occupy every core and starve the event loop: in a load test with 1,500 logins at once
# that made waiting chats time out. bcrypt gets a pool of its own, one thread short of the core
# count, so a core stays free for everything else.
_bcrypt_pool = ThreadPoolExecutor(max_workers=max(1, (os.process_cpu_count() or 2) - 1), thread_name_prefix="bcrypt")


async def _in_bcrypt_pool(function, *args):
    """Run a bcrypt call (hash or check) in its own thread pool."""
    return await asyncio.get_running_loop().run_in_executor(_bcrypt_pool, function, *args)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Get the user a user token was issued to.

    Args:
        credentials: The HTTP authorization credentials containing the JWT token.

    Returns:
        User: The user extracted from the token.

    Raises:
        HTTPException: 401 if the token is invalid, expired, a session token, or its user no longer exists.
    """
    user_id = verify_token(credentials.credentials, "user")
    if user_id is None or not user_id.isdigit():
        raise _unauthorized()

    user = await db_service.get_user(int(user_id))
    if user is None:
        logger.warning("user_not_found", user_id=user_id)
        raise _unauthorized()

    # Bind user_id to logging context for all subsequent logs in this request
    bind_context(user_id=user.id)

    return user


async def get_current_session(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Session:
    """Get the chat session a session token was issued for.

    Args:
        credentials: The HTTP authorization credentials containing the JWT token.

    Returns:
        Session: The session extracted from the token.

    Raises:
        HTTPException: 401 if the token is invalid, expired, a user token, or its session was deleted.
    """
    session_id = verify_token(credentials.credentials, "session")
    if session_id is None:
        raise _unauthorized()

    session = await db_service.get_session(session_id)
    if session is None:
        logger.warning("session_not_found", session_id=session_id)
        raise _unauthorized()

    # Bind user_id to logging context for all subsequent logs in this request
    bind_context(user_id=session.user_id, session_id=session.id)

    return session


@router.post("/register", response_model=UserResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["register"][0])
async def register_user(request: Request, user_data: UserCreate):
    """Register a new user.

    Args:
        request: The FastAPI request object for rate limiting.
        user_data: User registration data

    Returns:
        UserResponse: The created user info
    """
    try:
        # Sanitize email
        sanitized_email = sanitize_email(user_data.email)

        # Extract and validate password
        password = user_data.password.get_secret_value()
        validate_password_strength(password)
    except ValueError as ve:
        logger.warning("user_registration_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))

    # Check if user exists
    if await db_service.get_user_by_email(sanitized_email):
        raise HTTPException(status_code=400, detail="Email already registered")

    # bcrypt is slow on purpose; hash in its own thread pool so other requests keep being served
    hashed_password = await _in_bcrypt_pool(User.hash_password, password)
    try:
        user = await db_service.create_user(email=sanitized_email, password=hashed_password)
    except IntegrityError:
        # Another request registered the same email since the check above
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create access token
    token = create_access_token(str(user.id), "user")

    return UserResponse(id=user.id, email=user.email, token=token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["login"][0])
async def login(
    request: Request, username: str = Form(...), password: str = Form(...), grant_type: str = Form(default="password")
):
    """Login a user.

    Args:
        request: The FastAPI request object for rate limiting.
        username: User's email
        password: User's password
        grant_type: Must be "password"

    Returns:
        TokenResponse: Access token information

    Raises:
        HTTPException: If credentials are invalid
    """
    # Verify grant type
    if grant_type != "password":
        raise HTTPException(
            status_code=400,
            detail="Unsupported grant type. Must be 'password'",
        )

    # Emails are stored lowercased at registration. The password is compared exactly as typed:
    # it is hashed, never displayed, so there is nothing to escape.
    user = await db_service.get_user_by_email(username.strip().lower())

    # Check against a dummy hash when the email is unknown, so the response time does not
    # reveal which emails have accounts.
    hashed_password = user.hashed_password if user else await _in_bcrypt_pool(_dummy_hash)
    password_ok = await _in_bcrypt_pool(password_matches, password, hashed_password)

    if user is None or not password_ok:
        # A burst of these is what a password-guessing attack looks like in the logs
        logger.warning("login_failed")
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(str(user.id), "user")
    logger.info("user_logged_in", user_id=user.id)
    return TokenResponse(access_token=token.access_token, token_type="bearer", expires_at=token.expires_at)


@router.post("/session", response_model=SessionResponse)
async def create_session(user: User = Depends(get_current_user)):
    """Create a new chat session for the authenticated user.

    Args:
        user: The authenticated user

    Returns:
        SessionResponse: The session ID, name, and access token
    """
    # Generate a unique session ID
    session_id = str(uuid.uuid4())

    # Create session in database
    session = await db_service.create_session(session_id, user.id)

    # Create access token for the session
    token = create_access_token(session_id, "session")

    logger.info(
        "session_created",
        session_id=session_id,
        user_id=user.id,
        name=session.name,
        expires_at=token.expires_at.isoformat(),
    )

    return SessionResponse(session_id=session_id, name=session.name, token=token)


@router.patch("/session/{session_id}/name", response_model=SessionResponse)
async def update_session_name(
    session_id: str,
    name: str = Form(..., max_length=100),
    current_session: Session = Depends(get_current_session),
):
    """Update a session's name.

    Args:
        session_id: The ID of the session to update
        name: The new name for the session
        current_session: The current session from auth

    Returns:
        SessionResponse: The updated session information
    """
    # Verify the session ID matches the authenticated session
    if session_id != current_session.id:
        raise HTTPException(status_code=403, detail="Cannot modify other sessions")

    # Store the name as given; SessionResponse removes characters that are unsafe to display
    session = await db_service.update_session_name(session_id, name)

    # Create a new token (not strictly necessary but maintains consistency)
    token = create_access_token(session_id, "session")

    return SessionResponse(session_id=session_id, name=session.name, token=token)


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, current_session: Session = Depends(get_current_session)):
    """Delete a session and its conversation for the authenticated user.

    Args:
        session_id: The ID of the session to delete
        current_session: The current session from auth

    Returns:
        None
    """
    # Verify the session ID matches the authenticated session
    if session_id != current_session.id:
        raise HTTPException(status_code=403, detail="Cannot delete other sessions")

    # Delete the session and its messages
    await db_service.delete_session(session_id)

    logger.info("session_deleted", session_id=session_id, user_id=current_session.user_id)


@router.get("/sessions", response_model=List[SessionResponse])
async def get_user_sessions(user: User = Depends(get_current_user)):
    """Get all session IDs for the authenticated user.

    Args:
        user: The authenticated user

    Returns:
        List[SessionResponse]: List of session IDs
    """
    sessions = await db_service.get_user_sessions(user.id)
    return [
        SessionResponse(
            session_id=session.id,
            name=session.name,
            token=create_access_token(session.id, "session"),
        )
        for session in sessions
    ]
