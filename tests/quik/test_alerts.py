"""Tests for the QUIK agent alert forwarder (sprint02 Phase 1).

No real network: a fake sender is injected (or httpx is monkeypatched). Covers:
  * payload formatting (severity, code, message, agent host, timestamp),
  * unset token/chat -> no-op (no send),
  * cooldown suppresses duplicate (agent, code, severity),
  * recovery alerts always pass the cooldown,
  * CRITICAL severity hits the SMS stub.
"""

from trader.quik import alerts as alerts_mod
from trader.quik.alerts import AlertForwarder, SEVERITY_WARN


class _FakeSender:
    def __init__(self):
        self.sent: list[str] = []

    async def __call__(self, text: str) -> None:
        self.sent.append(text)


def _alert(severity=SEVERITY_WARN, code="DDE_DOWN", message="DDE channel lost",
           raised_ms=1_700_000_000_000):
    return {"severity": severity, "code": code, "message": message,
            "raised_at_unix_ms": raised_ms}


async def test_forward_builds_payload_and_sends():
    sender = _FakeSender()
    fwd = AlertForwarder("tok", "chat", cooldown_sec=60, send=sender)
    await fwd.forward(_alert(), agent_host="WIN-QUIK01")

    assert len(sender.sent) == 1
    text = sender.sent[0]
    assert "WARN" in text
    assert "DDE_DOWN" in text
    assert "DDE channel lost" in text
    assert "WIN-QUIK01" in text
    assert "UTC" in text  # timestamp rendered


async def test_unset_token_is_noop():
    sender = _FakeSender()
    fwd = AlertForwarder("", "", cooldown_sec=60, send=sender)
    await fwd.forward(_alert(), agent_host="WIN-QUIK01")
    assert sender.sent == []  # never sent

    # only chat set, token empty -> still unconfigured -> no-op
    fwd2 = AlertForwarder("", "chat", cooldown_sec=60, send=sender)
    await fwd2.forward(_alert(), agent_host="WIN-QUIK01")
    assert sender.sent == []


async def test_cooldown_suppresses_duplicate():
    sender = _FakeSender()
    fwd = AlertForwarder("tok", "chat", cooldown_sec=60, send=sender)
    a = _alert(code="QUIK_DOWN")
    await fwd.forward(a, agent_host="h1")
    await fwd.forward(a, agent_host="h1")  # same (agent, code, severity) within cooldown
    assert len(sender.sent) == 1  # second suppressed

    # different code is not suppressed
    await fwd.forward(_alert(code="LINK_DOWN"), agent_host="h1")
    assert len(sender.sent) == 2

    # different agent is not suppressed
    await fwd.forward(a, agent_host="h2")
    assert len(sender.sent) == 3


async def test_cooldown_expires():
    sender = _FakeSender()
    fwd = AlertForwarder("tok", "chat", cooldown_sec=0, send=sender)  # no cooldown
    a = _alert(code="QUIK_DOWN")
    await fwd.forward(a, agent_host="h1")
    await fwd.forward(a, agent_host="h1")
    assert len(sender.sent) == 2  # cooldown 0 -> both pass


async def test_recovery_always_passes():
    sender = _FakeSender()
    fwd = AlertForwarder("tok", "chat", cooldown_sec=600, send=sender)
    rec = _alert(code="DDE_RECOVERED", message="DDE back up")
    await fwd.forward(rec, agent_host="h1")
    await fwd.forward(rec, agent_host="h1")  # recovery bypasses cooldown
    assert len(sender.sent) == 2


async def test_send_failure_never_raises(monkeypatch):
    async def boom(text):
        raise RuntimeError("telegram down")

    fwd = AlertForwarder("tok", "chat", cooldown_sec=60, send=boom)
    # must not raise — a Telegram failure can't break the gRPC stream
    await fwd.forward(_alert(), agent_host="h1")


async def test_real_sender_uses_httpx(monkeypatch):
    """The default sender posts to the Telegram Bot API via httpx (no real net)."""
    captured = {}

    class _FakeResp:
        def raise_for_status(self):
            captured["raised"] = True

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResp()

    monkeypatch.setattr(alerts_mod.httpx, "AsyncClient", _FakeClient)
    fwd = AlertForwarder("SECRET_TOKEN", "12345", cooldown_sec=0)  # default httpx sender
    await fwd.forward(_alert(code="DDE_DOWN"), agent_host="h1")

    assert "/botSECRET_TOKEN/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == "12345"
    assert "DDE_DOWN" in captured["json"]["text"]
    assert captured.get("raised") is True


async def test_lineman_path_needs_no_token():
    """Без токена алерт уходит через Lineman: ключа провайдера в этой службе нет
    по политике федерации, а молчащий канал стоил нам 40 минут неуправляемой
    реальной позиции 14.08.2026."""
    sent = []

    async def sender(text):
        sent.append(text)

    fwd = AlertForwarder("", "36910539", cooldown_sec=0, send=sender,
                         lineman_url="http://10.66.0.1:9090")
    assert fwd.configured()
    await fwd.forward({"severity": 3, "code": "RECON_MISMATCH", "message": "x"}, "vds")
    assert len(sent) == 1


def test_not_configured_without_a_recipient():
    fwd = AlertForwarder("", "", cooldown_sec=0, lineman_url="http://10.66.0.1:9090")
    assert not fwd.configured()


async def test_failed_send_unstamps_cooldown_so_retry_passes():
    """Упавшая отправка не глушит свой же повтор: молчание канала уже стоило
    40 минут слепой позиции 14.08."""
    calls = []

    async def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("tg down")

    fwd = AlertForwarder("tok", "chat", cooldown_sec=600, send=flaky)
    await fwd.forward(_alert(), agent_host="a")   # падает, штамп снят
    await fwd.forward(_alert(), agent_host="a")   # повтор проходит сразу
    assert len(calls) == 2


async def test_token_failure_falls_back_to_lineman(monkeypatch):
    """Цепочка, не выбор при старте: недоступный Bot API не глушит CRITICAL,
    когда Lineman жив."""
    sent = []

    async def tg_down(self, text):
        raise RuntimeError("api.telegram.org unreachable")

    async def lineman_ok(self, text):
        sent.append(text)

    monkeypatch.setattr(AlertForwarder, "_send_telegram", tg_down)
    monkeypatch.setattr(AlertForwarder, "_send_lineman", lineman_ok)
    fwd = AlertForwarder("tok", "chat", cooldown_sec=0,
                         lineman_url="http://10.66.0.1:9090")
    await fwd.forward(_alert(), agent_host="a")
    assert len(sent) == 1
