"""Unit tests for the push service (configuration + send fan-out)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.services import push as push_service


def test_push_payload_to_json_includes_optional_tag() -> None:
    payload = push_service.PushPayload(
        title="Hello",
        body="World",
        url="https://example.com/",
        tag="t1",
    )
    raw = payload.to_json()
    assert '"title":"Hello"' in raw
    assert '"tag":"t1"' in raw
    assert '"url":"https://example.com/"' in raw


def test_push_payload_to_json_omits_tag_when_absent() -> None:
    payload = push_service.PushPayload(title="t", body="b", url="/")
    assert '"tag"' not in payload.to_json()


def test_is_configured_false_when_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
    monkeypatch.setenv("VAPID_SUBJECT", "")
    assert push_service.is_configured() is False


def test_is_configured_true_when_all_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BCp")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "abc")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:noreply@greenroom.example")
    assert push_service.is_configured() is True


def test_send_to_user_short_circuits_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
    monkeypatch.setenv("VAPID_SUBJECT", "")
    result = push_service.send_to_user(
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        payload=push_service.PushPayload(title="t", body="b", url="/"),
    )
    assert result.skipped_no_vapid is True
    assert result.attempted == 0


def test_send_to_user_returns_zero_when_no_subscriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BCp")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "abc")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:noreply@greenroom.example")

    monkeypatch.setattr(
        "backend.data.repositories.push_subscriptions.list_active_for_user",
        lambda _session, _user_id: [],
    )
    result = push_service.send_to_user(
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        payload=push_service.PushPayload(title="t", body="b", url="/"),
    )
    assert result.attempted == 0
    assert result.succeeded == 0
    assert result.skipped_no_vapid is False


def test_send_to_user_disables_subscription_on_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BCp")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "abc")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:noreply@greenroom.example")

    fake_sub = type(
        "FakeSub",
        (),
        {
            "id": uuid.uuid4(),
            "endpoint": "https://push.example/abc",
            "p256dh_key": "p",
            "auth_key": "a",
            "failure_count": 0,
            "disabled_at": None,
        },
    )()
    disabled_calls: list[Any] = []

    monkeypatch.setattr(
        "backend.data.repositories.push_subscriptions.list_active_for_user",
        lambda _session, _user_id: [fake_sub],
    )
    monkeypatch.setattr(
        "backend.data.repositories.push_subscriptions.record_success",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.data.repositories.push_subscriptions.record_failure",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.data.repositories.push_subscriptions.disable_subscription",
        lambda _session, sub: disabled_calls.append(sub),
    )
    monkeypatch.setattr(push_service, "_send_one", lambda *a, **kw: "permanent")

    result = push_service.send_to_user(
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        payload=push_service.PushPayload(title="t", body="b", url="/"),
    )
    assert result.attempted == 1
    assert result.disabled == 1
    assert result.succeeded == 0
    assert disabled_calls == [fake_sub]
