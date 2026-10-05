# -*- coding: utf-8 -*-
"""Top-level resolution: title in, {author, cover} out."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from .model import Candidate, author_similarity, is_confident, rank_candidates, search_title
from .sources import Diagnostic, SourceError, get_source, source_names, search_sources

__all__ = ["Resolution", "resolve", "resolve_ref", "parse_ref"]

# A candidate counts as "the same title" for ambiguity reporting only when it is
# an exact match, or exact after folding a short volume/edition subtitle. Prefix
# neighbours ("斗罗大陆 秋", "魔道祖师 2") are different books, not ambiguity.
SAME_TITLE_THRESHOLD = 0.97

# Book URLs / bare ids that identify one specific book, so no search is needed.
# This is the usable half of the 番茄 integration: its search endpoint is behind a
# Bdturing slide captcha, but its SSR detail page is open, so a URL copied from the
# browser (or an id) resolves directly.
_REF_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"fanqienovel\.com/(?:page|reader)/(?P<id>\d+)", re.I), "fanqie"),
    (re.compile(r"fanqienovel\.com/[^\s]*?book_id=(?P<id>\d+)", re.I), "fanqie"),
    (re.compile(r"^\s*(?P<id>\d{15,25})\s*$"), "fanqie"),          # bare 番茄 id
    (re.compile(r"(?:bilinovel|linovelib)\.com/novel/(?P<id>\d+)", re.I), "bilinovel"),
)


def parse_ref(text: str) -> tuple[str, str] | None:
    """Recognize a book URL or bare book id -> (source_name, id)."""
    value = (text or "").strip()
    if not value or len(value) > 500:
        return None
    for pattern, source in _REF_PATTERNS:
        found = pattern.search(value)
        if found:
            return source, found.group("id")
    return None


def resolve_ref(text: str, *, timeout: float = 15.0) -> Resolution:
    """Resolve one book straight from its site URL / id, skipping search entirely.

    The reference is authoritative by construction, so the candidate is marked as
    an exact match. Failures surface as an empty resolution with the reason in
    `diagnostics`, exactly like a normal lookup.
    """
    target = parse_ref(text)
    if target is None:
        raise ValueError(f"无法识别的书籍链接或 ID: {text!r}")
    source_name, ident = target
    source = get_source(source_name)
    started_diag = f"{source.label} #{ident}"
    try:
        cand = source.detail(ident, timeout=timeout)
    except SourceError as exc:
        return Resolution(
            query=text,
            best=None,
            diagnostics=[Diagnostic(source=started_diag, ok=False, error=str(exc),
                                    blocked=exc.blocked)],
        )
    cand.title_score = 1.0      # exact by construction
    cand.score = 1.0
    return Resolution(
        query=text,
        best=cand,
        diagnostics=[Diagnostic(source=started_diag, ok=True, count=1)],
    )


@dataclass
class Resolution:
    query: str
    best: Candidate | None
    alternatives: list[Candidate] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    author_hint: str = ""
    threshold: float = 0.85
    ambiguous: bool = False
    same_title: list[Candidate] = field(default_factory=list)
    weak: Candidate | None = None

    @property
    def confident(self) -> bool:
        return is_confident(self.best, self.threshold)

    @property
    def found(self) -> bool:
        return self.best is not None

    def as_dict(self, *, include_alternatives: bool = True) -> dict:
        out: dict = {"query": self.query}
        if self.best:
            out.update({
                "title": self.best.title,
                "author": self.best.author,
                "cover": self.best.cover,
                "source": self.best.source,
                "url": self.best.url,
                "matched": self.confident,
                "ambiguous": self.ambiguous,
                "score": round(self.best.score, 4),
                "title_match": round(self.best.title_score, 4),
            })
            if self.best.extra.get("coverFrom"):
                out["cover_from"] = self.best.extra["coverFrom"]
        else:
            # 输入与库中任何作品都不相似：返回空，而不是硬塞一个最像的结果。
            out.update({"title": None, "author": None, "cover": None,
                        "matched": False, "ambiguous": False,
                        "error": "未找到匹配的书目"})
        if self.weak is not None:
            out["weak_match"] = {
                "title": self.weak.title,
                "author": self.weak.author,
                "source": self.weak.source,
                "score": round(self.weak.title_score, 4),
                "note": f"相似度低于阈值 {self.threshold}，未采纳",
            }
        if self.ambiguous:
            out["same_title_matches"] = [
                {"title": c.title, "author": c.author, "source": c.source, "cover": c.cover}
                for c in self.same_title
            ]
        if include_alternatives:
            out["alternatives"] = [c.as_dict() for c in self.alternatives]
        out["sources"] = [d.as_dict() for d in self.diagnostics]
        return out


def _pick_cover_donor(best: Candidate, others: Sequence[Candidate]) -> Candidate | None:
    """Find a sibling record that carries the same book's artwork.

    Some sources (JJWXC) publish no cover at all, so a plain exact-title match can
    come back without artwork while another source holds the same book *with* a
    cover. Borrowing is only allowed when the author agrees, or the titles are
    effectively identical — otherwise we would attach a random namesake's cover.
    """
    for cand in others:
        if not cand.cover or cand.title_score < 0.90:
            continue
        if best.author and cand.author:
            if author_similarity(best.author, cand.author) >= 0.85:
                return cand
        elif cand.title_score >= 0.99:
            return cand
    return None


def resolve(
    title: str,
    *,
    author_hint: str = "",
    sources: Sequence[str] | None = None,
    timeout: float = 12.0,
    workers: int = 6,
    top: int = 8,
    threshold: float = 0.85,
    min_score: float | None = None,
    on_result=None,
) -> Resolution:
    """Look a book up across all selected sources and return the best match.

    `min_score` (default: `threshold`) is the similarity floor for accepting an
    answer at all. Below it the lookup reports "not found" **with empty fields**
    rather than substituting the least-bad candidate — garbage in, empty out.
    The rejected near-miss is still exposed as `Resolution.weak` for diagnostics.

    Sources that fail (network, anti-bot, layout change) are reported in
    `diagnostics`; they never abort the lookup.
    """
    query = (title or "").strip()
    if not query:
        raise ValueError("书名不能为空")

    floor = threshold if min_score is None else min_score
    # ★ 2026-09-29：送进各源搜索前先剥掉卷次后缀 / 促销括号。
    #   实测「败犬女主太多了 第八卷」这种写法：微读「搜索接口返回 0 条结果」、
    #   晋江「未匹配到搜索结果」—— 各源是字面检索，不像 norm_title 那样会先去掉卷次。
    #   所以：先用"干净的检索形"查一遍；一条候选都没有时，再用原始写法兜一次。
    lookup = search_title(query) or query
    candidates, diagnostics = search_sources(
        lookup, sources=sources, workers=workers, timeout=timeout, on_result=on_result
    )
    if not candidates and lookup != query:
        retry_candidates, retry_diag = search_sources(
            query, sources=sources, workers=workers, timeout=timeout, on_result=on_result
        )
        candidates = retry_candidates
        diagnostics = list(diagnostics) + list(retry_diag)
    priority = [name for name in source_names(include_overseas=True)
                if not sources or name in set(sources)]
    ranked = rank_candidates(query, candidates, author_hint=author_hint,
                             source_priority=priority)

    best = ranked[0] if ranked else None
    weak = None
    if best is not None and best.title_score < floor:
        # No plausible match: return empty instead of the closest stranger.
        weak, best = best, None

    if best is not None and not best.cover:
        donor = _pick_cover_donor(best, ranked[1:])
        if donor is not None:
            best.cover = donor.cover
            best.extra["coverFrom"] = donor.source
            best.extra["coverVariants"] = donor.extra.get("coverVariants") or []

    # Same title, different authors => the title alone cannot decide; say so.
    same_title = [c for c in ranked if c.title_score >= SAME_TITLE_THRESHOLD][:5]
    authors = {c.author for c in same_title if c.author}
    ambiguous = best is not None and len(same_title) > 1 and len(authors) > 1

    # Only show genuinely plausible siblings as alternatives.
    alternatives = ([c for c in ranked[1:] if c.title_score >= 0.55][: max(0, top - 1)]
                    if best is not None else [])
    return Resolution(query=query, best=best, alternatives=alternatives,
                      diagnostics=diagnostics, author_hint=author_hint,
                      threshold=threshold, ambiguous=ambiguous, same_title=same_title,
                      weak=weak)
