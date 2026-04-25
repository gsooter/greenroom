"""Unit tests for :mod:`backend.services.admin`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.data.models.scraper import ScraperRunStatus
from backend.services import admin as admin_service


@dataclass
class _FakeRun:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    venue_slug: str = "black-cat"
    scraper_class: str = "BlackCatScraper"
    status: ScraperRunStatus = ScraperRunStatus.SUCCESS
    event_count: int = 42
    started_at: datetime = field(
        default_factory=lambda: datetime(2026, 4, 18, tzinfo=UTC)
    )
    finished_at: datetime | None = field(
        default_factory=lambda: datetime(2026, 4, 18, 0, 5, tzinfo=UTC)
    )
    duration_seconds: float = 5.0
    error_message: str | None = None
    metadata_json: dict[str, Any] | None = field(default_factory=lambda: {"k": "v"})


@dataclass
class _FakeConfig:
    venue_slug: str = "black-cat"
    display_name: str = "Black Cat"
    region: str = "DMV"
    city_slug: str = "washington-dc"
    scraper_class: str = "BlackCatScraper"
    enabled: bool = True


def test_list_scraper_runs_rejects_oversized_per_page() -> None:
    with pytest.raises(ValidationError):
        admin_service.list_scraper_runs(MagicMock(), per_page=101)


def test_list_scraper_runs_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        admin_service.list_scraper_runs(MagicMock(), status="weird")


def test_list_scraper_runs_forwards_enum_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(admin_service.runs_repo, "list_scraper_runs", fake_list)
    admin_service.list_scraper_runs(
        MagicMock(), venue_slug="x", status="success", page=2, per_page=10
    )
    assert captured["status"] is ScraperRunStatus.SUCCESS
    assert captured["venue_slug"] == "x"


def test_trigger_scraper_run_raises_when_venue_config_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_service, "get_venue_config", lambda _slug: None)
    with pytest.raises(NotFoundError):
        admin_service.trigger_scraper_run(MagicMock(), "ghost-venue")


def test_trigger_scraper_run_raises_when_venue_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        admin_service,
        "get_venue_config",
        lambda _slug: _FakeConfig(enabled=False),
    )
    with pytest.raises(NotFoundError):
        admin_service.trigger_scraper_run(MagicMock(), "black-cat")


def test_trigger_scraper_run_invokes_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _FakeConfig()
    monkeypatch.setattr(admin_service, "get_venue_config", lambda _slug: cfg)
    monkeypatch.setattr(
        admin_service,
        "run_scraper_for_venue",
        lambda _session, config: {"status": "success", "slug": config.venue_slug},
    )
    result = admin_service.trigger_scraper_run(MagicMock(), "black-cat")
    assert result["status"] == "success"
    assert result["slug"] == "black-cat"


def test_summarize_fleet_groups_by_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = [
        _FakeConfig(venue_slug="a", region="DMV"),
        _FakeConfig(venue_slug="b", region="DMV"),
        _FakeConfig(venue_slug="c", region="NYC"),
    ]
    monkeypatch.setattr(admin_service, "get_enabled_configs", lambda: configs)
    summary = admin_service.summarize_fleet()
    assert summary["enabled"] == 3
    assert summary["by_region"] == {"DMV": 2, "NYC": 1}
    assert [v["slug"] for v in summary["venues"]] == ["a", "b", "c"]


def test_serialize_scraper_run_renders_all_fields() -> None:
    run = _FakeRun()
    payload = admin_service.serialize_scraper_run(run)  # type: ignore[arg-type]
    assert payload["id"] == str(run.id)
    assert payload["status"] == "success"
    assert payload["event_count"] == 42
    assert payload["metadata"] == {"k": "v"}


def test_serialize_scraper_run_handles_null_metadata_and_finish() -> None:
    run = _FakeRun(finished_at=None, metadata_json=None)
    payload = admin_service.serialize_scraper_run(run)  # type: ignore[arg-type]
    assert payload["finished_at"] is None
    assert payload["metadata"] == {}


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


@dataclass
class _FakeConn:
    provider: Any = None


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "a@b.com"
    display_name: str | None = "Pat"
    is_active: bool = True
    city_id: uuid.UUID | None = None
    music_connections: list[Any] = field(default_factory=list)
    last_login_at: datetime | None = None
    onboarding_completed_at: datetime | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 4, 18, tzinfo=UTC)
    )


def test_list_users_rejects_oversized_per_page() -> None:
    with pytest.raises(ValidationError):
        admin_service.list_users(MagicMock(), per_page=101)


def test_list_users_rejects_invalid_is_active() -> None:
    with pytest.raises(ValidationError):
        admin_service.list_users(MagicMock(), is_active="yes")


def test_list_users_forwards_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(admin_service.users_repo, "list_users", fake_list)
    admin_service.list_users(
        MagicMock(), search="pat", is_active="false", page=2, per_page=10
    )
    assert captured["search"] == "pat"
    assert captured["is_active"] is False
    assert captured["page"] == 2
    assert captured["per_page"] == 10


def test_deactivate_user_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_service.users_repo, "get_user_by_id", lambda _s, _u: None)
    with pytest.raises(NotFoundError):
        admin_service.deactivate_user(MagicMock(), uuid.uuid4())


def test_deactivate_user_flips_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(is_active=True)
    monkeypatch.setattr(admin_service.users_repo, "get_user_by_id", lambda _s, _u: user)

    def fake_update(_s: Any, target: Any, **kw: Any) -> Any:
        for k, v in kw.items():
            setattr(target, k, v)
        return target

    monkeypatch.setattr(admin_service.users_repo, "update_user", fake_update)
    out = admin_service.deactivate_user(MagicMock(), user.id)
    assert out.is_active is False


def test_reactivate_user_flips_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(is_active=False)
    monkeypatch.setattr(admin_service.users_repo, "get_user_by_id", lambda _s, _u: user)

    def fake_update(_s: Any, target: Any, **kw: Any) -> Any:
        for k, v in kw.items():
            setattr(target, k, v)
        return target

    monkeypatch.setattr(admin_service.users_repo, "update_user", fake_update)
    out = admin_service.reactivate_user(MagicMock(), user.id)
    assert out.is_active is True


def test_delete_user_calls_repo_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(admin_service.users_repo, "get_user_by_id", lambda _s, _u: user)

    def fake_delete(_s: Any, target: Any) -> None:
        captured["target"] = target

    monkeypatch.setattr(admin_service.users_repo, "delete_user", fake_delete)
    admin_service.delete_user(MagicMock(), user.id)
    assert captured["target"] is user


def test_delete_user_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_service.users_repo, "get_user_by_id", lambda _s, _u: None)
    with pytest.raises(NotFoundError):
        admin_service.delete_user(MagicMock(), uuid.uuid4())


def test_serialize_user_summary_renders_connections() -> None:
    from backend.data.models.users import OAuthProvider

    user = _FakeUser(
        last_login_at=datetime(2026, 4, 18, tzinfo=UTC),
        music_connections=[_FakeConn(provider=OAuthProvider.SPOTIFY)],
    )
    payload = admin_service.serialize_user_summary(user)  # type: ignore[arg-type]
    assert payload["id"] == str(user.id)
    assert payload["is_active"] is True
    assert payload["music_connections"] == ["spotify"]
    assert payload["last_login_at"] == "2026-04-18T00:00:00+00:00"


# ---------------------------------------------------------------------------
# send_test_alert
# ---------------------------------------------------------------------------


def test_send_test_alert_invokes_notifier_with_no_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test alert must skip cooldown so operators can press the button often."""
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr(admin_service, "send_alert", alert_mock)

    fake_settings = MagicMock(
        slack_webhook_url="https://hooks.slack/abc",
        alert_email="ops@example.com",
        resend_api_key="re_live_123",
    )
    monkeypatch.setattr(admin_service, "get_settings", lambda: fake_settings)

    result = admin_service.send_test_alert(MagicMock())

    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["alert_key"] is None
    assert kwargs["severity"] == "info"
    assert result["delivered"] is True
    assert result["slack_configured"] is True
    assert result["email_configured"] is True
    assert result["title"] == "Greenroom alert pipeline test"


def test_send_test_alert_flags_unconfigured_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sentinel placeholders count as 'not configured' in the response."""
    monkeypatch.setattr(admin_service, "send_alert", MagicMock(return_value=True))
    fake_settings = MagicMock(
        slack_webhook_url="x",
        alert_email="x@x.com",
        resend_api_key="x",
    )
    monkeypatch.setattr(admin_service, "get_settings", lambda: fake_settings)

    result = admin_service.send_test_alert(MagicMock())
    assert result["slack_configured"] is False
    assert result["email_configured"] is False
