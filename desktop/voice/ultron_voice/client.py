"""Backend client for the chat WebSocket (shared/protocols/chat_ws.md)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect


class BackendError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class UltronClient:
    def __init__(self, url: str, token: str | None) -> None:
        self._url = url
        self._token = token
        self._ws: ClientConnection | None = None
        self.conversation_id: str | None = None

    async def __aenter__(self) -> UltronClient:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        self._ws = await connect(self._url, additional_headers=headers, max_size=1 << 20)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws:
            await self._ws.close()

    async def turn(
        self, message: str, *, privacy: str = "default", modality: str = "voice"
    ) -> AsyncIterator[dict]:
        """Send one chat and yield server events until `end` (or raise on `error`)."""
        assert self._ws is not None
        payload = {"type": "chat", "message": message, "privacy": privacy, "modality": modality}
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id
        await self._ws.send(json.dumps(payload))
        async for raw in self._ws:
            event = json.loads(raw)
            if event["type"] == "amended":
                continue  # acknowledgement of an earlier turn's late cancel; not part of this turn
            if event["type"] == "error":
                if event.get("conversation_id"):
                    self.conversation_id = event["conversation_id"]
                raise BackendError(event.get("code", "error"), event.get("message", ""))
            yield event
            if event["type"] == "end":
                self.conversation_id = event.get("conversation_id") or self.conversation_id
                return

    def _http_base(self) -> str:
        parts = urlsplit(self._url)
        return f"{'https' if parts.scheme == 'wss' else 'http'}://{parts.netloc}"

    async def erase_conversation(self) -> str:
        """Permanently delete the current conversation on the backend and forget its id.

        Returns "erased" or "nothing" (no conversation yet). Raises BackendError if the backend
        refuses or cannot be reached; the id is kept in that case so the caller can say so."""
        cid = self.conversation_id
        if cid is None:
            return "nothing"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.delete(
                    f"{self._http_base()}/v1/conversations/{cid}", headers=headers
                )
        except httpx.HTTPError as exc:
            raise BackendError("unreachable", type(exc).__name__) from exc
        if r.status_code not in (204, 404):  # 404: it was already gone
            raise BackendError(f"http_{r.status_code}", "the backend refused to erase it")
        self.conversation_id = None
        return "erased"

    async def cancel(self, spoken: str | None = None, message_id: int | None = None) -> None:
        """Interrupt the turn in progress, or (with message_id) amend a turn that already ended."""
        assert self._ws is not None
        frame: dict = {"type": "cancel", "spoken": spoken}
        if message_id is not None:
            frame["message_id"] = message_id
        await self._ws.send(json.dumps(frame))
