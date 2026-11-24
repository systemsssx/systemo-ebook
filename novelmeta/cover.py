# -*- coding: utf-8 -*-
"""Cover image fetching: pick the best variant, download, verify it is an image."""

from __future__ import annotations

import io
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Sequence

from .net import FetchError, fetch
from .sources import cover_variants

try:
    from PIL import Image as _PILImage
except Exception:  # noqa: BLE001
    _PILImage = None

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
    "yuewen.com": "https://www.qidian.com/",
    "qidian.com": "https://www.qidian.com/",
    "doubanio.com": "https://book.douban.com/",
    "bilinovel.com": "https://www.bilinovel.com/",
    "linovelib.com": "https://www.bilinovel.com/",
    "readpai.com": "https://www.bilinovel.com/",
    "jjwxc.net": "https://www.jjwxc.net/",
    "baidu.com": "https://image.baidu.com/",
    "bdimg.com": "https://image.baidu.com/",
    "sogoucdn.com": "https://pic.sogou.com/",
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
    if _PILImage is not None:
        try:
            with _PILImage.open(io.BytesIO(data)) as im:
                return int(im.size[0]), int(im.size[1])
        except Exception:  # noqa: BLE001
            pass
    return 0, 0


def compute_dhash(data: bytes, hash_size: int = 8) -> int | None:
    """Compute a 64-bit difference hash (dHash) for same-artwork comparison."""
    if _PILImage is None or not data:
        return None
    try:
        with _PILImage.open(io.BytesIO(data)) as im:
            resample = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS", _PILImage.LANCZOS)
            gray = im.convert("L").resize((hash_size + 1, hash_size), resample)
            px = gray.tobytes()
        bits = 0
        stride = hash_size + 1
        for row in range(hash_size):
            base = row * stride
            for col in range(hash_size):
                bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
        return bits
    except Exception:  # noqa: BLE001
        return None


def hamming_distance(h1: int, h2: int) -> int:
    return int(h1 ^ h2).bit_count()


