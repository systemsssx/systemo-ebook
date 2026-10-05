# -*- coding: utf-8 -*-
"""HTTP layer: retries, multi-address (IPv4/IPv6) fallback, gzip, redirects.

Standard library only, so the tool runs anywhere without pip installs.

Why so defensive: while building this tool the machine showed flaky egress —
the same host resolved to both IPv4 and IPv6, and a single-address attempt
randomly died with an SSL/TLS error while another address succeeded seconds
later. urllib only ever tries the first address returned by getaddrinfo, so we
resolve every address ourselves and walk them until one answers.

Set `NOVELMETA_IPV4_ONLY=1` for the opposite problem — a host that resolves to
IPv6 but has no working IPv6 route (see `_ipv4_only()` below).
"""

from __future__ import annotations

import gzip
import http.client
import os
import random
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": DEFAULT_UA,
    "Accept": "application/json, text/plain, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}

MAX_REDIRECTS = 5


class FetchError(Exception):
    """Raised when every attempt for a request failed."""

    def __init__(self, url: str, attempts: Sequence[str]):
        self.url = url
        self.attempts = list(attempts)
        super().__init__(f"all {len(self.attempts)} attempt(s) failed for {url}: "
                         + " | ".join(self.attempts[-3:]))


@dataclass
class Response:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        ctype = (self.headers.get("Content-Type") or "").lower()
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")

    def json(self):
        import json

        return json.loads(self.text)


def _decode_body(raw: bytes, encoding: str | None) -> bytes:
    enc = (encoding or "").lower().strip()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _ipv4_only() -> bool:
    """Opt-in IPv4-only resolution (`NOVELMETA_IPV4_ONLY=1`).

    Some networks hand out an AAAA record for a host that has no working IPv6
    route, so every request burns one full per-address timeout before falling
    back to IPv4 — the same "IPv6 black hole" the EasyPub app hit on Baidu.
    Measured with this very HTTP layer on the app's machine (2026-09-29):

        weread  8.40s -> 0.24s     jjwxc  0.30s     fanqie  0.41s

    EasyPub sets the variable in the child-process env; the standalone CLI
    leaves it unset and keeps walking both families. Read at call time so
    setting it before or after import both work.
    """
    return (os.environ.get("NOVELMETA_IPV4_ONLY") or "").strip().lower() not in (
        "", "0", "false", "no", "off")


def _addresses(host: str, port: int) -> list[tuple[int, tuple]]:
    """Every resolved address for host, keeping the OS preference order."""
    try:
        infos = socket.getaddrinfo(
            host, port, socket.AF_INET if _ipv4_only() else 0, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    seen, out = set(), []
    for family, _type, _proto, _canon, sockaddr in infos:
        key = (family, sockaddr[0])
        if key not in seen:
            seen.add(key)
            out.append((family, sockaddr))
    return out


class _ForcedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to one specific resolved address."""

    def __init__(self, host: str, sockaddr: tuple, family: int, **kw):
        super().__init__(host, **kw)
        self._sockaddr = sockaddr
        self._family = family

    def connect(self) -> None:
        # NOTE: socket.create_connection() only accepts a 2-tuple (host, port) and
        # therefore silently rejects IPv6 sockaddrs (4-tuple) with a ValueError.
        # Build the socket from the resolved family instead, so v6 works too.
        self.sock = socket.socket(self._family, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._sockaddr)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tunnel_host:
            self._tunnel()


class _ForcedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection pinned to one resolved address (TLS SNI keeps the name)."""

    def __init__(self, host: str, sockaddr: tuple, family: int, **kw):
        self._sockaddr = sockaddr
        self._family = family
        super().__init__(host, **kw)

    def connect(self) -> None:
        self.sock = socket.socket(self._family, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._sockaddr)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tunnel_host:
            self._tunnel()
        ctx = self._context or ssl.create_default_context()
        self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)


def _split_url(url: str) -> tuple[str, str, int, str]:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    scheme = parts.scheme or "https"
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, port, path


