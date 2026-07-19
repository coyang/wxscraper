"""存储层：文章落盘、账号索引、checkpoint 断点续爬。"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from .article import Article
from .fetcher import Fetcher
from .utils import read_json, safe_filename, ts_to_str, write_json, now_str

log = logging.getLogger("wxscraper.storage")

IMG_EXT_PAT = re.compile(r"\.(jpe?g|png|gif|webp|bmp|svg)", re.I)


def _img_ext(url: str, content_type: str = "") -> str:
    path = urlparse(url).path
    m = IMG_EXT_PAT.search(path)
    if m:
        return "." + m.group(1).lower().replace("jpeg", "jpg")
    ct_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
              "image/webp": ".webp", "image/svg+xml": ".svg"}
    return ct_map.get((content_type or "").split(";")[0].strip(), ".jpg")


class Checkpoint:
    """output/<号>/.checkpoint.json —— 记录已抓链接，重跑自动跳过。"""

    def __init__(self, account_dir: str):
        self.path = os.path.join(account_dir, ".checkpoint.json")
        self.data = read_json(self.path, {"done_urls": [], "albums_done": [], "backend": {}})

    def is_done(self, url: str) -> bool:
        return url in set(self.data.get("done_urls") or [])

    def mark_done(self, url: str) -> None:
        urls = self.data.setdefault("done_urls", [])
        if url not in urls:
            urls.append(url)

    def album_done(self, album_id: str) -> bool:
        return album_id in set(self.data.get("albums_done") or [])

    def mark_album_done(self, album_id: str) -> None:
        lst = self.data.setdefault("albums_done", [])
        if album_id not in lst:
            lst.append(album_id)

    def save(self) -> None:
        write_json(self.path, self.data)


class AccountStore:
    """一个公众号的输出目录。"""

    def __init__(self, output_root: str, account_name: str):
        self.account_name = safe_filename(account_name or "unknown_account")
        self.dir = os.path.join(output_root, self.account_name)
        os.makedirs(self.dir, exist_ok=True)
        self.checkpoint = Checkpoint(self.dir)

    # ------------------------------------------------------------------ #
    def save_account(self, info: dict) -> None:
        """写 account.json。"""
        existing = read_json(os.path.join(self.dir, "account.json"), {})
        existing.update({k: v for k, v in info.items() if v})
        existing["updated_at"] = now_str()
        write_json(os.path.join(self.dir, "account.json"), existing)

    # ------------------------------------------------------------------ #
    def _article_dir(self, art: Article, publish_ts: Optional[int]) -> str:
        date_prefix = ts_to_str(publish_ts, "%Y%m%d") if publish_ts else "undated"
        dirname = f"{date_prefix}_{safe_filename(art.meta.title, 60)}"
        d = os.path.join(self.dir, dirname)
        os.makedirs(d, exist_ok=True)
        return d

    def save_article(self, art: Article, fetcher: Optional[Fetcher] = None,
                     download_images: bool = True) -> str:
        """保存单篇文章：title.html / title.md / meta.json / images/。返回相对路径。"""
        adir = self._article_dir(art, art.meta.publish_ts)
        base = safe_filename(art.meta.title, 60)
        images_map = {}

        # ---- 下载图片并替换 src ----
        if download_images and fetcher is not None:
            img_dir = os.path.join(adir, "images")
            urls = re.findall(r'<img[^>]+src="([^"]+)"', art.content_html)
            n = 0
            for u in dict.fromkeys(urls):  # 去重保序
                if not u.startswith("http"):
                    continue
                n += 1
                try:
                    resp = fetcher.get(
                        u, referer="https://mp.weixin.qq.com/", delay=(0.5, 1.5)
                    )
                    ext = _img_ext(u, resp.headers.get("Content-Type", ""))
                    fname = f"{n:03d}{ext}"
                    os.makedirs(img_dir, exist_ok=True)
                    with open(os.path.join(img_dir, fname), "wb") as f:
                        f.write(resp.content)
                    images_map[u] = f"images/{fname}"
                    art.meta.images.append(fname)
                except Exception as e:  # 单张图失败不中断
                    log.warning("图片下载失败 %s: %s", u, e)

        html_out = art.content_html
        for u, local in images_map.items():
            html_out = html_out.replace(f'src="{u}"', f'src="{local}"')
        # Markdown 同步替换
        md_out = art.content_md
        for u, local in images_map.items():
            md_out = md_out.replace(u, local)

        with open(os.path.join(adir, base + ".html"), "w", encoding="utf-8") as f:
            f.write(self._wrap_html(art.meta.title, html_out))
        with open(os.path.join(adir, base + ".md"), "w", encoding="utf-8") as f:
            f.write(f"# {art.meta.title}\n\n")
            f.write(f"> 公众号：{art.meta.account_name}  \n")
            f.write(f"> 作者：{art.meta.author or '-'}  \n")
            f.write(f"> 发布时间：{art.meta.publish_time or '-'}  \n")
            f.write(f"> 原文链接：{art.meta.url}\n\n---\n\n")
            f.write(md_out)
        write_json(os.path.join(adir, "meta.json"), art.meta.to_dict())

        rel = os.path.relpath(adir, self.dir).replace(os.sep, "/")
        self._update_index(art, rel)
        return rel

    @staticmethod
    def _wrap_html(title: str, body: str) -> str:
        return (
            "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<title>{title}</title>"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<style>body{max-width:720px;margin:2em auto;padding:0 1em;"
            "font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "line-height:1.8;color:#333}img{max-width:100%;height:auto}</style>"
            f"</head><body><h1>{title}</h1>{body}</body></html>"
        )

    # ------------------------------------------------------------------ #
    def _update_index(self, art: Article, rel_path: str) -> None:
        """更新账号级 index.json。"""
        path = os.path.join(self.dir, "index.json")
        idx = read_json(path, {"account": self.account_name, "articles": []})
        arts: List[dict] = idx.setdefault("articles", [])
        # 同一篇文章可能以多种 URL 形态被抓到（/s/短链、__biz&mid&sn 长链、
        # 合集里的 #rd 链接），URL 不同但是同一篇。除按 URL 去重外，
        # 再按 local_path（日期+标题，即落盘目录）去重，避免索引重复。
        arts[:] = [
            a for a in arts
            if a.get("url") != art.meta.url and a.get("local_path") != rel_path
        ]
        arts.append(
            {
                "title": art.meta.title,
                "publish_time": art.meta.publish_time,
                "publish_ts": art.meta.publish_ts,
                "url": art.meta.url,
                "author": art.meta.author,
                "local_path": rel_path,
            }
        )
        arts.sort(key=lambda a: a.get("publish_ts") or 0, reverse=True)
        idx["account"] = art.meta.account_name or self.account_name
        idx["total"] = len(arts)
        idx["updated_at"] = now_str()
        write_json(path, idx)