def _host_family(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.endswith("myqcloud.com") or host.endswith("weread.qq.com"):
        return "weread"
    if host.endswith("yuewen.com") or host.endswith("qidian.com"):
        return "yuewen"
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_placeholder_cover(url: str, body: bytes, w: int, h: int) -> bool:
    """Reject known site blank/placeholder images."""
    if not body or w <= 0 or h <= 0:
        return True
    # 阅文/起点默认空白封面是 ~6.9KB 的 180x240 纯底图
    if "yuewen.com" in url and w <= 180 and h <= 240 and len(body) < 8000:
        return True
    return False


def safe_filename(name: str, *, max_length: int = 80) -> str:
    """Windows-safe filename from a book title."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "cover"


@dataclass
class _FetchedCover:
    url: str
    body: bytes
    ctype: str
    ext: str
    width: int
    height: int
    dhash: int | None
    native: bool


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
    extra_urls: Sequence[str] | None = None,
    same_image_max_dist: int = 10,
) -> CoverResult:
    """Download candidates and select the highest-clarity version of the SAME cover artwork.

    1. Expands `variants` (e.g. WeRead `t9_` + Yuewen `/0`, Qidian `/0`) and optional
       `extra_urls` (other sources for the same book / search results).
    2. Establishes an authentic native anchor image on the book's own host family.
    3. Verifies cross-host candidates are the EXACT SAME artwork using `dHash`
       perceptual hashing (Hamming distance <= `same_image_max_dist` & aspect ratio match).
    4. Picks the verified image with maximum `(width * height, len(body))`.
    """
    if not url:
        raise CoverError("没有可用的封面地址")
    base_list: list[str] = list(variants) if variants else cover_variants(url)
    base_list.extend(extra_urls or ())
    base_list.append(url)
    all_cands = list(dict.fromkeys([c.strip() for c in base_list if c and c.strip()])) or [url]

    anchor_family = _host_family(url)
    native_cands = [c for c in all_cands if _host_family(c) == anchor_family]
    cross_cands = [c for c in all_cands if _host_family(c) != anchor_family]

    # Pick high-res native contenders first (avoid wasting requests on tiny s_/m_/b_ if t9_ works)
    if anchor_family == "weread":
        top_native = [c for c in native_cands if "/t9_" in c or "/outpic/" in c or "/weread/cover/" in c]
        if not top_native:
            top_native = native_cands[:2]
        fallback_native = [c for c in native_cands if c not in top_native]
    elif anchor_family == "yuewen":
        top_native = native_cands[:3]
        fallback_native = native_cands[3:]
    else:
        top_native = native_cands[:2]
        fallback_native = native_cands[2:]

    errors: list[str] = []

    def _fetch_one(cand: str, req_tries: int, is_native: bool) -> _FetchedCover | None:
        headers = {"User-Agent": _UA}
        ref = referer_for(cand) if referer is None else referer
        if ref:
            headers["Referer"] = ref
        try:
            resp = fetch(cand, headers=headers, timeout=min(timeout, 10.0), tries=req_tries)
        except FetchError as exc:
            errors.append(f"{cand} -> {exc}")
            return None
        if resp.status != 200:
            errors.append(f"{cand} -> HTTP {resp.status}")
            return None
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = _sniff_ext(resp.body, ctype)
        if not ext or ext == ".gif" or len(resp.body) < min_bytes:
            errors.append(f"{cand} -> 非有效位图响应 (ctype={ctype or '?'}, {len(resp.body)}B)")
            return None
        w, h = image_size(resp.body)
        if w <= 0 or h <= 0 or _is_placeholder_cover(cand, resp.body, w, h):
            errors.append(f"{cand} -> 占位图或无尺寸 ({w}x{h}, {len(resp.body)}B)")
            return None
        return _FetchedCover(
            url=cand,
            body=resp.body,
            ctype=ctype or ext.lstrip("."),
            ext=ext,
            width=w,
            height=h,
            dhash=compute_dhash(resp.body),
            native=is_native,
        )

    fetched: list[_FetchedCover] = []
    batch_jobs: list[tuple[str, int, bool]] = (
        [(c, min(2, tries), True) for c in top_native]
        + [(c, 1, False) for c in cross_cands[:10]]
    )
    if batch_jobs:
        with ThreadPoolExecutor(max_workers=min(6, len(batch_jobs))) as pool:
            futs = [pool.submit(_fetch_one, u, t, n) for u, t, n in batch_jobs]
            for fut in futs:
                res = fut.result()
                if res is not None:
                    fetched.append(res)

    # Ensure we have at least one valid native image if top_native failed (e.g. t9_ missing -> try t7_/b_/s_)
    if not any(f.native for f in fetched):
        for cand in fallback_native:
            res = _fetch_one(cand, tries, True)
            if res is not None:
                fetched.append(res)
                break

    if not fetched:
        raise CoverError("封面下载失败: " + " | ".join(errors[-3:]))

    native_valid = [f for f in fetched if f.native]
    pool_for_anchor = native_valid or fetched
    anchor = max(pool_for_anchor, key=lambda f: (f.width * f.height, len(f.body)))
    anchor_ratio = anchor.width / max(1, anchor.height)

    verified: list[_FetchedCover] = []
    for item in fetched:
        if item.native:
            verified.append(item)
            continue
        if anchor.dhash is None or item.dhash is None:
            # Without perceptual hash support, never blindly trust cross-host images
            continue
        dist = hamming_distance(anchor.dhash, item.dhash)
        ratio_diff = abs((item.width / max(1, item.height)) - anchor_ratio)
        if dist <= same_image_max_dist and ratio_diff <= 0.18:
            verified.append(item)

    if not verified:
        verified = [anchor]

    winner = max(verified, key=lambda f: (f.width * f.height, len(f.body)))

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{safe_filename(stem)}{winner.ext}")
    with open(path, "wb") as fh:
        fh.write(winner.body)
    return CoverResult(
        path=path,
        url=winner.url,
        bytes=len(winner.body),
        content_type=winner.ctype,
        width=winner.width,
        height=winner.height,
    )
