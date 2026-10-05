# -*- coding: utf-8 -*-
"""Command line interface.

Examples
--------
    py -m novelmeta 赘婿
    py -m novelmeta 赘婿 --download covers
    py -m novelmeta 诡秘之主 --author 爱潜水的乌贼 --json
    py -m novelmeta --file titles.txt --download covers --jsonl
    py -m novelmeta https://fanqienovel.com/page/7143038691944959011
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Iterable, Sequence

from . import __version__
from .cover import CoverError, download_cover
from .model import norm_title
from .resolver import Resolution, parse_ref, resolve, resolve_ref
from .sources import source_names

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_ERROR = 2

# Windows shells happily hand us a BOM or a zero-width space when the input was
# piped or pasted; str.strip() does not remove those, and a lone "\ufeff" would
# otherwise be looked up as a book title.
_ZERO_WIDTH_RE = re.compile(r"[\ufeff\u200b\u200c\u200d\u2060]")


def _clean_query(text: str | None) -> str:
    """Trim a user-supplied title: whitespace, BOM and zero-width characters."""
    return _ZERO_WIDTH_RE.sub("", text or "").strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novelmeta",
        description="根据书名查询网文作者与封面（多源聚合：微信读书 / 晋江 / 番茄 …）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  py -m novelmeta 赘婿\n"
            "  py -m novelmeta 赘婿 --download covers\n"
            "  py -m novelmeta 斗罗大陆 --author 唐家三少 --json\n"
            "  py -m novelmeta --file titles.txt --jsonl\n\n"
            f"可用数据源: {', '.join(source_names())}"
        ),
    )
    parser.add_argument("titles", nargs="*", help="书名（可多个）")
    parser.add_argument("--file", "-f", help="从文本文件按行读取书名（- 表示 stdin）")
    parser.add_argument("--author", "-a", default="", help="作者提示，用于同名书消歧")
    parser.add_argument("--source", "-s", default="",
                        help="只使用指定数据源，逗号分隔；默认全部（见 --list-sources）")
    parser.add_argument("--download", "-d", metavar="DIR", help="下载封面到该目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--jsonl", action="store_true", help="每个书名输出一行 JSON")
    parser.add_argument("--all", action="store_true", help="列出全部候选（不止最佳匹配）")
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="判定“匹配成功”的书名相似度阈值（默认 0.85）")
    parser.add_argument("--timeout", type=float, default=12.0, help="单个源超时秒数（默认 12）")
    parser.add_argument("--top", type=int, default=8, help="保留的候选数量（默认 8）")
    parser.add_argument("--no-overseas", action="store_true",
                        help="(已废弃，海外源默认不启用，保留以兼容旧脚本)")
    parser.add_argument("--overseas", action="store_true",
                        help="同时查询海外源（Webnovel / なろう；国内网络通常不可达）")
    parser.add_argument("--min-score", type=float, default=None,
                        help="低于该书名相似度就返回空（默认等于 --threshold）")
    parser.add_argument("--show-weak", action="store_true",
                        help="未找到时仍打印最接近的候选作为参考")
    parser.add_argument("--lino-refresh", action="store_true",
                        help="强制重建哔哩轻小说目录索引（默认缓存 7 天）")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="交互模式：反复提示输入书名（双击启动器时自动使用）")
    parser.add_argument("--list-sources", action="store_true", help="列出数据源后退出")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出结果，不打印诊断信息")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个数据源的状态")
    parser.add_argument("--version", action="version", version=f"novelmeta {__version__}")
    return parser


def _read_titles(args) -> list[str]:
    titles = list(args.titles)
    if args.file:
        if args.file == "-":
            titles += [line for line in sys.stdin.read().splitlines()]
        else:
            with open(args.file, encoding="utf-8") as fh:
                titles += [line for line in fh.read().splitlines()]
    seen: set[str] = set()
    out: list[str] = []
    for raw in titles:
        title = _clean_query(raw)
        if not title or title.startswith("#"):
            continue
        if title not in seen:
            seen.add(title)
            out.append(title)
    return out


def _resolve_sources(args) -> list[str] | None:
    if args.source:
        return [name.strip().lower() for name in args.source.split(",") if name.strip()]
    if args.overseas:
        return source_names(include_overseas=True)
    return None


def _format_human(res: Resolution, *, show_all: bool, verbose: bool, show_weak: bool = False,
                  cover_path: str = "", out=None) -> None:
    # Resolve stdout at call time: a default of sys.stdout would be bound at
    # import time and silently bypass contextlib.redirect_stdout / capsys.
    if out is None:
        out = sys.stdout
    p = lambda *a: print(*a, file=out)  # noqa: E731
    if not res.found:
        # Nonsense / unknown title: empty result, no guessed answer.
        p(f"✗ {res.query}: 未找到匹配的书目（已返回空）")
        if show_weak and res.weak is not None:
            p(f"  · 最接近的候选（相似度 {res.weak.title_score:.2f}，未采纳）: "
              f"{res.weak.title} — {res.weak.author or '未知作者'} [{res.weak.source}]")
    else:
        best = res.best
        mark = "✓" if res.confident else "?"
        p(f"{mark} 书名: {best.title}")
        p(f"  作者: {best.author or '（未知）'}")
        cover_from = best.extra.get("coverFrom")
        cover_note = f"（取自 {cover_from}）" if cover_from else ""
        p(f"  封面: {(best.cover or '（该源无封面）') + cover_note}")
        src = best.source
        p(f"  来源: {src}  (匹配度 {best.title_score:.2f} / 综合 {best.score:.2f})")
        if best.url:
            p(f"  链接: {best.url}")
        if cover_path:
            p(f"  封面已保存: {cover_path}")
        if not res.confident:
            p("  ⚠ 书名未精确匹配，请人工确认，或用 --author 提供作者消歧")
        if res.ambiguous:
            p(f"  ⚠ 书名相同但作者不同的作品有 {len(res.same_title)} 部，"
              f"已按来源排序取最优；如需指定请加 --author：")
            for cand in res.same_title[:3]:
                p(f"      · {cand.title} — {cand.author or '未知作者'} [{cand.source}]")
        if show_all and res.alternatives:
            p("  其他候选:")
            for cand in res.alternatives:
                cover = "有封面" if cand.cover else "无封面"
                p(f"    - {cand.title} — {cand.author or '未知作者'} "
                  f"[{cand.source}, {cover}, {cand.title_score:.2f}]")
        elif res.alternatives and verbose:
            p(f"  （另有 {len(res.alternatives)} 个候选，用 --all 查看）")

    if verbose:
        p("  数据源状态:")
        for diag in res.diagnostics:
            state = f"{diag.count} 条" if diag.ok else f"失败: {diag.error}"
            flag = " [被反爬拦截]" if diag.blocked else ""
            p(f"    - {diag.source}: {state}{flag} ({diag.ms} ms)")


def _cover_target_name(res: Resolution) -> str:
    title = res.best.title if res.best else res.query
    author = res.best.author if res.best else ""
    stem = norm_title(title) or title
    if author:
        stem = f"{title} - {author}"
    return stem


def _lookup_many(titles: Sequence[str], args, sources) -> list[tuple[Resolution, str]]:
    """Resolve a batch of titles and optionally download their covers."""
    results: list[tuple[Resolution, str]] = []
    for title in titles:
        try:
            if parse_ref(title):
                # A book URL / id identifies exactly one book, so skip search. This
                # is also the only way to reach 番茄, whose search API is captcha-gated.
                res = resolve_ref(title, timeout=max(args.timeout, 15.0))
            else:
                res = resolve(
                    title,
                    author_hint=args.author,
                    sources=sources,
                    timeout=args.timeout,
                    top=args.top,
                    threshold=args.threshold,
                    min_score=args.min_score,
                    on_result=(lambda label, found, diag: print(
                        f"  · {label}: {'ok' if diag.ok else 'fail'} ({diag.count})",
                        file=sys.stderr)) if args.verbose else None,
                )
        except ValueError as exc:
            print(f"✗ {title}: {exc}", file=sys.stderr)
            results.append((Resolution(query=title, best=None), ""))
            continue

        cover_path = ""
        if args.download and res.best and res.best.cover:
            try:
                got = download_cover(
                    res.best.cover,
                    args.download,
                    stem=_cover_target_name(res),
                    variants=res.best.extra.get("coverVariants"),
                    referer=res.best.extra.get("coverReferer"),
                    timeout=max(args.timeout, 15.0),
                )
                cover_path = f"{got.path} ({got.width}x{got.height}, {got.bytes} B)"
            except CoverError as exc:
                print(f"  ! 封面下载失败: {exc}", file=sys.stderr)
        results.append((res, cover_path))
    return results


def _interactive(args, sources) -> int:
    """Prompt for titles one at a time — used when the launcher is double-clicked.

    Kept on the Python side on purpose: the console encoding is Python's problem
    (PEP 528 handles it), so the .cmd wrapper can stay pure ASCII and never
    mangles Chinese text on a GBK console.
    """
    print()
    print("  网文书名 -> 作者 + 封面        （直接回车退出）")
    print()
    # Only offer the cover download on a real console: when titles are piped in,
    # an extra prompt would swallow the next title.
    offer_cover = sys.stdin.isatty() and not args.download
    rc_total = EXIT_OK
    while True:
        try:
            title = _clean_query(input("  请输入书名: "))
        except (EOFError, KeyboardInterrupt):
            print()
            return rc_total
        if not title:
            return rc_total
        print()
        results = _lookup_many([title], args, sources)
        for res, cover_path in results:
            _format_human(res, show_all=args.all, verbose=args.verbose,
                          show_weak=args.show_weak, cover_path=cover_path)
            if not res.found or not res.confident:
                rc_total = EXIT_NOT_FOUND
            elif offer_cover and res.best.cover:
                try:
                    answer = _clean_query(input("  保存封面到 covers 目录？[y/N] "))
                except (EOFError, KeyboardInterrupt):
                    print()
                    return rc_total
                if answer.lower() in {"y", "yes", "是", "好"}:
                    try:
                        got = download_cover(
                            res.best.cover, "covers",
                            stem=_cover_target_name(res),
                            variants=res.best.extra.get("coverVariants"),
                        )
                        print(f"  已保存: {got.path} ({got.width}x{got.height}, "
                              f"{got.bytes} B)")
                    except CoverError as exc:
                        print(f"  ! 封面下载失败: {exc}")
        print()
        print("  " + "-" * 56)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_sources:
        for name in source_names():
            print(name)
        return EXIT_OK

    titles = _read_titles(args)
    if not titles and not args.interactive:
        parser.print_help()
        return EXIT_ERROR

    sources = _resolve_sources(args)
    if sources:
        unknown = [s for s in sources if s not in source_names()]
        if unknown:
            print(f"未知数据源: {', '.join(unknown)}；可用: {', '.join(source_names())}",
                  file=sys.stderr)
            return EXIT_ERROR

    # A first-run 哔哩轻小说 index build takes minutes; narrate it on stderr so the
    # tool never looks hung, and keep stdout clean for piping.
    if not args.source or "bilinovel" in (sources or []):
        from . import sources as _sources

        if args.lino_refresh or _sources.BILINOVEL_INDEX.age() is None:
            _sources.BILINOVEL_PROGRESS = lambda msg: print(f"  [{msg}]", file=sys.stderr)
            if args.lino_refresh:
                try:
                    size = get_source("bilinovel").refresh_index(timeout=max(args.timeout, 20.0))
                    print(f"  哔哩轻小说索引已刷新：{size} 部作品", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001 - index is optional
                    print(f"  ! 哔哩轻小说索引刷新失败: {exc}", file=sys.stderr)
                _sources.BILINOVEL_PROGRESS = None

    if args.interactive:
        return _interactive(args, sources)

    results = _lookup_many(titles, args, sources)

    if args.json:
        payload = [res.as_dict() | ({"cover_file": cp} if cp else {}) for res, cp in results]
        print(json.dumps(payload if len(payload) > 1 else payload[0],
                         ensure_ascii=False, indent=2))
    elif args.jsonl:
        for res, cp in results:
            row = res.as_dict()
            if cp:
                row["cover_file"] = cp
            print(json.dumps(row, ensure_ascii=False))
    elif not args.quiet:
        for i, (res, cp) in enumerate(results):
            if i:
                print()
            _format_human(res, show_all=args.all, verbose=args.verbose,
                          show_weak=args.show_weak, cover_path=cp)
    else:
        for res, cp in results:
            if res.best:
                print(f"{res.query}\t{res.best.author}\t{res.best.cover or cp}")
            else:
                # 空结果：字段留空而不是给一个猜测值
                print(f"{res.query}\t\t")

    for res, _cp in results:
        if not res.found or not res.confident:
            return EXIT_NOT_FOUND
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
