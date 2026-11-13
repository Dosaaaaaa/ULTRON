"""Which web addresses may be shown to, or fetched on behalf of, the operator.

A hostile page or search result can name an address on the operator's own machine or network
(http://127.0.0.1:8000/..., a router, the cloud metadata address 169.254.169.254). Anything that
later fetches such an address from this machine would be attacking the operator from the inside.
`is_public_http_url` is the address check; `resolves_public` additionally checks what a name really
points to, and is the one a future fetch tool must use (a public name can resolve to a private
address, "DNS rebinding"). Nothing here fetches anything.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

_BAD_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP address `host` denotes, including the odd spellings browsers accept (127.1,
    0x7f000001, 2130706433, 0177.0.0.1); None if it is an ordinary name."""
    host = host.strip("[]")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:  # inet_aton accepts the legacy forms
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None and not is_public_ip(ip.sixtofour):
            return False
    return ip.is_global and not ip.is_multicast


def is_public_http_url(url: str) -> bool:
    """True only for http(s) URLs with a public host, no embedded credentials and no odd port."""
    if not isinstance(url, str) or len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return False
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or port == 0:
        return False
    ip = _address(host)
    if ip is not None:
        return is_public_ip(ip)
    if "." not in host or host == "localhost" or host.endswith(_BAD_SUFFIXES):
        return False  # single-label and local-only names
    return True


Resolver = Callable[[str], Awaitable[list[str]]]


async def _system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [i[4][0] for i in infos]


async def resolves_public(url: str, resolver: Resolver = _system_resolver) -> bool:
    """`is_public_http_url`, and every address the name resolves to is public as well."""
    if not is_public_http_url(url):
        return False
    host = (urlsplit(url).hostname or "").rstrip(".")
    if _address(host) is not None:
        return True  # already an IP literal, checked above
    try:
        addresses = await resolver(host)
    except OSError:
        return False
    if not addresses:
        return False
    return all((ip := _address(a)) is not None and is_public_ip(ip) for a in addresses)
