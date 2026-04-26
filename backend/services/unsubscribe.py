"""Bridge between unsubscribe tokens and notification-preference writes.

The public ``/api/v1/unsubscribe`` endpoint is the only caller. It
hands a raw token to :func:`unsubscribe_with_token`, which verifies
it, dispatches to the right write path on
:mod:`backend.services.notification_preferences`, and returns the
decoded token so the route handler can render a human-friendly
confirmation.

Token scopes route as follows:

* ``"all"`` → :func:`prefs_service.pause_all_emails` (preserves
  per-type flags via the JSONB snapshot, so a future "resume all"
  restores the user's choices).
* Anything else → :func:`prefs_service.update_preferences_for_user`
  with that one boolean column set to ``False``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services import email_tokens
from backend.services import notification_preferences as prefs_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def unsubscribe_with_token(
    session: Session, token: str
) -> email_tokens.UnsubscribeToken:
    """Decode an unsubscribe token and apply the corresponding change.

    Args:
        session: Active SQLAlchemy session passed through to the
            preference service.
        token: The opaque token from the unsubscribe link's query
            string.

    Returns:
        The decoded :class:`UnsubscribeToken` so the caller can render
        a confirmation message naming the affected scope.

    Raises:
        ValidationError: If the token is malformed, expired, or its
            signature does not verify.
    """
    decoded = email_tokens.verify_unsubscribe_token(token)

    if decoded.scope == "all":
        prefs_service.pause_all_emails(session, decoded.user_id)
    else:
        prefs_service.update_preferences_for_user(
            session, decoded.user_id, {decoded.scope: False}
        )
    return decoded
