"""This file contains the authentication utilities for the application."""

import uuid
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import (
    Any,
    Dict,
    Literal,
    Optional,
)

import jwt

from src.config.settings import (
    check_jwt_settings,
    settings,
)
from src.data.schemas.auth import Token
from src.system.logs import logger

# A user token says who the user is (it comes from /register or /login). A session token
# gives access to one chat session (it comes from /session). Each endpoint accepts one type.
TokenType = Literal["user", "session"]


def _signing_key() -> str:
    """Return JWT_SECRET_KEY, refusing to sign or verify anything with an unsafe key."""
    problem = check_jwt_settings(settings.JWT_SECRET_KEY, settings.JWT_ALGORITHM)
    if problem:
        raise RuntimeError(problem)
    return settings.JWT_SECRET_KEY


def create_access_token(subject: str, token_type: TokenType, expires_delta: Optional[timedelta] = None) -> Token:
    """Create a signed access token.

    Args:
        subject: The user ID for a user token, the session ID for a session token.
        token_type: "user" or "session".
        expires_delta: Optional lifetime. Defaults to JWT_ACCESS_TOKEN_EXPIRE_DAYS.

    Returns:
        Token: The generated access token.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS))

    to_encode = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": expire,
        "jti": uuid.uuid4().hex,  # Unique token identifier
    }

    encoded_jwt = jwt.encode(to_encode, _signing_key(), algorithm=settings.JWT_ALGORITHM)

    logger.info("token_created", token_type=token_type, subject=subject, expires_at=expire.isoformat())

    return Token(access_token=encoded_jwt, expires_at=expire)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Return the claims of a token whose signature and expiry check out.

    Args:
        token: The JWT.

    Returns:
        Optional[Dict[str, Any]]: The claims, or None if the token is malformed, forged or expired.
    """
    try:
        return jwt.decode(
            token,
            _signing_key(),
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "type", "iat", "exp"]},
        )
    except jwt.InvalidTokenError:
        return None


def verify_token(token: str, token_type: TokenType) -> Optional[str]:
    """Verify a token and return its subject.

    Args:
        token: The JWT from the Authorization header.
        token_type: The type of token the endpoint accepts.

    Returns:
        Optional[str]: The user or session ID, or None if the token is invalid, expired,
        or of the other type.
    """
    claims = decode_token(token)
    if claims is None:
        logger.warning("token_rejected", reason="invalid or expired")
        return None
    if claims.get("type") != token_type:
        logger.warning("token_rejected", reason="wrong token type", expected=token_type, got=claims.get("type"))
        return None
    return claims["sub"]
