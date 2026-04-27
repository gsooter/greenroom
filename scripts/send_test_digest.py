"""One-off: send the weekly digest to a single user, by email.

Bypasses Celery and the dispatcher's schedule guards so we can fire
a real email without waiting for the top-of-hour beat. The cap and
idempotency guards inside ``send_weekly_digest_to_user`` still apply,
so re-running this against the same recipient inside 6 days is a
no-op until you clear the row in ``email_digest_log``.

Usage:
    python scripts/send_test_digest.py garrett.sooter@gmail.com
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from backend.core.database import get_session_factory
from backend.data.models.users import User
from backend.services import notifications


def main(email: str) -> int:
    """Look up a user by email and trigger the weekly digest send.

    Args:
        email: Address to look up. Must already exist as a User row.

    Returns:
        ``0`` on a successful send, ``1`` on a no-op (guard tripped
        or no events to send), ``2`` on missing user.
    """
    factory = get_session_factory()
    with factory() as session:
        user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None:
            print(f"NO USER with email={email}")
            return 2

        print(f"User: id={user.id} display_name={user.display_name} city_id={user.city_id}")
        sent = notifications.send_weekly_digest_to_user(session, user.id)
        if sent:
            session.commit()
            print(f"SENT to {email}")
            return 0
        session.rollback()
        print(f"NOT SENT (guard short-circuited) — to={email}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/send_test_digest.py <email>")
        sys.exit(64)
    sys.exit(main(sys.argv[1]))
