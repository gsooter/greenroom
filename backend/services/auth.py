"""Authentication service — magic-link sign-in, and (coming next) Google,
Apple, and WebAuthn passkey flows.

Routes stay thin; all auth business logic lives here. The module imports
its collaborators by name (``magic_links_repo``, ``users_repo``,
``email_service``) so tests can ``monkeypatch.setattr`` them without
patching import machinery.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.core.auth import issue_token
from backend.core.config import get_settings
from backend.core.exceptions import (
    MAGIC_LINK_ALREADY_USED,
    MAGIC_LINK_EXPIRED,
    MAGIC_LINK_INVALID,
    AppError,
    ValidationError,
)
from backend.data.models.users import User
from backend.data.repositories import magic_links as magic_links_repo
from backend.data.repositories import users as users_repo
from backend.services import email as email_service

_RAW_TOKEN_BYTES = 32


@dataclass(frozen=True)
class MagicLinkDelivery:
    """Return value from :func:`generate_magic_link`.

    Attributes:
        raw_token: The unhashed token that rode in the outgoing email.
            Exposed to the caller only so tests can assert on it; a
            production caller discards it immediately after the email
            is sent.
        expires_at: Wall-clock UTC expiry of the issued token.
    """

    raw_token: str
    expires_at: datetime


@dataclass(frozen=True)
class MagicLinkVerification:
    """Return value from :func:`verify_magic_link`.

    Attributes:
        user: The authenticated user — either freshly created on first
            visit or the pre-existing row for a returning user.
        jwt: A signed session token the client stores and presents on
            subsequent API calls.
    """

    user: User
    jwt: str


def generate_magic_link(session: Session, *, email: str) -> MagicLinkDelivery:
    """Mint a magic-link token for ``email`` and send it via SendGrid.

    Only the SHA-256 hash of the raw token is persisted (Decision 027);
    the raw value leaves the process inside the email body and is
    returned to the caller so it can be asserted on in tests and logged
    behind a debug flag if needed.

    Args:
        session: Active SQLAlchemy session.
        email: Destination address. Casing is normalized before storage
            so the lookup key is stable across clients that upper- or
            title-case addresses.

    Returns:
        A :class:`MagicLinkDelivery` with the raw token and its expiry.

    Raises:
        ValidationError: If the email is empty or whitespace-only.
        AppError: ``EMAIL_DELIVERY_FAILED`` if SendGrid rejects the send.
    """
    cleaned = email.strip().lower()
    if not cleaned:
        raise ValidationError(message="An email address is required.")

    settings = get_settings()
    raw_token = secrets.token_urlsafe(_RAW_TOKEN_BYTES)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.magic_link_ttl_seconds)

    magic_links_repo.create(
        session,
        email=cleaned,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    verify_url = (
        f"{settings.frontend_base_url.rstrip('/')}/auth/verify?token={raw_token}"
    )
    html_body = _render_email_html(verify_url=verify_url)
    email_service.send_email(
        to=cleaned,
        subject="Your Greenroom sign-in link",
        html_body=html_body,
    )

    return MagicLinkDelivery(raw_token=raw_token, expires_at=expires_at)


def verify_magic_link(session: Session, *, token: str) -> MagicLinkVerification:
    """Redeem a magic-link token and return the authenticated user + JWT.

    Looks up the token by its hash, checks freshness and single-use,
    finds-or-creates the user bound to the token's email, stamps the
    token as used, refreshes ``last_login_at``, and signs a JWT.

    Args:
        session: Active SQLAlchemy session.
        token: The raw token from the email URL.

    Returns:
        A :class:`MagicLinkVerification` with the user and a fresh JWT.

    Raises:
        AppError: ``MAGIC_LINK_INVALID`` when no matching token exists
            or the bound user is deactivated;
            ``MAGIC_LINK_EXPIRED`` when ``expires_at`` has passed;
            ``MAGIC_LINK_ALREADY_USED`` when ``used_at`` is already set.
    """
    row = magic_links_repo.get_by_hash(session, _hash_token(token))
    if row is None:
        raise AppError(
            code=MAGIC_LINK_INVALID,
            message="This sign-in link is not valid.",
            status_code=400,
        )
    if row.used_at is not None:
        raise AppError(
            code=MAGIC_LINK_ALREADY_USED,
            message="This sign-in link has already been used.",
            status_code=400,
        )
    if row.expires_at <= datetime.now(UTC):
        raise AppError(
            code=MAGIC_LINK_EXPIRED,
            message="This sign-in link has expired.",
            status_code=400,
        )

    email = row.email.lower()
    user = users_repo.get_user_by_email(session, email)
    if user is None:
        user = users_repo.create_user(session, email=email)
    elif not user.is_active:
        raise AppError(
            code=MAGIC_LINK_INVALID,
            message="This account is no longer active.",
            status_code=400,
        )

    magic_links_repo.mark_used(session, row, user_id=user.id)
    users_repo.update_last_login(session, user)

    return MagicLinkVerification(user=user, jwt=issue_token(user.id))


def _hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest used for at-rest token storage.

    Args:
        raw_token: The unhashed token string.

    Returns:
        Lowercase hex digest suitable for the ``token_hash`` column.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _render_email_html(*, verify_url: str) -> str:
    """Build the HTML body for the magic-link email.

    Kept intentionally simple — one paragraph, one prominent link. The
    plain-text fallback in :mod:`backend.services.email` strips the
    tags and still leaves a clickable URL.

    Args:
        verify_url: Absolute URL containing the raw token as a query
            parameter.

    Returns:
        HTML string ready to hand to SendGrid.
    """
    return (
        "<p>Tap the link below to finish signing in to Greenroom. "
        "This link is single-use and expires shortly.</p>"
        f'<p><a href="{verify_url}">Sign in to Greenroom</a></p>'
        f"<p>If the link doesn't work, paste this URL into your browser: "
        f"{verify_url}</p>"
    )
