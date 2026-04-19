"""SQLAlchemy engine and session factory.

Provides a configured engine and scoped session for database access.
All database sessions should be obtained through this module.
"""

from datetime import datetime
from uuid import uuid4

from flask import Flask, g
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


# Module-level session factory, initialized once by init_db()
_session_factory: sessionmaker[Session] | None = None


def init_db(app: Flask) -> None:
    """Initialize the database for the Flask app.

    Creates the session factory and registers a teardown handler
    that closes the request-scoped session after each request.

    Args:
        app: The Flask application instance.
    """
    global _session_factory
    _session_factory = get_session_factory()
    app.teardown_appcontext(_teardown_session)


def _teardown_session(exception: BaseException | None) -> None:
    """Close the request-scoped DB session.

    Called automatically by Flask at the end of each request.
    Rolls back on exception, commits otherwise.

    Args:
        exception: The exception that occurred, if any.
    """
    session: Session | None = g.pop("db_session", None)
    if session is not None:
        if exception is not None:
            session.rollback()
        else:
            session.commit()
        session.close()


def get_db() -> Session:
    """Get the request-scoped database session.

    Creates a new session on first call within a request context
    and stores it on Flask's g object. Subsequent calls within the
    same request return the same session.

    Returns:
        An active SQLAlchemy Session for the current request.

    Raises:
        RuntimeError: If init_db() has not been called.
    """
    if "db_session" not in g:
        if _session_factory is None:
            raise RuntimeError("Database not initialized. Call init_db(app) first.")
        g.db_session = _session_factory()
    session: Session = g.db_session
    return session
