"""This file contains the user model for the application."""

from typing import (
    TYPE_CHECKING,
    List,
)

import bcrypt
from sqlmodel import (
    Field,
    Relationship,
)

from src.data.models.base import BaseModel

if TYPE_CHECKING:
    from src.data.models.session import Session

# bcrypt only reads the first 72 bytes of a password.
BCRYPT_MAX_BYTES = 72


def password_matches(password: str, hashed_password: str) -> bool:
    """Check a password against a bcrypt hash.

    bcrypt 5 raises on passwords longer than 72 bytes where older versions cut them
    silently, so cut here to keep accepting accounts created before the limit was enforced.
    """
    return bcrypt.checkpw(password.encode("utf-8")[:BCRYPT_MAX_BYTES], hashed_password.encode("utf-8"))


class User(BaseModel, table=True):
    """User model for storing user accounts.

    Attributes:
        id: The primary key
        email: User's email (unique)
        hashed_password: Bcrypt hashed password
        created_at: When the user was created
        sessions: Relationship to user's chat sessions
    """

    id: int = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    sessions: List["Session"] = Relationship(back_populates="user")

    def verify_password(self, password: str) -> bool:
        """Verify if the provided password matches the hash."""
        return password_matches(password, self.hashed_password)

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        secret = password.encode("utf-8")
        if len(secret) > BCRYPT_MAX_BYTES:
            raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes long")
        return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("utf-8")


# Avoid circular imports
from src.data.models.session import Session  # noqa: E402
