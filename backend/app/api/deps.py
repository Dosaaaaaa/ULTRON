from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, Request

from app.core.errors import Forbidden, Unauthorized
from app.core.services import Services
from app.devices.registry import Who

LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _hostname(netloc: str) -> str:
    """'host:port' -> 'host' (IPv6 literals keep their brackets)."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def origin_allowed(headers) -> bool:
    """Browsers attach an Origin to every WebSocket upgrade and do not apply the same-origin policy
    to sockets, so any web page open on this machine could otherwise drive the API. Native clients
    (voice client, desktop app) send no Origin. A page is allowed only if it is served by this
    backend itself (Origin names the same host the request was addressed to)."""
    origin = headers.get("origin")
    if origin is None:
        return True
    scheme, sep, netloc = origin.partition("://")
    return bool(sep) and netloc != "" and netloc.lower() == (headers.get("host") or "").lower()


def host_allowed(settings, headers) -> bool:
    """With authentication switched off, only a loopback Host name is served. A page on a
    rebound domain (DNS rebinding) would otherwise be same-origin with the open API."""
    if not settings.auth_disabled:
        return True
    return _hostname(headers.get("host") or "") in LOOPBACK_NAMES


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


async def authenticate(services: Services, authorization: str) -> Who | None:
    """Who is calling: the owner token, a paired device's own token, or None."""
    settings = services.settings
    if settings.auth_disabled:
        return Who("anonymous-loopback", True)
    scheme, _, supplied = authorization.partition(" ")
    supplied = supplied.strip()
    if scheme.lower() != "bearer" or not supplied:
        return None
    expected = settings.api_token.get_secret_value()  # type: ignore[union-attr]
    if hmac.compare_digest(supplied.encode(), expected.encode()):
        return Who("tok-" + hashlib.sha256(expected.encode()).hexdigest()[:12], True)
    if services.devices is not None:
        return await services.devices.verify(supplied)
    return None


async def _caller(request: Request, services: Services) -> Who:
    if not host_allowed(services.settings, request.headers):
        raise Forbidden("This host name is not served while authentication is disabled.")
    who = await authenticate(services, request.headers.get("authorization", ""))
    if who is None:
        raise Unauthorized("Missing or invalid credentials.")
    return who


async def require_auth(request: Request, services: ServicesDep) -> str:
    """The OWNER only. A paired device's token is refused here (403) and works only on the few
    endpoints that declare a capability it holds (see `device_can`)."""
    who = await _caller(request, services)
    if not who.owner:
        raise Forbidden("This device is not allowed to use this endpoint.")
    return who.key


def device_can(cap: str):
    """Dependency for endpoints a paired device may use: the owner, or a device holding `cap`.
    Rate limited per caller."""

    async def dependency(request: Request, services: ServicesDep) -> Who:
        who = await _caller(request, services)
        if not who.can(cap):
            raise Forbidden(f"This device was not paired with the '{cap}' capability.")
        services.limiter.check(who.key)
        return who

    return dependency


Principal = Annotated[str, Depends(require_auth)]


async def rate_limited(services: ServicesDep, principal: Principal) -> str:
    services.limiter.check(principal)
    return principal


Limited = Annotated[str, Depends(rate_limited)]
