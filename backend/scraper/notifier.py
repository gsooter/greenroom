"""Slack and email alert notifications for scraper failures.

Sends alerts via Slack webhook (primary) and email (fallback) when
scrapers fail or produce anomalous results. Callers may pass an
``alert_key`` and ``cooldown_hours`` to dedup repeat notifications —
without that, a single broken venue would post on every nightly run
and on every manual ``/admin`` re-trigger.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import requests

from backend.core.config import get_settings
from backend.core.database import get_session_factory
from backend.core.exceptions import AppError
from backend.core.logging import get_logger
from backend.data.repositories import scraper_alerts as alerts_repo
from backend.services.email import send_email

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

DEFAULT_COOLDOWN_HOURS = 6.0


def send_alert(
    *,
    title: str,
    message: str,
    severity: str = "warning",
    details: dict[str, Any] | None = None,
    alert_key: str | None = None,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    session: Session | None = None,
) -> bool:
    """Send an alert via Slack and email, with optional cooldown dedup.

    Attempts Slack first. If Slack fails, falls back to email. Logs all
    alert attempts regardless of delivery success. When ``alert_key``
    is supplied, the notifier consults ``scraper_alerts`` and skips
    delivery if a previous send for the same key falls inside
    ``cooldown_hours``.

    The dedup row is recorded after the delivery attempt, so a Slack
    or email outage still consumes a slot in the cooldown — preventing
    a runaway loop of failed sends. Operators can find the suppressed
    alerts in the application logs.

    Args:
        title: Short alert title.
        message: Detailed alert message.
        severity: Alert severity level (``"info"``, ``"warning"``,
            ``"error"``).
        details: Optional additional context as a dictionary.
        alert_key: Stable, human-readable identifier for this alert
            (e.g. ``"zero_results:black-cat"``). Pass ``None`` to opt
            out of dedup entirely.
        cooldown_hours: Suppression window in hours. Ignored when
            ``alert_key`` is ``None``. Non-positive values disable
            suppression even when a key is supplied.
        session: Optional SQLAlchemy session for the dedup tracking
            row. When omitted, the notifier opens its own short-lived
            session via :func:`get_session_factory`. Test code passes
            an explicit session; production callers usually let it
            default.

    Returns:
        True when a delivery attempt was made, False when the call was
        suppressed by an active cooldown.
    """
    if alert_key is not None and _is_suppressed(
        alert_key=alert_key,
        cooldown_hours=cooldown_hours,
        session=session,
    ):
        logger.info(
            "Alert '%s' suppressed (within %.1fh cooldown).",
            alert_key,
            cooldown_hours,
        )
        return False

    logger.warning("SCRAPER ALERT [%s] %s: %s", severity, title, message)

    slack_sent = _send_slack_alert(
        title=title,
        message=message,
        severity=severity,
        details=details,
    )

    if not slack_sent:
        logger.error("Slack alert failed, falling back to email for: %s", title)
        _send_email_alert(
            title=title,
            message=message,
            severity=severity,
            details=details,
        )

    if alert_key is not None:
        _record_attempt(
            alert_key=alert_key,
            severity=severity,
            title=title,
            message=message,
            details=details,
            session=session,
        )

    return True


def _is_suppressed(
    *,
    alert_key: str,
    cooldown_hours: float,
    session: Session | None,
) -> bool:
    """Check the dedup table for a suppressing prior send.

    Args:
        alert_key: Stable, human-readable alert identifier.
        cooldown_hours: Suppression window in hours.
        session: Optional caller-supplied session. When ``None``, a
            short-lived session is opened from the global factory.

    Returns:
        True when a prior delivery falls inside the cooldown window.
        False on any error reading the table — alerting fails open.
    """
    try:
        if session is not None:
            return alerts_repo.should_suppress(session, alert_key, cooldown_hours)
        with _scoped_session() as scoped:
            return alerts_repo.should_suppress(scoped, alert_key, cooldown_hours)
    except Exception:
        logger.exception(
            "Failed to read scraper_alerts for '%s'; failing open.", alert_key
        )
        return False


def _record_attempt(
    *,
    alert_key: str,
    severity: str,
    title: str,
    message: str,
    details: dict[str, Any] | None,
    session: Session | None,
) -> None:
    """Persist the dedup row for a just-attempted delivery.

    Errors are swallowed so a broken alert table never breaks the
    actual alert flow.

    Args:
        alert_key: Stable, human-readable alert identifier.
        severity: Severity recorded for this send.
        title: Title delivered to Slack/email.
        message: Body delivered to Slack/email.
        details: Optional structured detail payload.
        session: Optional caller-supplied session. When ``None``, a
            short-lived session is opened and committed independently.
    """
    try:
        if session is not None:
            alerts_repo.record_alert(
                session,
                alert_key=alert_key,
                severity=severity,
                title=title,
                message=message,
                details=details,
            )
            return
        with _scoped_session(commit=True) as scoped:
            alerts_repo.record_alert(
                scoped,
                alert_key=alert_key,
                severity=severity,
                title=title,
                message=message,
                details=details,
            )
    except Exception:
        logger.exception("Failed to record scraper_alerts row for '%s'.", alert_key)


class _ScopedSession:
    """Context manager that owns a short-lived SQLAlchemy session.

    Used by the notifier when no caller-supplied session is available.
    Commits on clean exit when ``commit`` is True; always rolls back
    and closes on error.
    """

    def __init__(self, *, commit: bool = False) -> None:
        """Initialize the scoped session wrapper.

        Args:
            commit: When True, commit the session on clean exit.
        """
        self._commit = commit
        self._session: Session | None = None

    def __enter__(self) -> Session:
        """Open the session.

        Returns:
            The newly created SQLAlchemy session.
        """
        factory = get_session_factory()
        self._session = factory()
        return self._session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        """Close the session, committing or rolling back as appropriate.

        Args:
            exc_type: Type of any in-flight exception.
            exc: The in-flight exception instance, if any.
            tb: Traceback of the in-flight exception, if any.
        """
        assert self._session is not None
        try:
            if exc is None and self._commit:
                self._session.commit()
            else:
                self._session.rollback()
        finally:
            self._session.close()


def _scoped_session(*, commit: bool = False) -> _ScopedSession:
    """Return a fresh short-lived session context for notifier use.

    Args:
        commit: When True, the resulting context manager commits on
            clean exit.

    Returns:
        A context manager that yields a SQLAlchemy session.
    """
    return _ScopedSession(commit=commit)


def _send_slack_alert(
    *,
    title: str,
    message: str,
    severity: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """Send an alert to Slack via incoming webhook.

    Args:
        title: Short alert title.
        message: Detailed alert message.
        severity: Alert severity level.
        details: Optional additional context.

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    settings = get_settings()
    webhook_url = settings.slack_webhook_url

    if not webhook_url or webhook_url == "x":
        logger.debug("Slack webhook not configured, skipping.")
        return False

    color_map = {
        "info": "#36a64f",
        "warning": "#ff9900",
        "error": "#ff0000",
    }

    fields = []
    if details:
        for key, value in details.items():
            fields.append(
                {
                    "title": key,
                    "value": str(value),
                    "short": True,
                }
            )

    payload = {
        "attachments": [
            {
                "color": color_map.get(severity, "#ff9900"),
                "title": f"🔔 Scraper Alert: {title}",
                "text": message,
                "fields": fields,
            }
        ]
    }

    try:
        response = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("Failed to send Slack alert: %s", e)
        return False


