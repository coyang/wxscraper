"""D 线（可选后端）：Wechat2RSS / wewe-rss 自部署实例。

配置 --rss-base / --rss-token 后，可直接取全文 JSON。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from .fetcher import Fetcher, FetchError

log = logging.getLogger("wxscraper.backend_rss")


@dataclass
class RssArticle:
    title: str
    link: str
    create_time: int
    content_html: str = ""


class RssBackend:
    def __init__(self, fetcher: Fetcher, base: str, token: str):
        self.fetcher = fetcher
        self.base = base.rstrip("/")
        self.token = token

    def find_biz_by_name(self, name: str) -> Optional[str]:
        """按名称查 bid（兼容常见 wechat2rss 列表接口）。"""
        try:
            data = self.fetcher.get_json(
                f"{self.base}/list", params={"name": name}, delay=(1, 2)
            )
        except FetchError as e:
            log.error("RSS 列表接口失败: %s", e)
            return None
        items = data if isinstance(data, list) else data.get("list") or data.get("data") or []
        for it in items:
            if name in (it.get("name") or it.get("title") or ""):
                return it.get("bid") or it.get("id") or it.get("__biz")
        return None

    def list_articles(self, bid: str, content: bool = True) -> List[RssArticle]:
        """取某号全部文章（content=1 时含全文 HTML）。"""
        try:
            data = self.fetcher.get_json(
                f"{self.base}/api/query",
                params={"k": self.token, "bid": bid, "content": "1" if content else "0"},
                delay=(1, 2),
            )
        except FetchError as e:
            log.error("RSS 查询失败: %s", e)
            return []
        items = data.get("list") or data.get("data") or (data if isinstance(data, list) else [])
        out: List[RssArticle] = []
        for it in items:
            out.append(
                RssArticle(
                    title=it.get("title") or "",
                    link=it.get("url") or it.get("link") or "",
                    create_time=int(it.get("created_at") or it.get("create_time") or 0),
                    content_html=it.get("content") or it.get("html") or "",
                )
            )
        return out
