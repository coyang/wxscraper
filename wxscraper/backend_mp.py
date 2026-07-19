"""B 线：公众号后台接口（需要用户自有公众号的 Cookie + token）。

- searchbiz：按名称搜号拿 fakeid
- appmsgpublish：翻页拉全量群发记录（真全量）
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Iterator, List, Optional

from .fetcher import Fetcher, FetchError
from .utils import ts_to_str

log = logging.getLogger("wxscraper.backend_mp")

BASE = "https://mp.weixin.qq.com"

# 遇到这些 errcode 立即停止（频率限制）
_STOP_ERRCODES = {200013, 200040}


@dataclass
class BizInfo:
    fakeid: str
    nickname: str
    alias: str = ""
    round_head_img: str = ""
    signature: str = ""


@dataclass
class ArticleItem:
    title: str
    link: str
    create_time: int
    update_time: int = 0
    digest: str = ""
    cover: str = ""


class MpBackend:
    """公众号后台接口封装。"""

    def __init__(self, fetcher: Fetcher, token: str, page_delay=(5.0, 10.0)):
        self.fetcher = fetcher
        self.token = token
        self.page_delay = page_delay

    # ------------------------------------------------------------------ #
    def search_biz(self, query: str, count: int = 5) -> List[BizInfo]:
        """按名称搜索公众号。"""
        params = {
            "action": "search_biz",
            "token": self.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
            "random": str(random.random()),
            "query": query,
            "begin": "0",
            "count": str(count),
        }
        data = self.fetcher.get_json(
            f"{BASE}/cgi-bin/searchbiz",
            params=params,
            referer=f"{BASE}/cgi-bin/home?t=home/index",
            delay=self.page_delay,
        )
        out = []
        for it in data.get("list") or []:
            out.append(
                BizInfo(
                    fakeid=it.get("fakeid", ""),
                    nickname=it.get("nickname", ""),
                    alias=it.get("alias", ""),
                    round_head_img=it.get("round_head_img", ""),
                    signature=it.get("signature", ""),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    def iter_articles(self, fakeid: str, count: int = 5) -> Iterator[ArticleItem]:
        """翻页拉取某号的全部群发文章。遇到频率 errcode 立即停止（yield 中断）。

        调用方需自行做 checkpoint，以便断点续爬。
        """
        begin = 0
        while True:
            params = {
                "sub": "list",
                "search_field": "null",
                "begin": str(begin),
                "count": str(count),
                "query": "",
                "fakeid": fakeid,
                "type": "101_1",
                "free_publish_type": "1",
                "sub_action": "list_ex",
                "f": "json",
                "token": self.token,
                "lang": "zh_CN",
            }
            try:
                data = self.fetcher.get_json(
                    f"{BASE}/cgi-bin/appmsgpublish",
                    params=params,
                    referer=f"{BASE}/cgi-bin/home?t=home/index",
                    delay=self.page_delay,
                )
            except FetchError as e:
                log.error("拉取文章列表失败 begin=%d: %s（已保存的进度不受影响）", begin, e)
                return

            # errcode 检查
            base_resp = data.get("base_resp") or {}
            errcode = base_resp.get("ret", data.get("errcode", 0)) or 0
            try:
                errcode = int(errcode)
            except (TypeError, ValueError):
                errcode = 0
            if errcode in _STOP_ERRCODES:
                log.warning("命中微信频率限制 errcode=%d，停止翻页（进度已 checkpoint）", errcode)
                return
            if errcode != 0:
                log.error("后台接口返回 errcode=%d errmsg=%s，停止", errcode, base_resp.get("errmsg", ""))
                return

            items = self._extract_items(data)
            if not items:
                return
            for it in items:
                yield it

            total = self._extract_total(data)
            begin += count
            if total is not None and begin >= total:
                return

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_publish_page(data: dict) -> dict:
        """publish_page 是 JSON-in-JSON，需要二次解析。"""
        pp = data.get("publish_page")
        if isinstance(pp, str):
            try:
                return json.loads(pp)
            except json.JSONDecodeError:
                return {}
        return pp or {}

    def _extract_total(self, data: dict) -> Optional[int]:
        pp = self._extract_publish_page(data)
        try:
            return int(pp.get("total_count"))
        except (TypeError, ValueError):
            return None

    def _extract_items(self, data: dict) -> List[ArticleItem]:
        pp = self._extract_publish_page(data)
        out: List[ArticleItem] = []

        def add(appmsg: dict):
            create = appmsg.get("create_time") or appmsg.get("update_time") or 0
            link = appmsg.get("link") or ""
            if link.startswith("/"):
                link = BASE + link
            out.append(
                ArticleItem(
                    title=appmsg.get("title") or "",
                    link=link,
                    create_time=int(create or 0),
                    update_time=int(appmsg.get("update_time") or 0),
                    digest=appmsg.get("digest") or "",
                    cover=appmsg.get("cover") or "",
                )
            )

        # 结构 1：sent_list（群发）
        for sent in pp.get("sent_list") or []:
            for info in sent.get("appmsg_info") or []:
                add(info)
            if sent.get("title"):
                add(sent)
        # 结构 2：publish_list -> publish_info -> appmsgex
        for pub in pp.get("publish_list") or []:
            pi = pub.get("publish_info")
            if isinstance(pi, str):
                try:
                    pi = json.loads(pi)
                except json.JSONDecodeError:
                    pi = {}
            pi = pi or {}
            for ex in pi.get("appmsgex") or []:
                add(ex)
        return out
