"""NEW: the HTTP erase call, its failure modes, and the marker lines the desktop window reads."""

from __future__ import annotations

import httpx
import pytest

from ultron_voice import client as client_mod
from ultron_voice.cli import Console
from ultron_voice.client import BackendError, UltronClient

TOKEN = "t" * 32


def with_transport(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        client_mod.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_erase_deletes_the_right_url_with_the_token_in_a_header(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        return httpx.Response(204)

    with_transport(monkeypatch, handler)
    c = UltronClient("ws://127.0.0.1:8000/v1/ws/chat", TOKEN)
    c.conversation_id = "abc-123"
    assert await c.erase_conversation() == "erased"
    assert seen == [("DELETE", "http://127.0.0.1:8000/v1/conversations/abc-123", f"Bearer {TOKEN}")]
    assert c.conversation_id is None
    assert TOKEN not in seen[0][1]  # never in the URL


async def test_wss_uses_https(monkeypatch):
    seen = []
    with_transport(monkeypatch, lambda r: seen.append(str(r.url)) or httpx.Response(204))
    c = UltronClient("wss://ultron.example:9000/v1/ws/chat", TOKEN)
    c.conversation_id = "x"
    await c.erase_conversation()
    assert seen == ["https://ultron.example:9000/v1/conversations/x"]


async def test_nothing_to_erase_makes_no_request(monkeypatch):
    with_transport(monkeypatch, lambda r: pytest.fail("no request expected"))
    c = UltronClient("ws://127.0.0.1:8000/v1/ws/chat", TOKEN)
    assert await c.erase_conversation() == "nothing"


async def test_already_gone_counts_as_erased(monkeypatch):
    with_transport(monkeypatch, lambda r: httpx.Response(404))
    c = UltronClient("ws://127.0.0.1:8000/v1/ws/chat", TOKEN)
    c.conversation_id = "x"
    assert await c.erase_conversation() == "erased" and c.conversation_id is None


@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_a_refusal_raises_and_keeps_the_id(monkeypatch, status):
    with_transport(monkeypatch, lambda r: httpx.Response(status))
    c = UltronClient("ws://127.0.0.1:8000/v1/ws/chat", TOKEN)
    c.conversation_id = "x"
    with pytest.raises(BackendError) as exc:
        await c.erase_conversation()
    assert exc.value.code == f"http_{status}" and c.conversation_id == "x"


async def test_an_unreachable_backend_raises_and_keeps_the_id(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused")

    with_transport(monkeypatch, refuse)
    c = UltronClient("ws://127.0.0.1:8000/v1/ws/chat", TOKEN)
    c.conversation_id = "x"
    with pytest.raises(BackendError) as exc:
        await c.erase_conversation()
    assert exc.value.code == "unreachable" and c.conversation_id == "x"


def test_console_prints_the_exact_markers_the_desktop_reads(capsys):
    console = Console(show_text=False)
    console("new_conversation", erased=True, had_chat=True)
    console("new_conversation", erased=False, had_chat=False)
    console("new_conversation", erased=False, reason="unreachable")
    out = capsys.readouterr().out
    assert "[NEW CONVERSATION] previous chat erased" in out
    assert "[NEW CONVERSATION] nothing to erase yet" in out
    assert "[NEW CONVERSATION] previous chat NOT erased (unreachable); it is still stored" in out


def test_console_prints_the_reply_text_for_the_window_captions_unless_transcripts_are_hidden(
    capsys,
):
    shown = Console(show_text=True)
    shown("heard", text="what time is it")
    shown("turn_done", reply="It is   five\nto nine.", total_ms=1200)
    out = capsys.readouterr().out
    assert "  you: what time is it" in out
    assert "  reply: It is five to nine." in out  # whitespace collapsed to one line

    hidden = Console(show_text=False)
    hidden("heard", text="private")
    hidden("turn_done", reply="secret answer", total_ms=1)
    out = capsys.readouterr().out
    assert "private" not in out and "secret answer" not in out  # --hide-transcripts hides both

    shown("turn_done", reply="x" * 2000, total_ms=1)
    long_line = next(line for line in capsys.readouterr().out.splitlines() if "reply:" in line)
    assert len(long_line) <= 700
