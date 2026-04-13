"""V2.17 round 5: paper-trading alert webhook dispatcher.

Closes the last "fire and forget" gap. The Monitor detects 5 alert
conditions (consecutive loss days / high drawdown / slow execution /
consecutive errors / inactivity) but the only consumer is the UI —
a user running unattended paper trading has no way to know a deployment
hit a risk threshold unless they actively open the dashboard.

This module pushes alerts to a user-configured webhook. Supports four
payload formats:
- `plain`   : generic `{alerts: [{deployment_id, alert_type, message, ts}]}`
- `dingtalk`: DingTalk group bot (`{msgtype:text, text:{content:...}}`)
- `wecom`   : WeCom group bot (same shape as DingTalk for text)
- `slack`   : `{text: "..."}`

Dedup: (deployment_id, alert_type, YYYY-MM-DD) tuple kept in-memory so
a running deployment won't spam the same alert every hour. Cleared
naturally when process restarts (acceptable since a fresh process
should re-alert once if the condition persists).

Failure isolation: httpx errors are logged, not raised. The auto-tick
loop must never die because a webhook is down.

Configuration (env):
- `EZ_ALERT_WEBHOOK_URL`: enable dispatch to this URL
- `EZ_ALERT_WEBHOOK_FORMAT`: plain (default) / dingtalk / wecom / slack
- `EZ_ALERT_WEBHOOK_TIMEOUT_S`: POST timeout (default 10s)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


# Dedup key format: (deployment_id, alert_type, date_iso)
_DedupKey = tuple[str, str, str]


class AlertDispatcher:
    """Webhook dispatcher with per-day dedup and graceful failure."""

    VALID_FORMATS = ("plain", "dingtalk", "wecom", "slack")

    def __init__(
        self,
        webhook_url: str,
        template: str = "plain",
        timeout_s: float = 10.0,
    ):
        if not webhook_url:
            raise ValueError("webhook_url is required")
        if template not in self.VALID_FORMATS:
            raise ValueError(
                f"template must be one of {self.VALID_FORMATS}, got {template!r}"
            )
        self.webhook_url = webhook_url
        self.template = template
        self.timeout_s = timeout_s
        self._seen: set[_DedupKey] = set()

    # ------------------------------------------------------------------
    # Payload shaping
    # ------------------------------------------------------------------

    def _build_payload(self, alerts: list[dict[str, Any]]) -> dict:
        """Render `alerts` into the configured webhook payload shape."""
        today = date.today().isoformat()

        if self.template == "plain":
            return {
                "alerts": [
                    {
                        "deployment_id": a.get("deployment_id"),
                        "alert_type": a.get("alert_type"),
                        "message": a.get("message", ""),
                        "ts": today,
                    }
                    for a in alerts
                ],
            }

        # Text-based formats: fold all alerts into a single message
        lines = [f"[ez-trading] {len(alerts)} 个告警 ({today})"]
        for a in alerts:
            lines.append(
                f"  • {a.get('alert_type')}: {a.get('message', '')}"
            )
        text = "\n".join(lines)

        if self.template in ("dingtalk", "wecom"):
            return {"msgtype": "text", "text": {"content": text}}
        # slack
        return {"text": text}

    # ------------------------------------------------------------------
    # Dedup + dispatch
    # ------------------------------------------------------------------

    def _filter_new(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return only alerts not yet seen today. Mutates self._seen."""
        today = date.today().isoformat()
        fresh: list[dict[str, Any]] = []
        for a in alerts:
            key: _DedupKey = (
                str(a.get("deployment_id", "")),
                str(a.get("alert_type", "")),
                today,
            )
            if key in self._seen:
                continue
            self._seen.add(key)
            fresh.append(a)
        return fresh

    async def dispatch_new(self, alerts: list[dict[str, Any]]) -> int:
        """Dispatch alerts not yet sent today. Returns count dispatched.

        Safe: any exception is logged and suppressed. Caller (auto-tick
        loop) must never fail because of webhook issues.
        """
        if not alerts:
            return 0
        fresh = self._filter_new(alerts)
        if not fresh:
            return 0
        payload = self._build_payload(fresh)
        try:
            await self._post(payload)
            logger.info(
                "Dispatched %d alert(s) to webhook (format=%s)",
                len(fresh), self.template,
            )
            return len(fresh)
        except Exception as e:
            logger.warning(
                "Alert webhook dispatch failed (%s alerts lost): %s",
                len(fresh), e,
            )
            # Roll back dedup so we re-try next cycle
            for a in fresh:
                key = (
                    str(a.get("deployment_id", "")),
                    str(a.get("alert_type", "")),
                    date.today().isoformat(),
                )
                self._seen.discard(key)
            return 0

    async def _post(self, payload: dict) -> None:
        """Async HTTP POST. Separate for test override."""
        import httpx
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(self.webhook_url, json=payload)
            resp.raise_for_status()


def from_env() -> AlertDispatcher | None:
    """Build dispatcher from env; return None if not configured.

    Called once at startup by the app lifespan. No env = feature off =
    previous behaviour (alerts visible only in UI).
    """
    import os
    url = os.environ.get("EZ_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return None
    fmt = os.environ.get("EZ_ALERT_WEBHOOK_FORMAT", "plain").strip() or "plain"
    try:
        timeout = float(os.environ.get("EZ_ALERT_WEBHOOK_TIMEOUT_S", "10"))
    except ValueError:
        timeout = 10.0
    try:
        return AlertDispatcher(webhook_url=url, template=fmt, timeout_s=timeout)
    except ValueError as e:
        logger.warning("Alert dispatcher config invalid: %s — alerts disabled", e)
        return None
