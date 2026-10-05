# -*- coding: utf-8 -*-
"""Cover image fetching: pick the best variant, download, verify it is an image."""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable, Sequence

from .net import FetchError, fetch
from .sources import cover_variants

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}

_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Cover CDNs that check the Referer. Sending the matching site referer is what
# makes these downloads work; sending an *empty* Referer header is what made
# 番茄's covers fail with HTTP 403 before this map existed.
_REFERER_BY_HOST = {
    "byteimg.com": "https://fanqienovel.com/",
    "fqnovelpic.com": "https://fanqienovel.com/",
    "myqcloud.com": "https://weread.qq.com/",
    "weread.qq.com": "https://weread.qq.com/",
    "bilinovel.com": "https://www.bilinovel.com/",
    "linovelib.com": "https://www.bilinovel.com/",
    "jjwxc.net": "https://www.jjwxc.net/",
}


def referer_for(url: str) -> str:
    """Best-guess Referer for a cover URL, or "" when the host needs none."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for suffix, referer in _REFERER_BY_HOST.items():
        if host == suffix or host.endswith("." + suffix):
            return referer
    return ""


class CoverError(RuntimeError):
    pass


@dataclass
class CoverResult:
    path: str
    url: str
    bytes: int
    content_type: str
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict:
        d = {"path": self.path, "url": self.url, "bytes": self.bytes,
             "content_type": self.content_type}
        if self.width and self.height:
            d["size"] = f"{self.width}x{self.height}"
        return d


def _sniff_ext(data: bytes, content_type: str) -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if b"ftypavif" in data[:32] or b"ftypavis" in data[:32]:
        return ".avif"
    ctype = (content_type or "").split(";")[0].strip().lower()
    return _EXT_BY_TYPE.get(ctype, "")


def image_size(data: bytes) -> tuple[int, int]:
    """Minimal JPEG/PNG/GIF/WebP dimension probe (no Pillow dependency)."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data.startswith(b"GIF") and len(data) > 10:
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data[:2] == b"\xff\xd8":
            i, n = 2, len(data)
            while i < n - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return (int.from_bytes(data[i + 7:i + 9], "big"),
                            int.from_bytes(data[i + 5:i + 7], "big"))
                i += 2 + seg_len
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) > 30:
            fmt = data[12:16]
            if fmt == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if fmt == b"VP8 ":
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return w, h
            if fmt == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except Exception:  # noqa: BLE001 - dimensions are a nicety, never fatal
        pass
    return 0, 0


def safe_filename(name: str, *, max_length: int = 80) -> str:
    """Windows-safe filename from a book title."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "cover"


def download_cover(
    url: str,
    dest_dir: str,
    *,
    stem: str = "cover",
    variants: Sequence[str] | None = None,
    referer: str | None = None,
    min_bytes: int = 512,
    timeout: float = 15.0,
    tries: int = 3,
) -> CoverResult:
    """Download the largest working variant of a cover into dest_dir.

    Tries every size variant in order and keeps the first response that really is
    an image (content-type / magic bytes), so a dead `b_` URL degrades to the
    original file instead of writing an error page to disk.

    `referer` overrides the host-derived Referer; passing an empty string disables
    the header entirely (which is what hot-link-protected CDNs want).
    """
    if not url:
        raise CoverError("没有可用的封面地址")
    candidates: Iterable[str] = variants if variants else cover_variants(url)
    candidates = list(dict.fromkeys([c for c in candidates if c])) or [url]

    errors: list[str] = []
    for candidate in candidates:
        headers = {"User-Agent": _UA}
        ref = referer_for(candidate) if referer is None else referer
        if ref:
            headers["Referer"] = ref
        try:
            resp = fetch(candidate, headers=headers, timeout=timeout, tries=tries)
        except FetchError as exc:
            errors.append(f"{candidate} -> {exc}")
            continue
        if resp.status != 200:
            errors.append(f"{candidate} -> HTTP {resp.status}")
            continue
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = _sniff_ext(resp.body, ctype)
        if not ext or len(resp.body) < min_bytes:
            errors.append(f"{candidate} -> 非图片响应 (ctype={ctype or '?'}, {len(resp.body)}B)")
            continue

        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{safe_filename(stem)}{ext}")
        with open(path, "wb") as fh:
            fh.write(resp.body)
        width, height = image_size(resp.body)
        return CoverResult(path=path, url=candidate, bytes=len(resp.body),
                           content_type=ctype or ext.lstrip("."), width=width, height=height)

    raise CoverError("封面下载失败: " + " | ".join(errors[-3:]))
