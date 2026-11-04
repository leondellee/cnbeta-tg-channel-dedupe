# -*- coding: utf-8 -*-
"""回归测试：解析 / 去重 / 归组。

数据用 2026-11-04 抓的 5 个真实频道预览页（tests/fixtures），
共 100 条帖子、85 篇唯一文章 —— 那 15 条重复正是要合并掉的。

跑法：
    python tests/test_dedupe.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dedupe import (Deduper, group_posts, normalize_url,  # noqa: E402
                    post_key, title_fingerprint)
from parser import parse  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
CHANNELS = ["cnbeta_com", "cnbeta_com_tech", "cnbeta_com_business",
            "cnbeta_com_media", "cnbeta_com_buzz"]

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}" + (f"  ← {detail}" if detail else ""))


def load_all():
    out = []
    for ch in CHANNELS:
        html = (FIXTURES / f"{ch}.html").read_text(encoding="utf-8")
        out.extend(parse(html))
    return out


def main() -> None:
    posts = load_all()

    print("\n[1] 解析")
    check("5 个频道共 100 条", len(posts) == 100, f"实际 {len(posts)}")
    check("每条都有标题", all(p.title for p in posts))
    check("每条都有原文地址", all(p.article_url.startswith("http") for p in posts),
          next((p.post_id for p in posts if not p.article_url), ""))
    check("每条都有时间", all(p.date for p in posts))
    check("每条都有摘要", all(p.summary for p in posts),
          next((p.post_id for p in posts if not p.summary), ""))
    check("绝大多数带配图", sum(1 for p in posts if p.image_url) >= 90,
          f"{sum(1 for p in posts if p.image_url)}/100")
    check("标题不含 HTML 标签", not any("<" in p.title for p in posts))
    check("摘要不含 [分类] 残留行", not any(p.summary.startswith("[") for p in posts))

    print("\n[2] 地址归一化")
    same = [
        "https://www.cnbeta.com.tw/articles/soft/1584976.htm",
        "http://cnbeta.com.tw/articles/soft/1584976.htm",
        "https://m.cnbeta.com.tw/articles/soft/1584976.htm",
        "https://www.cnbeta.com/articles/soft/1584976.htm?utm=x#top",
        "https://cnbeta.com.tw/articles/soft/1584976.htm/",
    ]
    norms = {normalize_url(u) for u in same}
    check("5 种写法归一为 1 个", len(norms) == 1, str(norms))
    check("归一结果正确",
          norms.pop() == "cnbeta.com.tw/articles/soft/1584976.htm")
    check("不同文章不撞键",
          normalize_url("https://www.cnbeta.com.tw/articles/soft/1584976.htm")
          != normalize_url("https://www.cnbeta.com.tw/articles/soft/1584977.htm"))

    print("\n[3] 标题指纹")
    f1 = title_fingerprint("窃听门本可避免？消息称OpenAI员工已警告安全漏洞多年")
    f2 = title_fingerprint("窃听门本可避免? 消息称 OpenAI 员工已警告安全漏洞多年！")
    f3 = title_fingerprint("苹果天气闹乌龙：多地现零下30℃，官方致歉")
    check("标点/空格/问号差异不改变指纹", f1 == f2, f"{f1} vs {f2}")
    check("不同标题指纹不同", f1 != f3)

    print("\n[4] 同轮归组（跨频道合并）")
    groups = group_posts(posts)
    check("100 条归并为 85 篇", len(groups) == 85, f"实际 {len(groups)}")
    multi = [g for g in groups if len(g.channels) > 1]
    check("跨频道重复 13 篇", len(multi) == 13, f"实际 {len(multi)}")
    check("单篇最多 3 个频道", max(len(g.channels) for g in groups) == 3,
          str(max(len(g.channels) for g in groups)))
    check("归组按时间正序",
          all(groups[i].first_ts <= groups[i + 1].first_ts
              for i in range(len(groups) - 1)))
    g0 = multi[0]
    check("合并后 primary 取摘要最长者",
          len(g0.primary.summary) == max(len(p.summary) for p in g0.posts))
    check("同一个键的文章地址一致",
          len({normalize_url(p.article_url) for p in g0.posts}) == 1)

    print("\n[5] 跨轮去重")
    state: dict = {}
    d1 = Deduper(state)
    check("初始未初始化", not d1.initialized)
    d1.mark_all(groups)
    d1.set_initialized()
    dup_flags = [d1.is_duplicate(g)[0] for g in groups]
    check("全部标记后 85 篇均判为重复", all(dup_flags), f"{sum(dup_flags)}/85 命中")

    state2 = d1.to_dict()
    d2 = Deduper(state2)
    check("状态可反序列化并保持 initialized", d2.initialized)
    check("重启后仍然全部命中", all(d2.is_duplicate(g)[0] for g in groups))

    # 同一篇文章换域名 + 改标题标点，仍应判重
    src = groups[0].posts[0]
    clone = type(src)(**{**src.__dict__,
                         "article_url": src.article_url.replace("cnbeta.com.tw",
                                                                "cnbeta.com"),
                         "title": src.title + "！",
                         "post_id": "x/" + str(src.msg_id),
                         "channel": "x"})
    check("换域名+改标点仍判重", d2.is_duplicate(group_posts([clone])[0])[0])
    check("判重原因标注为地址", "地址" in d2.is_duplicate(group_posts([clone])[0])[1])

    print("\n[6] 标题兜底（地址缺失时）")
    d3 = Deduper({})
    p = groups[0].posts[0]
    fake = type(p)(**{**p.__dict__, "article_url": "", "post_id": "x/1"})
    g = group_posts([fake])
    check("无地址时用标题作键", g and g[0].key.startswith("t:"), g[0].key if g else "")
    d3.mark(g[0])
    fake2 = type(p)(**{**p.__dict__, "article_url": "", "post_id": "x/2",
                       "title": p.title.replace("？", "?")})
    check("无地址时标题差异也能判重",
          d3.is_duplicate(group_posts([fake2])[0])[0])

    print("\n[7] 状态清理")
    d4 = Deduper({})
    old = type(p)(**{**p.__dict__, "article_url": "https://cnbeta.com.tw/a/1.htm"})
    g_old = group_posts([old])[0]
    d4.mark(g_old, time.time() - 8 * 86400)
    new = type(p)(**{**p.__dict__, "article_url": "https://cnbeta.com.tw/a/2.htm"})
    d4.mark(group_posts([new])[0])
    check("超过 7 天的记录被清掉", d4.prune() == 1, f"清了 {len(d4.seen)}")
    check("未过期记录保留", len(d4.seen) == 1)

    print(f"\n{'=' * 46}\n通过 {passed} 项，失败 {failed} 项\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
