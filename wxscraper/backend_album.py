"""C 线：免登录合集 appmsgalbum（覆盖度 = 号主建的合集）。

从文章页收集合集链接 -> 翻页 getalbum_list 拿 title/link/create_time。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .fetcher import Fetcher, FetchError

log = logging.getLogger("wxscraper.backend_album")

BASE = "https://mp.weixin.qq.com"


@dataclass
class AlbumItem:
    title: str
    link: str
    create_time: int
    msgid: str = ""
    itemidx: str = ""


def parse_album_url(url: str) -> Optional[Tuple[str, str]]:
    """从合集 URL 解析 (__biz, album_id)。"""
    q = parse_qs(urlparse(url).query)
    biz = (q.get("__biz") or [""])[0]
    album_id = (q.get("album_id") or [""])[0]
    if biz and album_id:
        return biz, album_id
    return None


class AlbumBackend:
    def __init__(self, fetcher: Fetcher, page_delay=(3.0, 5.0)):
        self.fetcher = fetcher
        self.page_delay = page_delay

    def iter_album(self, biz: str, album_id: str, count: int = 10) -> Iterator[AlbumItem]:
        """翻页拉取合集内全部文章。continue_flag 非空则继续。"""
        begin_msgid = ""
        begin_itemidx = ""
        while True:
            params = {
                "action": "getalbum",
                "__biz": biz,
                "album_id": album_id,
                "count": str(count),
                "begin_msgid": begin_msgid,
                "begin_itemidx": begin_itemidx,
                "continue_flag": "",
                "f": "json",
            }
            try:
                data = self.fetcher.get_json(
                    f"{BASE}/mp/appmsgalbum",
                    params=params,
                    referer=f"{BASE}/mp/appmsgalbum?__biz={biz}&action=getalbum&album_id={album_id}",
                    delay=self.page_delay,
                )
            except FetchError as e:
                log.error("合集翻页失败 album=%s: %s", album_id, e)
                return

            # 真实接口结构：{"base_resp":..., "getalbum_resp": {"article_list": [...], "continue_flag": ...}}
            resp = data.get("getalbum_resp") or data
            items = resp.get("article_list") or data.get("getalbum_list") or []
            if not items:
                return
            for it in items:
                link = it.get("url") or it.get("link") or ""
                if link.startswith("/"):
                    link = BASE + link
                yield AlbumItem(
                    title=it.get("title") or "",
                    link=link.replace("&amp;", "&"),
                    create_time=int(it.get("create_time") or 0),
                    msgid=str(it.get("msgid") or ""),
                    itemidx=str(it.get("itemidx") or ""),
                )
            # 游标：本页最后一条
            last = items[-1]
            begin_msgid = str(last.get("msgid") or "")
            begin_itemidx = str(last.get("itemidx") or "")
            if not resp.get("continue_flag"):
                return
