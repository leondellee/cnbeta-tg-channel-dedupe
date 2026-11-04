# -*- coding: utf-8 -*-
"""去重核心：跨频道判重 + 状态持久化。

判重分三层，从硬到软：

1. **原文地址**（主键）—— 同一条 cnBeta 文章被推到 5 个频道时，href 完全一致，
   只差主机名（cnbeta.com / cnbeta.com.tw / m.cnbeta.com）。归一化后是天然唯一键。
   实测 100 条样本里 85 个唯一地址 / 85 个唯一标题，一一对应，无碰撞。

2. **标题指纹**（兜底）—— 地址缺失或换域名时用；只在 6 小时窗口内生效，
   避免把「同名但不同文章」（如系列报道）误判成重复。

3. **同轮合并**（in-run）—— 同一轮抓取里，一篇文章出现在多个频道，先归组成一条，
   再决定发不发。归组时保留摘要最长的那条，分类取各频道并集。

状态文件 output/state.json 形如：

    {
      "version": 2,
      "initialized": true,
      "updated": "2026-11-04T08:00:00+08:00",
      "seen": {
        "u:cnbeta.com.tw/articles/soft/1584976.htm": [1793780218, "a1b2c3d4e5"],
        ...
      }
    }

键 = "u:" + 归一化地址（或 "t:" + 标题指纹），值 = [首次时间戳, 标题指纹]。
只保留 7 天，条目上限 4000，避免状态文件无限膨胀（它是每次提交回仓库的）。
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from parser import Post

STATE_VERSION = 2
RETENTION_SEC = 7 * 24 * 3600      # 状态保留 7 天
MAX_ENTRIES = 4000
TITLE_WINDOW_SEC = 6 * 3600        # 标题兜底判重的生效窗口

_HOST_ALIASES = {"cnbeta.com", "m.cnbeta.com", "www.cnbeta.com",
                 "cnbeta.com.tw", "m.cnbeta.com.tw", "www.cnbeta.com.tw"}
_CANON_HOST = "cnbeta.com.tw"


def normalize_url(url: str) -> str:
    """归一化原文地址：忽略协议/主机别名/查询串/锚点/末尾斜杠，统一小写。"""
    if not url:
        return ""
    url = url.strip()
    try:
        p = urlsplit(url)
    except ValueError:
        return url.lower()
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in _HOST_ALIASES or host.endswith(".cnbeta.com"):
        host = _CANON_HOST
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return f"{host}{path}".lower()


def title_fingerprint(title: str) -> str:
    """标题指纹：去掉标点/空白/大小写差异后取 sha1 前 10 位。

    中文标题里的「？！」「：」「·」等在不同频道偶尔有细微差别，故全部剔除。
    """
    t = (title or "").lower()
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    if not t:
        return ""
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]


def post_key(post: Post) -> str:
    u = normalize_url(post.article_url)
    if u and u.count("/") >= 2:          # 至少 host/段/文件，避免把首页当键
        return "u:" + u
    fp = title_fingerprint(post.title)
    return "t:" + fp if fp else ""


_ID_RE = re.compile(r"/(\d{4,})\.htm")


def article_id(url: str) -> str:
    """取 cnBeta 的文章数字 ID（如 1584994）。

    必要原因：/articles/soft/1584994.htm 会 301 跳到 /articles/tech/1584994.htm
    （换栏目），地址归一化后路径不同，但其实是同一篇文章。ID 才是真唯一键。
    """
    m = _ID_RE.search(url or "")
    return m.group(1) if m else ""


def parse_ts(date: str) -> float:
    """ISO8601 → 时间戳；失败返回 0（排序时会排到最前，不会丢内容）。"""
    if not date:
        return 0.0
    try:
        return datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


@dataclass
class Group:
    """同一篇文章的多频道副本。"""
    key: str
    posts: list[Post] = field(default_factory=list)

    def add(self, post: Post) -> None:
        self.posts.append(post)

    @property
    def first_ts(self) -> float:
        return min((parse_ts(p.date) for p in self.posts if p.date), default=0.0)

    @property
    def channels(self) -> list[str]:
        """按首次出现顺序去重的来源频道。"""
        out: list[str] = []
        for p in sorted(self.posts, key=lambda x: parse_ts(x.date)):
            if p.channel not in out:
                out.append(p.channel)
        return out

    @property
    def primary(self) -> Post:
        """代表条目：摘要最长者优先（信息最全），其次最早。"""
        return max(self.posts, key=lambda p: (len(p.summary), -parse_ts(p.date)))

    @property
    def title_fp(self) -> str:
        return title_fingerprint(self.primary.title)


def group_posts(posts: list[Post]) -> list[Group]:
    """按判重键归组，保持组内时间顺序。"""
    buckets: dict[str, Group] = {}
    for p in posts:
        k = post_key(p)
        if not k:
            continue
        if k not in buckets:
            buckets[k] = Group(key=k)
        buckets[k].add(p)
    return sorted(buckets.values(), key=lambda g: g.first_ts)


class Deduper:
    def __init__(self, state: dict | None = None):
        # 注意：不能用 `state or {}`——空 dict 是 falsy，会丢掉调用方的引用，
        # 导致冷启动时 run_once 填好的状态写不回传入的 state。
        self.state = state if state is not None else {}
        self.state.setdefault("version", STATE_VERSION)
        self.state.setdefault("initialized", False)
        self.seen: dict[str, list] = self.state.setdefault("seen", {})
        self._title_index: dict[str, float] = {}
        for entry in self.seen.values():
            if isinstance(entry, list) and len(entry) >= 2 and entry[1]:
                self._title_index[entry[1]] = max(
                    self._title_index.get(entry[1], 0), float(entry[0]))

        # 文章 ID 索引直接从已有的 u: 键里解析，不需要改状态格式
        self._id_index: dict[str, float] = {}
        for k, v in self.seen.items():
            aid = article_id(k)
            if aid and isinstance(v, list) and v:
                self._id_index[aid] = max(self._id_index.get(aid, 0), float(v[0]))

    # -- 判定 ---------------------------------------------------------------
    def is_duplicate(self, group: Group) -> tuple[bool, str]:
        if group.key in self.seen:
            return True, "原文地址已收录"
        aid = article_id(group.primary.article_url)
        if aid and aid in self._id_index:
            return True, f"文章 ID {aid} 已收录（换了栏目路径）"
        fp = group.title_fp
        if fp:
            last = self._title_index.get(fp, 0)
            if last and (time.time() - last) <= TITLE_WINDOW_SEC:
                return True, "标题指纹在 6 小时内出现过"
        return False, ""

    # -- 记账 ---------------------------------------------------------------
    def mark(self, group: Group, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        fp = group.title_fp
        self.seen[group.key] = [int(ts), fp]
        if fp:
            self._title_index[fp] = max(self._title_index.get(fp, 0), ts)
        aid = article_id(group.primary.article_url)
        if aid:
            self._id_index[aid] = max(self._id_index.get(aid, 0), ts)

    def mark_all(self, groups: list[Group]) -> None:
        now = time.time()
        for g in groups:
            self.mark(g, now)

    # -- 维护 ---------------------------------------------------------------
    def prune(self, now: float | None = None) -> int:
        now = now or time.time()
        cutoff = now - RETENTION_SEC
        drop = [k for k, v in self.seen.items()
                if not isinstance(v, list) or not v or float(v[0]) < cutoff]
        for k in drop:
            self.seen.pop(k, None)
        if len(self.seen) > MAX_ENTRIES:      # 兜底：按时间保留最近的
            ordered = sorted(self.seen.items(), key=lambda kv: float(kv[1][0]),
                             reverse=True)[:MAX_ENTRIES]
            self.seen = dict(ordered)
        self._title_index = {v[1]: float(v[0]) for v in self.seen.values()
                             if isinstance(v, list) and len(v) >= 2 and v[1]}
        self._id_index = {}
        for k, v in self.seen.items():
            aid = article_id(k)
            if aid and isinstance(v, list) and v:
                self._id_index[aid] = max(self._id_index.get(aid, 0), float(v[0]))
        return len(drop)

    def to_dict(self) -> dict:
        self.state["seen"] = self.seen
        self.state["updated"] = datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds")
        return self.state

    @property
    def initialized(self) -> bool:
        return bool(self.state.get("initialized"))

    def set_initialized(self) -> None:
        self.state["initialized"] = True

    def stats(self) -> dict:
        now = time.time()
        recent = sum(1 for v in self.seen.values()
                     if isinstance(v, list) and now - float(v[0]) < 86400)
        return {"total": len(self.seen), "last_24h": recent}
