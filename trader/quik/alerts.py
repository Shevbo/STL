"""STL-side alert forwarder for the QUIK agent link (sprint02 Phase 1).

When the QuikAgentLink.Session stream delivers an ``Alert``, this forwarder
pushes a formatted message to Telegram via the Bot API ``sendMessage``. CRITICAL
alerts are additionally marked for SMS-dubbing through the Garden-manager
gateway — but Phase 1 only logs a stub (no real gateway call; see TODO).

Read-only domain. No order code. Secrets are read by ENV NAME only (via
Settings); a token value is never hardcoded or logged.
"""

from __future__ import annotations

import time

import httpx
import structlog

log = structlog.get_logger()

# Mirrors proto AlertSeverity (shectory.quik.v1).
SEVERITY_UNSPECIFIED = 0
SEVERITY_INFO = 1
SEVERITY_WARN = 2
SEVERITY_CRITICAL = 3

_SEVERITY_LABEL = {
    SEVERITY_UNSPECIFIED: "UNSPEC",
    SEVERITY_INFO: "INFO",
    SEVERITY_WARN: "WARN",
    SEVERITY_CRITICAL: "CRITICAL",
}

_TG_API = "https://api.telegram.org/bot{token}/sendMessage"


def _is_recovery(code: str) -> bool:
    """A recovery alert (channel came back). Always bypasses the cooldown so a
    'back up' notice is never swallowed by a still-warm duplicate window."""
    c = (code or "").lower()
    return "recover" in c or c.endswith("_up") or c.endswith("_ok")


def _format_alert(alert: dict, agent_host: str) -> str:
    severity = _SEVERITY_LABEL.get(int(alert.get("severity", 0)), "UNSPEC")
    code = alert.get("code", "")
    message = alert.get("message", "")
    raised_ms = int(alert.get("raised_at_unix_ms", 0) or 0)
    if raised_ms:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(raised_ms / 1000)) + " UTC"
    else:
        ts = "n/a"
    return (
        f"[QUIK {severity}] {code}\n"
        f"{message}\n"
        f"agent: {agent_host or 'unknown'}\n"
        f"at: {ts}"
    )


def sms_stub(alert: dict, agent_host: str) -> None:
    """Phase 1 SMS-dubbing placeholder for CRITICAL alerts.

    Does NOT call any real gateway. Phase 2 wires the Garden-manager SMS gateway
    here, reading its endpoint/credentials by ENV NAME only.

    TODO(Phase 2): send via the Garden-manager SMS gateway. Read gateway URL +
    auth from env NAMES (e.g. GARDEN_MANAGER_SMS_URL / GARDEN_MANAGER_SMS_TOKEN)
    through Settings; never hardcode a value. Reference: reference_commission_model
    note and the Garden-manager gateway service.
    """
    log.warning(
        "quik.alert.sms_stub",
        msg="would SMS via garden-manager gateway",
        code=alert.get("code", ""),
        agent=agent_host or "unknown",
    )


class AlertForwarder:
    """Async Telegram forwarder with per-(agent, code, severity) cooldown.

    Construct once (e.g. in the gRPC server) and call ``forward(...)`` per Alert.
    A missing token/chat id makes ``forward`` a logged no-op. Telegram failures
    are swallowed (logged) so they can never break the gRPC stream.
    """

    def __init__(
        self,
        tg_token: str,
        tg_chat_id: str,
        cooldown_sec: int = 60,
        send: "callable | None" = None,
    ) -> None:
        self._token = tg_token
        self._chat_id = tg_chat_id
        self._cooldown_sec = max(0, cooldown_sec)
        # Injectable sender for tests; default posts to the Telegram Bot API.
        self._send = send or self._send_telegram
        # (agent, code, severity) -> last-sent monotonic seconds.
        self._last_sent: dict[tuple[str, str, int], float] = {}

    def configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def _suppressed(self, key: tuple[str, str, int], code: str) -> bool:
        if _is_recovery(code):
            return False
        now = time.monotonic()
        last = self._last_sent.get(key)
        if last is not None and (now - last) < self._cooldown_sec:
            return True
        self._last_sent[key] = now
        return False

    async def forward(self, alert: dict, agent_host: str) -> None:
        """Forward one Alert. Non-blocking-safe: never raises."""
        try:
            severity = int(alert.get("severity", 0))
            code = alert.get("code", "")

            # CRITICAL: mark for SMS-dubbing (Phase 1 stub only).
            if severity == SEVERITY_CRITICAL:
                sms_stub(alert, agent_host)

            if not self.configured():
                log.warning(
                    "quik.alert.tg_unconfigured",
                    msg="QUIK_ALERT_TG_TOKEN / QUIK_ALERT_TG_CHAT_ID unset; alert not sent",
                    code=code,
                )
                return

            key = (agent_host or "", code, severity)
            if self._suppressed(key, code):
                log.info("quik.alert.suppressed_cooldown", code=code, agent=agent_host)
                return

            text = _format_alert(alert, agent_host)
            await self._send(text)
            log.info("quik.alert.forwarded", code=code, severity=severity, agent=agent_host)
        except Exception as exc:  # noqa: BLE001 — never let an alert break the stream
            log.warning("quik.alert.forward_failed", error=str(exc), code=alert.get("code", ""))

    async def _send_telegram(self, text: str) -> None:
        url = _TG_API.format(token=self._token)
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.post(url, json={"chat_id": self._chat_id, "text": text})
            r.raise_for_status()
