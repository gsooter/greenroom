"""SQLAlchemy engine and session factory.

Provides a configured engine and scoped session for database access.
All database sessions should be obtained through this module.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from backend.core.config import get_settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass


class TimestampMixin:
    """Mixin that adds created_at and updated_at columns.

    Attributes:
        created_at: Timestamp when the row was created.
        updated_at: Timestamp when the row was last updated.
    """

    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


def new_uuid() -> str:
    """Generate a new UUID4 string for use as a default primary key.

    Returns:
        A new UUID4 as a string.
    """
    return str(uuid4())


def get_engine() -> Engine:
    """Create and return a SQLAlchemy engine.

    Returns:
        A configured SQLAlchemy engine instance.
    """
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.debug,
    )


def get_session_factory() -> sessionmaker[Session]:
    """Create and return a session factory bound to the engine.

    Returns:
        A sessionmaker instance configured with the application engine.
    """
    engine = get_engine()
    return sessionmaker(bind=engine, expire_on_commit=False)
