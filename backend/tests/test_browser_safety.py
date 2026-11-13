"""Review findings: a web page in the operator's browser must not be able to drive the API, and a
`local_only` routing policy must keep web search off as well."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from tests.conftest import AUTH, make_settings
from tests.fakes import StreamingFake

URL = "/v1/ws/chat"


def client(tmp_path, **settings):
    app = create_app(make_settings(tmp_path, **settings), local=StreamingFake("local"))
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_a_browser_page_from_another_origin_cannot_open_the_chat_socket(tmp_path):
    with client(tmp_path) as c:
        for origin in ("https://evil.example", "http://127.0.0.1:9999", "null"):
            with pytest.raises(WebSocketDisconnect) as info:
                host = {"Host": "127.0.0.1:8000"}
                with c.websocket_connect(URL, headers={**AUTH, **host, "Origin": origin}):
                    pass
            assert info.value.code == 1008, origin


def test_native_clients_without_an_origin_header_and_same_origin_pages_still_connect(tmp_path):
    with client(tmp_path) as c:
        with c.websocket_connect(URL, headers=AUTH) as ws:  # the voice client / desktop app
            ws.send_json({"type": "chat", "message": "hi"})
            assert ws.receive_json()["type"] in ("start", "delta", "end")
        same = {"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"}
        with c.websocket_connect(URL, headers={**AUTH, **same}):
            pass  # the backend's own /docs page (the test client always says Host: testserver)


def test_with_auth_disabled_a_rebound_hostname_is_refused(tmp_path):
    # DNS rebinding: the page's origin and the Host header both name the attacker's domain.
    with client(tmp_path, auth_disabled=True, host="127.0.0.1") as c:
        r = c.get("/v1/conversations", headers={"Host": "evil.example:8000"})
        assert r.status_code == 403
        assert c.get("/v1/conversations").status_code == 200  # a normal loopback request
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                URL, headers={"Host": "evil.example:8000", "Origin": "http://evil.example:8000"}
            ):
                pass


def test_a_local_only_routing_policy_also_keeps_web_search_off(tmp_path):
    kw = dict(web_search_provider="searxng", web_search_url="http://127.0.0.1:8888")
    with client(tmp_path, **kw) as c:
        assert c.app.state.services.web is not None  # control: it is on when cloud is allowed
    with client(tmp_path, routing_policy="local_only", **kw) as c:
        assert c.app.state.services.web is None