def _send_email_alert(
    *,
    title: str,
    message: str,
    severity: str,
    details: dict[str, Any] | None = None,
) -> bool:
    """Send an alert email via Resend as a fallback.

    Args:
        title: Short alert title.
        message: Detailed alert message.
        severity: Alert severity level.
        details: Optional additional context.

    Returns:
        True if the email was sent successfully, False otherwise.
    """
    settings = get_settings()

    if not settings.alert_email or settings.alert_email == "x@x.com":
        logger.debug("Alert email not configured, skipping.")
        return False

    if not settings.resend_api_key or settings.resend_api_key == "x":
        logger.debug("Resend not configured, skipping email alert.")
        return False

    body_parts = [f"Severity: {severity}", "", message]
    if details:
        body_parts.append("")
        body_parts.append("Details:")
        for key, value in details.items():
            body_parts.append(f"  {key}: {value}")
    text_body = "\n".join(body_parts)
    html_body = "<pre>" + text_body.replace("<", "&lt;").replace(">", "&gt;") + "</pre>"

    try:
        send_email(
            to=settings.alert_email,
            subject=f"[Greenroom Scraper] {title}",
            html_body=html_body,
            text_body=text_body,
        )
        return True
    except AppError as e:
        logger.error("Failed to send email alert: %s", e)
        return False
