"""CLI 入口：编排 A/B/C/D 四条线。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

from .article import Article, fetch_article, parse_article_html
from .backend_album import AlbumBackend, parse_album_url
from .backend_mp import MpBackend
from .backend_rss import RssBackend
from .fetcher import Fetcher, RateLimited, FetchError
from .storage import AccountStore
from .utils import read_json, dedupe_keep_order, ts_to_str

log = logging.getLogger("wxscraper")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wxscraper",
        description="微信公众号文章抓取工具（纯 requests + beautifulsoup4）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python -m wxscraper --url "https://mp.weixin.qq.com/s/xxxx"
  python -m wxscraper --name "刘润" --mp-cookie "..." --token "123456"
  python -m wxscraper --name "刘润" --config config.json
  python -m wxscraper --url "https://mp.weixin.qq.com/s/xxxx" --rss-base http://localhost:4001 --rss-token xxx

后端说明：
  A 线（单篇）：仅有 --url 且无凭证时，只抓这一篇。
  B 线（真全量）：提供 --mp-cookie + --token，走公众号后台接口拉全部群发记录。
  C 线（合集）：免登录，抓文章页中发现的合集（appmsgalbum），覆盖度=号主建的合集。
  D 线（RSS）：--rss-base/--rss-token 指向自部署 Wechat2RSS / wewe-rss。
""",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--name", help="公众号名称（B 线搜号；无凭证时退化为 C 线）")
    src.add_argument("--url", help="任一文章链接")

    p.add_argument("--mp-cookie", help="公众号后台完整 Cookie（B 线必需）")
    p.add_argument("--token", help="公众号后台 URL 中的 token 参数（B 线必需）")
    p.add_argument("--rss-base", help="自部署 Wechat2RSS/wewe-rss 地址，如 http://localhost:4001")
    p.add_argument("--rss-token", help="RSS 实例的访问 token（k 参数）")

    p.add_argument("-o", "--output", default="output", help="输出根目录（默认 output/）")
    p.add_argument("--config", help="JSON 配置文件（键与参数名一致，命令行优先）")
    p.add_argument("--no-images", action="store_true", help="不下载正文图片")
    p.add_argument("--albums-only", action="store_true",
                   help="只走 C 线合集（即使提供了 cookie/token）")
    p.add_argument("--max-pages", type=int, default=0,
                   help="列表翻页上限（0=不限），用于调试")
    p.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    return p


def _merge_config(args: argparse.Namespace) -> argparse.Namespace:
    """命令行优先，config.json 兜底。"""
    if not args.config:
        return args
    cfg = read_json(args.config, {})
    for k, v in cfg.items():
        attr = k.replace("-", "_")
        if getattr(args, attr, None) in (None, False) and v not in (None, ""):
            setattr(args, attr, v)
    return args


# ---------------------------------------------------------------------- #
def _scrape_urls(fetcher: Fetcher, store: AccountStore, urls: List[str],
                 download_images: bool) -> tuple[int, int]:
    """逐篇走 A 管线，带 checkpoint。返回 (成功, 失败)。"""
    ok = fail = 0
    for i, url in enumerate(urls, 1):
        if store.checkpoint.is_done(url):
            log.info("[%d/%d] 跳过（checkpoint）: %s", i, len(urls), url)
            continue
        log.info("[%d/%d] 抓取: %s", i, len(urls), url)
        try:
            art = fetch_article(fetcher, url)
            store.save_article(art, fetcher, download_images)
            store.checkpoint.mark_done(url)
            store.checkpoint.save()
            ok += 1
        except RateLimited as e:
            log.error("被限流，保存进度后停止: %s", e)
            store.checkpoint.save()
            break
        except FetchError as e:
            log.error("抓取失败: %s", e)
            fail += 1
    return ok, fail


