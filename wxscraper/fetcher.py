"""集中式 HTTP 抓取层：限速、重试、反爬失败页检测。

所有网络请求都必须经过 Fetcher，不直接调用 requests。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from .utils import sleep_random

log = logging.getLogger("wxscraper.fetcher")

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 微信返回的"200 但其实是错误提示页"特征
_ERROR_PAGE_PAT = re.compile(r"未知错误|环境异常|操作频繁|访问过于频繁|weixin110\.qq\.com")

DEFAULT_BACKOFFS = (10, 30, 60)  # 指数退避秒数，最多 3 次重试


class FetchError(Exception):
    """网络层不可恢复错误。"""


class RateLimited(FetchError):
    """命中微信频率/异常提示页，且重试后仍失败。"""


class Fetcher:
    """带限速与重试的 HTTP 客户端。

    :param delay: (min, max) 每次成功请求后的随机 sleep 区间（秒）
    :param cookie: 公众号后台 Cookie（B 线接口用）
    :param max_retries: 失败页/网络错误的最大重试次数
    """

    def __init__(
        self,
        delay=(3.0, 5.0),
        cookie: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 20,
    ):
        self.delay = delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DESKTOP_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        if cookie:
            self.session.headers["Cookie"] = cookie

    # ------------------------------------------------------------------ #
    def _looks_like_error_page(self, text: str, require_marker: Optional[str]) -> bool:
        """判定一个 200 响应是否是反爬提示页。

        require_marker: 正常页面应包含的标记（如 'js_content'）。
        """
        if require_marker and require_marker in text:
            return False
        return bool(_ERROR_PAGE_PAT.search(text))

    def get(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        referer: Optional[str] = None,
        require_marker: Optional[str] = None,
        delay: Optional[tuple] = None,
        expect_json: bool = False,
    ) -> requests.Response:
        """GET，带限速 + 失败页检测 + 指数退避重试。

        :param require_marker: 响应文本中必须出现的标记，否则视为反爬提示页。
        :param expect_json: True 时把 JSON 解析错误也视为可重试失败。
        """
        headers = {}
        if referer:
            headers["Referer"] = referer

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
                resp.encoding = resp.apparent_encoding or "utf-8"
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code}: {url}")

                if expect_json:
                    try:
                        resp.json()
                    except ValueError:
                        # JSON 接口的错误提示页
                        if attempt < self.max_retries:
                            self._backoff(attempt, url, "invalid json")
                            continue
                        raise FetchError(f"JSON 解析失败: {url}")

                if not expect_json and self._looks_like_error_page(resp.text, require_marker):
                    if attempt < self.max_retries:
                        self._backoff(attempt, url, "error page")
                        continue
                    raise RateLimited(f"命中微信反爬提示页（已重试 {self.max_retries} 次）: {url}")

                # 成功：限速 sleep
                lo, hi = delay or self.delay
                sleep_random(lo, hi)
                return resp
            except (requests.RequestException, FetchError) as e:
                last_exc = e
                if isinstance(e, RateLimited):
                    raise
                if attempt < self.max_retries:
                    self._backoff(attempt, url, str(e))
                    continue
                raise FetchError(f"请求失败（已重试 {self.max_retries} 次）: {url} -> {e}") from e
        raise FetchError(f"请求失败: {url} -> {last_exc}")

    def _backoff(self, attempt: int, url: str, reason: str) -> None:
        wait = DEFAULT_BACKOFFS[min(attempt, len(DEFAULT_BACKOFFS) - 1)]
        log.warning("请求受挫（%s），%ds 后重试: %s", reason, wait, url)
        import time
        time.sleep(wait)

    # ------------------------------------------------------------------ #
    def get_text(self, url: str, **kw) -> str:
        return self.get(url, **kw).text

    def get_json(self, url: str, **kw) -> dict:
        kw["expect_json"] = True
        return self.get(url, **kw).json()

    def get_bytes(self, url: str, *, referer: Optional[str] = None, delay=(0.5, 1.5)) -> bytes:
        return self.get(url, referer=referer, delay=delay).content
