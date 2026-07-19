"""通用工具：文本清洗、文件名安全化、JSON 读写等。"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

CN_TZ = timezone(timedelta(hours=8))

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_WHITESPACE = re.compile(r"\s+")


def safe_filename(name: str, max_len: int = 80) -> str:
    """把任意字符串转成安全的文件/目录名。"""
    name = _INVALID_CHARS.sub("_", (name or "").strip())
    name = _WHITESPACE.sub(" ", name).strip(" .")
    if not name:
        name = "untitled"
    return name[:max_len]


def html_unescape(s: str) -> str:
    import html
    return html.unescape(s or "")


def strip_js_string(s: str) -> str:
    """处理微信内嵌 JS 变量里的转义字符串（如 \\x26）。"""
    if not s:
        return ""
    try:
        # 微信常用 \xHH 转义
        return s.encode("utf-8").decode("unicode_escape").encode("latin-1", "ignore").decode("utf-8", "ignore") if "\\x" in s else s
    except Exception:
        return s


def ts_to_str(ts, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Unix 秒 -> 东八区时间字符串；非法输入原样返回。"""
    try:
        return datetime.fromtimestamp(int(ts), tz=CN_TZ).strftime(fmt)
    except (TypeError, ValueError, OSError):
        return str(ts or "")


def now_str() -> str:
    return datetime.now(tz=CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def normalize_url(url: str) -> str:
    return (url or "").strip()


def dedupe_keep_order(items, key):
    seen = set()
    out = []
    for it in items:
        k = key(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def sleep_random(low: float, high: float) -> None:
    import random
    time.sleep(random.uniform(low, high))
