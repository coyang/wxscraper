"""A 线：单篇文章页解析（免登录核心管线）。

字段双路提取：DOM 优先，内嵌 JS 变量兜底。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from bs4 import BeautifulSoup

from .fetcher import Fetcher, FetchError
from .utils import html_unescape, strip_js_string, ts_to_str

log = logging.getLogger("wxscraper.article")

MP_HOST = "https://mp.weixin.qq.com"

_ALBUM_PAT = re.compile(
    r"https?://mp\.weixin\.qq\.com/mp/appmsgalbum\?[^\"'<>\s]+", re.I
)
_BIZ_PAT = re.compile(r"__biz=([A-Za-z0-9+/=]+)")


def _js_var(html: str, name: str) -> Optional[str]:
    """从内嵌 JS 中取 var xxx = "..." 的字符串值。"""
    m = re.search(r'var\s+%s\s*=\s*"((?:[^"\\]|\\.)*)"' % re.escape(name), html)
    if m:
        return html_unescape(strip_js_string(m.group(1)))
    m = re.search(r"var\s+%s\s*=\s*'((?:[^'\\]|\\.)*)'" % re.escape(name), html)
    if m:
        return html_unescape(strip_js_string(m.group(1)))
    return None


@dataclass
class ArticleMeta:
    url: str
    title: str = ""
    account_name: str = ""
    author: str = ""
    publish_time: str = ""          # 可读时间
    publish_ts: Optional[int] = None  # Unix 秒
    cover: str = ""
    avatar: str = ""
    biz: str = ""
    digest: str = ""
    images: List[str] = field(default_factory=list)  # 已下载的本地文件名

    def to_dict(self):
        return asdict(self)


@dataclass
class Article:
    meta: ArticleMeta
    content_html: str   # 清洗后的正文 HTML（img 的 src 已替换为本地相对路径）
    content_md: str     # 正文 Markdown
    album_urls: List[str] = field(default_factory=list)  # 页面中发现的合集链接


# ---------------------------------------------------------------------- #
def _clean_text(s: str) -> str:
    return (s or "").strip()


def _html_to_markdown(content: BeautifulSoup, images_map: dict) -> str:
    """极简 HTML -> Markdown 转换（保留标题/段落/列表/引用/图片/代码块）。"""
    lines: List[str] = []

    def walk(node, depth=0):
        from bs4 import NavigableString, Tag

        if isinstance(node, NavigableString):
            text = str(node).strip()
            if text:
                lines.append(text)
            return
        if not isinstance(node, Tag):
            return
        name = node.name.lower()
        if name in ("script", "style"):
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = int(name[1])
            lines.append(f"\n{'#' * lvl} {node.get_text(strip=True)}\n")
        elif name == "img":
            src = node.get("src") or node.get("data-src") or ""
            local = images_map.get(src, src)
            alt = node.get("alt") or "image"
            if local:
                lines.append(f"\n![{alt}]({local})\n")
        elif name in ("p", "section", "div"):
            text_parts = []
            # 直接渲染子节点中可能包含的图片
            for img in node.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src and src in images_map:
                    img.replace_with(f"![image]({images_map[src]})")
            text = node.get_text(" ", strip=True)
            if text:
                lines.append("\n" + text + "\n")
            return  # 不再递归，避免重复
        elif name in ("blockquote",):
            text = node.get_text(" ", strip=True)
            if text:
                lines.append("\n> " + text.replace("\n", "\n> ") + "\n")
            return
        elif name in ("li",):
            lines.append("- " + node.get_text(" ", strip=True))
            return
        elif name in ("pre", "code"):
            if name == "pre":
                lines.append("\n```\n" + node.get_text() + "\n```\n")
                return
        for child in node.children:
            walk(child, depth + 1)

    walk(content)
    # 合并多余空行
    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


# ---------------------------------------------------------------------- #
def parse_article_html(html: str, url: str) -> Article:
    """从文章页原始 HTML 解析出 Article。"""
    soup = BeautifulSoup(html, "html.parser")
    meta = ArticleMeta(url=url)

    # ---- 标题 ----
    h1 = soup.select_one("h1#activity-name")
    meta.title = _clean_text(h1.get_text()) if h1 else ""
    if not meta.title:
        meta.title = _js_var(html, "msg_title") or ""
    if not meta.title:
        t = soup.find("title")
        meta.title = _clean_text(t.get_text()) if t else "untitled"

    # ---- 公众号名 ----
    a_name = soup.select_one("a#js_name")
    meta.account_name = _clean_text(a_name.get_text()) if a_name else ""
    if not meta.account_name:
        meta.account_name = _js_var(html, "nickname") or ""

    # ---- 作者 ----
    author_el = soup.select_one("#js_author_name")
    meta.author = _clean_text(author_el.get_text()) if author_el else ""
    if not meta.author:
        meta.author = _js_var(html, "author") or ""

    # ---- 发布时间：原始 HTML 中 #publish_time 为空，必须取 var ct ----
    ct = _js_var(html, "ct")
    if ct and ct.isdigit():
        meta.publish_ts = int(ct)
        meta.publish_time = ts_to_str(ct)
    else:
        pt = soup.select_one("#publish_time")
        meta.publish_time = _clean_text(pt.get_text()) if pt else ""

    # ---- 封面 ----
    og = soup.find("meta", attrs={"property": "og:image"})
    meta.cover = (og.get("content") or "") if og else ""
    if not meta.cover:
        meta.cover = _js_var(html, "msg_cdn_url") or ""

    # ---- 头像 / 简介 ----
    meta.avatar = _js_var(html, "round_head_img") or ""
    meta.digest = _js_var(html, "msg_desc") or ""

    # ---- __biz ----
    m = _BIZ_PAT.search(html)
    if m:
        meta.biz = m.group(1)
    if not meta.biz:
        # 新版页面用 var biz = "..."（HTML 里只有 ${window.biz} 占位）
        meta.biz = _js_var(html, "biz") or ""
    if not meta.biz:
        m = _BIZ_PAT.search(url)
        if m:
            meta.biz = m.group(1)

    # ---- 正文 ----
    content = soup.select_one("div#js_content")
    if content:
        # 去掉 visibility:hidden
        if content.has_attr("style"):
            style = content["style"].replace("visibility: hidden", "").replace("visibility:hidden", "")
            if style.strip().strip(";"):
                content["style"] = style
            else:
                del content["style"]
        # 剔除 display:none 节点
        for el in content.select('[style*="display:none"], [style*="display: none"]'):
            el.decompose()
        # 图片懒加载：data-src -> src
        for img in content.find_all("img"):
            real = img.get("data-src") or img.get("src")
            if real:
                img["src"] = real
    content_html = str(content) if content else ""
    md = _html_to_markdown(content, {}) if content else ""

    # ---- 合集链接（C 线用） ----
    album_urls = _ALBUM_PAT.findall(html)
    # 去重并解码 &amp;
    seen, albums = set(), []
    for u in album_urls:
        u = html_unescape(u)
        if u not in seen:
            seen.add(u)
            albums.append(u)

    return Article(meta=meta, content_html=content_html, content_md=md, album_urls=albums)


def fetch_article(fetcher: Fetcher, url: str) -> Article:
    """抓取并解析单篇文章。成功判据 = 响应含正文容器与标题节点。"""
    # 拦截/验证页可能碰巧包含 "js_content" 字样（JS 模板），
    # 用标题节点 "activity-name" 作为成功标记更可靠。
    html = fetcher.get_text(url, require_marker="activity-name")
    art = parse_article_html(html, url)
    if not art.meta.title and not art.meta.account_name and not art.content_html.strip():
        raise FetchError(f"页面解析结果为空，疑似拦截页或页面结构变更: {url}")
    return art


def extract_biz_from_html(html: str) -> str:
    m = _BIZ_PAT.search(html or "")
    return m.group(1) if m else ""
