"""V2.17 round 5: AlertDispatcher webhook tests.

Contract:
1. Dispatches only NEW alerts (dedup by (dep_id, alert_type, date)).
2. Re-dispatches if webhook POST fails (rolls back dedup).
3. Multiple alerts fold into a single webhook POST per cycle.
4. Per-template payload shapes are correct.
5. Bad template raises ValueError at construction.
6. Missing webhook_url rejected at construction.
7. from_env() returns None when env var absent; builds valid
   dispatcher when set; invalid format → None + warning, not crash.
8. dispatch_new returns count dispatched; swallows all exceptions.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest


from ez.live.alert_dispatcher import AlertDispatcher, from_env


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------

def test_construction_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="webhook_url"):
        AlertDispatcher(webhook_url="", template="plain")


def test_construction_rejects_bad_template() -> None:
    with pytest.raises(ValueError, match="template"):
        AlertDispatcher(webhook_url="http://x", template="bogus")


def test_construction_accepts_all_valid_templates() -> None:
    for t in AlertDispatcher.VALID_FORMATS:
        d = AlertDispatcher(webhook_url="http://x", template=t)
        assert d.template == t


# ---------------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------------

_SAMPLE_ALERTS = [
    {"deployment_id": "dep-A", "alert_type": "high_drawdown",
     "message": "MyDeploy: max drawdown -30% exceeds threshold -20%"},
    {"deployment_id": "dep-A", "alert_type": "consecutive_loss_days",
     "message": "MyDeploy: 6 consecutive loss days"},
]


def test_plain_payload_is_json_list() -> None:
    d = AlertDispatcher(webhook_url="http://x", template="plain")
    payload = d._build_payload(_SAMPLE_ALERTS)
    assert "alerts" in payload
    assert isinstance(payload["alerts"], list)
    assert len(payload["alerts"]) == 2
    assert payload["alerts"][0]["deployment_id"] == "dep-A"
    assert payload["alerts"][0]["alert_type"] == "high_drawdown"
    assert "ts" in payload["alerts"][0]


def test_dingtalk_payload_shape() -> None:
    d = AlertDispatcher(webhook_url="http://x", template="dingtalk")
    payload = d._build_payload(_SAMPLE_ALERTS)
    assert payload["msgtype"] == "text"
    assert "content" in payload["text"]
    content = payload["text"]["content"]
    assert "2 个告警" in content
    assert "high_drawdown" in content
    assert "consecutive_loss_days" in content


def test_wecom_payload_shape() -> None:
    """WeCom uses the same shape as DingTalk."""
    d = AlertDispatcher(webhook_url="http://x", template="wecom")
    payload = d._build_payload(_SAMPLE_ALERTS)
    assert payload["msgtype"] == "text"
    assert "content" in payload["text"]


def test_slack_payload_shape() -> None:
    d = AlertDispatcher(webhook_url="http://x", template="slack")
    payload = d._build_payload(_SAMPLE_ALERTS)
    assert "text" in payload
    assert isinstance(payload["text"], str)
    assert "2 个告警" in payload["text"]


def test_dingtalk_business_error_raises() -> None:
    d = AlertDispatcher(webhook_url="http://x", template="dingtalk")
    with pytest.raises(RuntimeError, match="errcode=310000"):
        d._raise_on_business_error(_FakeResponse({"errcode": 310000, "errmsg": "invalid token"}))


def test_wecom_business_success_does_not_raise() -> None:
    d = AlertDispatcher(webhook_url="http://x", template="wecom")
    d._raise_on_business_error(_FakeResponse({"errcode": 0, "errmsg": "ok"}))


# ---------------------------------------------------------------------------
# Dedup behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedup_drops_second_identical_dispatch() -> None:
    """Second dispatch of same (dep_id, alert_type) same day returns 0."""
    d = AlertDispatcher(webhook_url="http://x", template="plain")
    d._post = AsyncMock()  # type: ignore[method-assign]

    n1 = await d.dispatch_new(_SAMPLE_ALERTS)
    assert n1 == 2
    assert d._post.call_count == 1

    # Re-dispatch same alerts — all dedup'd
    n2 = await d.dispatch_new(_SAMPLE_ALERTS)
    assert n2 == 0
    # No additional POST made
    assert d._post.call_count == 1


@pytest.mark.asyncio
async def test_dedup_lets_new_alert_type_through() -> None:
    """If the same deployment hits a NEW alert type today, it goes
    through even though a prior type was already dispatched."""
    d = AlertDispatcher(webhook_url="http://x", template="plain")
    d._post = AsyncMock()  # type: ignore[method-assign]

    await d.dispatch_new([_SAMPLE_ALERTS[0]])  # dispatch drawdown

    # Later in day: new alert_type "slow_execution" arrives
    await d.dispatch_new([{
        "deployment_id": "dep-A",
        "alert_type": "slow_execution",
        "message": "tick took 60s",
    }])
    assert d._post.call_count == 2


@pytest.mark.asyncio
async def test_post_failure_rolls_back_dedup_for_retry() -> None:
    """If the webhook POST fails, dedup is rolled back so next cycle
    re-attempts — user doesn't miss a real alert because of a
    transient webhook outage."""
    d = AlertDispatcher(webhook_url="http://x", template="plain")
    call = {"n": 0}

    async def failing_then_ok(payload):
        call["n"] += 1
        if call["n"] == 1:
            raise RuntimeError("webhook temporarily down")

    d._post = failing_then_ok  # type: ignore[method-assign]

    n1 = await d.dispatch_new(_SAMPLE_ALERTS)
    assert n1 == 0  # dispatch reports 0 sent
    # First call failed → seen set rolled back

    n2 = await d.dispatch_new(_SAMPLE_ALERTS)
    assert n2 == 2  # second attempt succeeded
    assert call["n"] == 2


@pytest.mark.asyncio
async def test_empty_alerts_list_is_noop() -> None:
    d = AlertDispatcher(webhook_url="http://x", template="plain")
    d._post = AsyncMock()  # type: ignore[method-assign]
    assert await d.dispatch_new([]) == 0
    assert d._post.call_count == 0


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_new_swallows_all_exceptions() -> None:
    """dispatch_new must never raise — caller relies on it for loop
    safety. Connection refused, DNS, timeout, unexpected payload
    errors all stay inside."""
    d = AlertDispatcher(webhook_url="http://x", template="plain")

    async def boom(payload):
        raise ConnectionError("DNS fail")

    d._post = boom  # type: ignore[method-assign]

    # Does not raise
    n = await d.dispatch_new(_SAMPLE_ALERTS)
    assert n == 0


# ---------------------------------------------------------------------------
# from_env()
# ---------------------------------------------------------------------------

def test_from_env_without_url_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("EZ_ALERT_WEBHOOK_URL", raising=False)
    assert from_env() is None


def test_from_env_empty_url_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_URL", "")
    assert from_env() is None


def test_from_env_with_url_builds_dispatcher(monkeypatch) -> None:
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_FORMAT", "dingtalk")
    d = from_env()
    assert d is not None
    assert d.webhook_url == "https://example.com/hook"
    assert d.template == "dingtalk"


def test_from_env_bad_format_falls_back_to_none(monkeypatch, caplog) -> None:
    """Invalid template should not crash startup — return None + warn."""
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_FORMAT", "invalid_format")
    with caplog.at_level("WARNING"):
        d = from_env()
    assert d is None


def test_from_env_bad_timeout_falls_back_to_default(monkeypatch) -> None:
    """Invalid timeout string must not crash — use 10s default."""
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("EZ_ALERT_WEBHOOK_TIMEOUT_S", "not a number")
    d = from_env()
    assert d is not None
    assert d.timeout_s == 10.0
