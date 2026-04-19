"""Repository functions for WebAuthn passkey credentials.

All DB access for :class:`backend.data.models.users.PasskeyCredential`
lives here. The auth service drives WebAuthn ceremony state; this
module only persists the resulting credential rows and the monotonic
sign counts that detect cloned authenticators.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.data.models.users import PasskeyCredential


def create(
    session: Session,
    *,
    user_id: uuid.UUID,
    credential_id: str,
    public_key: str,
    transports: str | None = None,
    name: str | None = None,
    sign_count: int = 0,
) -> PasskeyCredential:
    """Persist a newly registered passkey credential.

    Args:
        session: Active SQLAlchemy session.
        user_id: The owning user's UUID.
        credential_id: Raw credential id returned by the authenticator,
            base64url-encoded.
        public_key: CBOR public key, base64url-encoded.
        transports: Optional comma-separated transports hint
            (e.g. ``internal,hybrid``).
        name: Optional user-visible label for the credential.
        sign_count: Starting signature counter. Defaults to 0 — most
            platform authenticators begin at 0.

    Returns:
        The newly persisted :class:`PasskeyCredential`.
    """
    row = PasskeyCredential(
        user_id=user_id,
        credential_id=credential_id,
        public_key=public_key,
        transports=transports,
        name=name,
        sign_count=sign_count,
    )
    session.add(row)
    session.flush()
    return row


def get_by_credential_id(
    session: Session, credential_id: str
) -> PasskeyCredential | None:
    """Fetch a credential by its raw credential_id.

    Args:
        session: Active SQLAlchemy session.
        credential_id: The authenticator-supplied credential id to
            look up.

    Returns:
        The :class:`PasskeyCredential` if found, else None.
    """
    stmt = select(PasskeyCredential).where(
        PasskeyCredential.credential_id == credential_id
    )
    return session.execute(stmt).scalar_one_or_none()


def list_by_user(session: Session, user_id: uuid.UUID) -> list[PasskeyCredential]:
    """Return every credential registered for a user.

    Args:
        session: Active SQLAlchemy session.
        user_id: User whose credentials should be returned.

    Returns:
        A list of :class:`PasskeyCredential` rows, newest-first.
    """
    stmt = (
        select(PasskeyCredential)
        .where(PasskeyCredential.user_id == user_id)
        .order_by(PasskeyCredential.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def update_sign_count(
    session: Session,
    credential: PasskeyCredential,
    *,
    new_count: int,
) -> PasskeyCredential:
    """Store the authenticator's latest signature counter and last-used stamp.

    A regression in ``new_count`` relative to the stored value is a
    signal that the credential has been cloned; the service layer is
    expected to reject the ceremony before calling this function.

    Args:
        session: Active SQLAlchemy session.
        credential: The credential row to update.
        new_count: The monotonic sign counter reported by the
            authenticator on this ceremony.

    Returns:
        The updated :class:`PasskeyCredential`.
    """
    credential.sign_count = new_count
    credential.last_used_at = datetime.now(UTC)
    session.flush()
    return credential


def delete(session: Session, credential: PasskeyCredential) -> None:
    """Remove a credential row.

    Args:
        session: Active SQLAlchemy session.
        credential: The credential to delete.
    """
    session.execute(
        PasskeyCredential.__table__.delete().where(
            PasskeyCredential.id == credential.id
        )
    )
    session.flush()