def fetch_once(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 12.0,
    per_address_timeout: float | None = None,
    verify_tls: bool = False,
    method: str = "GET",
    form: Mapping[str, str] | None = None,
) -> Response:
    """One logical request: try every resolved address until one succeeds.

    Redirects are followed, still walking all addresses of each new host.
    """
    merged = dict(DEFAULT_HEADERS)
    if headers:
        # An empty value means "remove this header", not "send it empty": an empty
        # `Referer:` is a different request than no Referer, and 番茄's image CDN
        # answers 403 to it (that bug shipped once, hence the explicit handling).
        for key, value in headers.items():
            if value:
                merged[key] = value
            else:
                merged.pop(key, None)

    # ★ 2026-09-29：支持 POST 表单。久久小说网的搜索**只有 POST 通**
    #   （GET 返回 0 结果）。form 给出时自动 urlencode + 设 Content-Type；
    #   正文只放第一跳，跟随重定向后降级成 GET（POST 重放是错的）。
    body_bytes: bytes | None = None
    if form is not None:
        from urllib.parse import urlencode
        body_bytes = urlencode({k: str(v) for k, v in form.items()}).encode("utf-8")
        merged.setdefault("Content-Type", "application/x-www-form-urlencoded")
    cur_method, cur_body = method.upper(), body_bytes

    current = url
    errors: list[str] = []
    for _hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = _split_url(current)
        if not host:
            raise FetchError(url, [f"bad url: {current}"])

        addrs = _addresses(host, port)
        if not addrs:
            errors.append(f"{host}: DNS failure")
            raise FetchError(url, errors)

        resp: Response | None = None
        for family, sockaddr in addrs:
            # Prefer shorter per-address timeouts so a black-holed address
            # cannot eat the whole budget.
            addr_timeout = per_address_timeout or min(timeout, 8.0)
            try:
                if scheme == "https":
                    ctx = ssl.create_default_context()
                    if not verify_tls:
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                    conn = _ForcedHTTPSConnection(
                        host, sockaddr, family, port=port, timeout=addr_timeout, context=ctx
                    )
                else:
                    conn = _ForcedHTTPConnection(
                        host, sockaddr, family, port=port, timeout=addr_timeout
                    )
                try:
                    # ★ 保持 GET 的调用形状不变（不传 body=None），
                    #   否则任何包装了连接对象的代码/测试都会被打穿（实测踩过：
                    #   test_cover.py 的 FakeConnection 不接受 body 参数）。
                    if cur_body is None:
                        conn.request(cur_method, path, headers=merged)
                    else:
                        conn.request(cur_method, path, body=cur_body, headers=merged)
                    raw = conn.getresponse()
                    body = _decode_body(raw.read(), raw.getheader("Content-Encoding"))
                    resp = Response(
                        url=current,
                        status=raw.status,
                        headers={k: v for k, v in raw.getheaders()},
                        body=body,
                    )
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001 - try the next address
                fam = "v6" if family == socket.AF_INET6 else "v4"
                errors.append(f"{host}[{fam} {sockaddr[0]}]: {type(exc).__name__}: {exc}")
                continue
            break  # first address that answered wins

        if resp is None:
            raise FetchError(url, errors)

        location = resp.headers.get("Location") or resp.headers.get("location")
        if resp.status in (301, 302, 303, 307, 308) and location:
            from urllib.parse import urljoin

            cur_method, cur_body = "GET", None   # ★ 重定向后不重放 POST
            current = urljoin(current, location)
            continue
        return resp

    raise FetchError(url, errors + ["too many redirects"])


def fetch(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 12.0,
    tries: int = 3,
    backoff: float = 0.8,
    jitter: float = 0.35,
    verify_tls: bool = False,
    method: str = "GET",
    form: Mapping[str, str] | None = None,
) -> Response:
    """fetch_once with retries and exponential backoff + jitter."""
    errors: list[str] = []
    last_exc: Exception | None = None
    for attempt in range(max(1, tries)):
        try:
            return fetch_once(url, headers=headers, timeout=timeout, verify_tls=verify_tls,
                              method=method, form=form)
        except FetchError as exc:
            last_exc = exc
            errors.extend(exc.attempts)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            errors.append(f"{type(exc).__name__}: {exc}")
        if attempt + 1 < tries:
            time.sleep(backoff * (2**attempt) + random.uniform(0, jitter))
    raise FetchError(url, errors) from last_exc


def fetch_many(
    requests: Iterable[tuple[str, Mapping[str, str] | None]],
    *,
    timeout: float = 12.0,
    tries: int = 3,
    workers: int = 6,
) -> list[Response | Exception]:
    """Fetch several URLs concurrently, preserving input order."""
    from concurrent.futures import ThreadPoolExecutor

    items = list(requests)

    def one(item: tuple[str, Mapping[str, str] | None]):
        url, hdrs = item
        try:
            return fetch(url, headers=hdrs, timeout=timeout, tries=tries)
        except Exception as exc:  # noqa: BLE001 - returned to the caller
            return exc

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, items))
