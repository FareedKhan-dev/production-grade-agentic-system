"""This file contains the session model for the application."""

from typing import (
    TYPE_CHECKING,
    List,
)

from sqlmodel import (
    Field,
    Relationship,
)

from src.data.models.base import BaseModel

if TYPE_CHECKING:
    from src.data.models.user import User


class Session(BaseModel, table=True):
    """Session model for storing chat sessions.

    Attributes:
        id: The primary key
        user_id: Foreign key to the user
        name: Name of the session (defaults to empty string)
        created_at: When the session was created
        messages: Relationship to session messages
        user: Relationship to the session owner
    """

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)  # GET /sessions filters on it
    name: str = Field(default="")
    user: "User" = Relationship(back_populates="sessions")
