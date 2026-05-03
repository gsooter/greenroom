"""Browser Web Push subscription records.

When a user grants notification permission inside an installed PWA,
the browser produces a :class:`PushSubscription` payload — endpoint,
public key, auth secret — that the backend stores against their user
id. Sending a push is then a matter of looking up every active
subscription for the recipient and POSTing the encrypted payload to
each endpoint.

A user can have several subscriptions: one per device, one per
browser profile. The unique constraint is on
``(user_id, endpoint)`` rather than just ``endpoint`` so a shared
device with two Greenroom accounts can keep distinct subscriptions
even when the underlying browser endpoint is the same.

Failure tracking lives on the row itself: when a send to an endpoint
fails, the dispatcher bumps ``failure_count`` and stamps
``last_failure_at``. After
:data:`backend.services.push.MAX_CONSECUTIVE_FAILURES` consecutive
failures the subscription is treated as dead and skipped — most
"endpoint gone" responses (HTTP 404 / 410) zero out the row in a
single pass via :func:`disable_subscription`.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.users import User


class PushSubscription(TimestampMixin, Base):
    """A single browser's Web Push subscription registered to a user.

    Attributes:
        id: Primary key.
        user_id: Foreign key to the owning user. ``ON DELETE CASCADE``
            removes subscriptions when an account is deleted.
        endpoint: Push service URL the browser handed us at subscribe
            time. Each browser/device picks its own — one subscription
            per ``(user, endpoint)`` pair.
        p256dh_key: Browser's P-256 ECDH public key (base64url),
            consumed by the Web Push encryption header.
        auth_key: 16-byte browser-side auth secret (base64url) used as
            the HKDF salt for payload encryption.
        user_agent: Optional ``User-Agent`` snapshot taken at subscribe
            time. Helps the admin dashboard explain "why is this user
            getting two pushes" without contacting the user.
        last_successful_send_at: Most recent send that returned 2xx.
            Read by ops to spot dormant subscriptions before they
            silently rot.
        last_failure_at: Most recent send that returned non-2xx.
        failure_count: Consecutive failures since the last success.
            Reset to 0 after any successful send. The dispatcher
            disables the row once this hits a configured ceiling.
        disabled_at: When the row was permanently disabled (404/410
            from the push service, or the failure ceiling tripped).
            Disabled rows are never sent to.
        user: Backref to :class:`User`.
    """

    __tablename__ = "push_subscriptions"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "endpoint",
            name="uq_push_sub_user_endpoint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh_key: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_key: Mapped[str] = mapped_column(String(60), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_successful_send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        """Return a string representation for log lines.

        Returns:
            ``<PushSubscription user=... endpoint=...>``.
        """
        return f"<PushSubscription user={self.user_id} endpoint={self.endpoint[:40]}…>"
