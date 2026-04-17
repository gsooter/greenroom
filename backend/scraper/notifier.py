"""Slack and email alert notifications for scraper failures.

Sends alerts via Slack webhook (primary) and email (fallback)
when scrapers fail or produce anomalous results.
"""

import json
from typing import Any

import requests

from backend.core.config import get_settings
from backend.core.logging import get_logger

logger = get_logger(__name__)


def send_alert(
    *,
    title: str,
    message: str,
    severity: str = "warning",
    details: dict[str, Any] | None = None,
) -> None:
    """Send an alert via Slack and email.

    Attempts Slack first. If Slack fails, falls back to email.
    Logs all alert attempts regardless of delivery success.

    Args:
        title: Short alert title.
        message: Detailed alert message.
        severity: Alert severity level ("info", "warning", "error").
        details: Optional additional context as a dictionary.
    """
    logger.warning("SCRAPER ALERT [%s] %s: %s", severity, title, message)

    slack_sent = _send_slack_alert(
        title=title,
        message=message,
        severity=severity,
        details=details,
    )

    if not slack_sent:
        logger.error(
            "Slack alert failed, falling back to email for: %s", title
        )
        _send_email_alert(
            title=title,
            message=message,
            severity=severity,
            details=details,
        )


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
            fields.append({
                "title": key,
                "value": str(value),
                "short": True,
            })

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
    """Send an alert email via SendGrid as a fallback.

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

    if not settings.sendgrid_api_key or settings.sendgrid_api_key == "x":
        logger.debug("SendGrid not configured, skipping email alert.")
        return False

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Content, Email, Mail, To

        body_parts = [f"Severity: {severity}", "", message]
        if details:
            body_parts.append("")
            body_parts.append("Details:")
            for key, value in details.items():
                body_parts.append(f"  {key}: {value}")

        mail = Mail(
            from_email=Email(settings.sendgrid_from_email),
            to_emails=To(settings.alert_email),
            subject=f"[Greenroom Scraper] {title}",
            plain_text_content=Content(
                "text/plain", "\n".join(body_parts)
            ),
        )

        sg = SendGridAPIClient(settings.sendgrid_api_key)
        response = sg.send(mail)
        return 200 <= response.status_code < 300
    except Exception as e:
        logger.error("Failed to send email alert: %s", e)
        return False
