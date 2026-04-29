"""Sentry initialization for the Flask app and Celery workers.

Both entry points (``backend/app.py`` and ``backend/celery_app.py``) call
:func:`init_sentry` at boot. When ``SENTRY_DSN`` is empty — the dev
default — initialization is a no-op so contributors don't need a
Sentry account to run the app.

The integrations list is shared across web and worker because
``sentry-sdk[flask,celery,sqlalchemy]`` registers the right handlers
based on what's actually imported at runtime; the only divergence is
that the web process additionally enables Flask's request hooks.
"""

from __future__ import annotations

from typing import Literal

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from backend.core.config import get_settings

ProcessKind = Literal["web", "worker"]


def init_sentry(process: ProcessKind) -> bool:
    """Initialize Sentry for the given process kind.

    Args:
        process: ``"web"`` for the Flask app, ``"worker"`` for Celery.
            Both share the same DSN and environment; the label is
            written to ``server_name`` so prod-side filters can split
            request errors from worker errors.

    Returns:
        True when the SDK was initialized, False when the DSN was empty
        (dev default) and Sentry stays a no-op.
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        return False

    integrations: list[FlaskIntegration | CeleryIntegration | SqlalchemyIntegration] = [
        SqlalchemyIntegration()
    ]
    if process == "web":
        integrations.append(FlaskIntegration())
    else:
        integrations.append(CeleryIntegration())

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=integrations,
        server_name=f"greenroom-{process}",
    )
    return True