# ---------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    args = _merge_config(build_parser().parse_args(argv))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    fetcher = Fetcher(cookie=args.mp_cookie)
    os.makedirs(args.output, exist_ok=True)

    seed_url: Optional[str] = args.url
    account_name_hint: Optional[str] = args.name
    biz_hint = ""

    # ---- 第 1 步：确定公众号身份（名称 / __biz），并从种子文章收集合集 ----
    album_urls: List[str] = []
    if seed_url:
        log.info("抓取种子文章以识别公众号: %s", seed_url)
        try:
            art = fetch_article(fetcher, seed_url)
        except FetchError as e:
            log.error("种子文章抓取失败: %s", e)
            return 2
        account_name_hint = art.meta.account_name or account_name_hint or "unknown_account"
        biz_hint = art.meta.biz
        album_urls = art.album_urls
        store = AccountStore(args.output, account_name_hint)
        store.save_article(art, fetcher, not args.no_images)
        store.checkpoint.mark_done(seed_url)
        store.checkpoint.save()
        store.save_account({
            "name": art.meta.account_name, "biz": art.meta.biz,
            "avatar": art.meta.avatar,
        })
        log.info("公众号：%s  __biz=%s  发现合集 %d 个",
                 account_name_hint, biz_hint or "(未知)", len(album_urls))
    else:
        store = AccountStore(args.output, account_name_hint or "unknown_account")

    # ---- 第 2 步：选择后端 ----
    has_mp_cred = bool(args.mp_cookie and args.token)
    has_rss = bool(args.rss_base)

    # D 线优先（若显式配置）
    if has_rss and (args.mp_cookie is None):
        rss = RssBackend(fetcher, args.rss_base, args.rss_token or "")
        bid = biz_hint or (rss.find_biz_by_name(account_name_hint) if account_name_hint else None)
        if not bid:
            log.error("RSS 后端无法确定 bid（__biz），请先在 RSS 实例订阅该号")
        else:
            items = rss.list_articles(bid, content=True)
            log.info("RSS 返回 %d 篇", len(items))
            ok = fail = 0
            for it in items:
                if store.checkpoint.is_done(it.link):
                    continue
                try:
                    if it.content_html:
                        art = parse_article_html(
                            f'<div id="js_content">{it.content_html}</div>'
                            f'<script>var msg_title = "{it.title}"; var ct = "{it.create_time}";</script>',
                            it.link,
                        )
                    else:
                        art = fetch_article(fetcher, it.link)
                    if not art.meta.account_name:
                        art.meta.account_name = account_name_hint or ""
                    store.save_article(art, fetcher, not args.no_images)
                    store.checkpoint.mark_done(it.link)
                    store.checkpoint.save()
                    ok += 1
                except FetchError as e:
                    log.error("抓取失败 %s: %s", it.link, e)
                    fail += 1
            log.info("完成：成功 %d，失败 %d", ok, fail)
        _finish(store)
        return 0

    # B 线（真全量）
    if has_mp_cred and not args.albums_only:
        mp = MpBackend(fetcher, args.token)
        fakeid = ""
        if account_name_hint:
            log.info("B 线：搜索公众号「%s」...", account_name_hint)
            candidates = mp.search_biz(account_name_hint)
            if not candidates:
                log.error("未搜到公众号（cookie/token 可能失效），退化到 C 线")
            else:
                hit = next(
                    (c for c in candidates if c.nickname == account_name_hint),
                    candidates[0],
                )
                fakeid = hit.fakeid
                store.save_account({
                    "name": hit.nickname, "alias": hit.alias,
                    "avatar": hit.round_head_img, "signature": hit.signature,
                    "fakeid": fakeid, "biz": biz_hint,
                })
                log.info("选中：%s (fakeid=%s)", hit.nickname, fakeid)
        if fakeid:
            urls: List[str] = []
            pages = 0
            for item in mp.iter_articles(fakeid):
                urls.append(item.link)
                if len(urls) % 5 == 0:
                    pages += 1
                    if args.max_pages and pages >= args.max_pages:
                        log.info("达到 --max-pages=%d，停止翻页", args.max_pages)
                        break
            urls = dedupe_keep_order(urls, key=lambda u: u)
            log.info("B 线共发现 %d 篇文章，开始逐篇抓取（sn 会过期，立即抓正文）", len(urls))
            ok, fail = _scrape_urls(fetcher, store, urls, not args.no_images)
            log.info("B 线完成：成功 %d，失败 %d（进度已 checkpoint，可重跑续爬）", ok, fail)
            _finish(store)
            return 0

    # C 线（免登录合集）
    if not album_urls and account_name_hint and not seed_url:
        log.error("仅 --name 且无 cookie/token 时无法定位合集。"
                  "请提供该号任一文章链接（--url），或提供 --mp-cookie/--token 走 B 线。")
        return 2
    if album_urls:
        # 同一合集可能以多种 URL 形态出现（& 被转义为 \x26 等），先按 album_id 去重
        seen_albums: List[tuple[str, str]] = []
        for au in album_urls:
            parsed = parse_album_url(au)
            if parsed and parsed not in seen_albums:
                seen_albums.append(parsed)
        log.info("C 线：从 %d 个合集收集文章", len(seen_albums))
        album = AlbumBackend(fetcher)
        urls = []
        for biz, album_id in seen_albums:
            if store.checkpoint.album_done(album_id):
                log.info("合集 %s 已完成（checkpoint），跳过", album_id)
                continue
            for it in album.iter_album(biz, album_id):
                urls.append(it.link)
            store.checkpoint.mark_album_done(album_id)
            store.checkpoint.save()
        urls = dedupe_keep_order(urls, key=lambda u: u)
        log.info("合集共发现 %d 篇（覆盖度=号主建的合集，非全部历史）", len(urls))
        ok, fail = _scrape_urls(fetcher, store, urls, not args.no_images)
        log.info("C 线完成：成功 %d，失败 %d", ok, fail)
    elif not seed_url:
        log.warning("没有可抓的文章来源")

    _finish(store)
    return 0


def _finish(store: AccountStore) -> None:
    log.info("输出目录: %s", os.path.abspath(store.dir))


if __name__ == "__main__":
    sys.exit(main())
