# -*- coding: utf-8 -*-
"""原型：把 cnBeta 文章正文转成 Telegraph 页面（Telegraph 天然支持 Instant View）。

只验证可行性，不进主流程。用法：
    python tools/telegraph_probe.py <文章地址>
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sys
import urllib.parse
import urllib.request

PROXY = "http://127.0.0.1:7890"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""


def opener(proxy=PROXY):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return opener().open(req, timeout=timeout).read().decode("utf-8", "ignore")


def slice_div(html_text: str, start_idx: int) -> str:
    """从 <div ...> 的起始位置切出配平的整块（cnBeta 正文里有嵌套 div）。"""
    depth = 0
    i = start_idx
    n = len(html_text)
    while i < n:
        m = re.compile(r"<(/?)div\b", re.I).search(html_text, i)
        if not m:
            break
        if m.group(1):
            depth -= 1
            if depth == 0:
                return html_text[start_idx:m.end() + 1]
        else:
            depth += 1
        i = m.end()
    return html_text[start_idx:]


def to_nodes(body_html: str) -> list[dict]:
    nodes: list[dict] = []
    # 按块级标签切
    for m in re.finditer(r"<(p|h2|h3|blockquote)\b[^>]*>(.*?)</\1>", body_html, re.S | re.I):
        inner = m.group(2)
        imgs = re.findall(r'<img[^>]+src="([^"]+)"', inner, re.I)
        for src in imgs:
            if src.startswith("http"):
                nodes.append({"tag": "figure",
                              "children": [{"tag": "img", "attrs": {"src": src}}]})
        text = re.sub(r"<br\s*/?>", "\n", inner)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_mod.unescape(text).replace("\u200b", " ").strip()
        if len(text) >= 2:
            nodes.append({"tag": "p", "children": [text]})
    return nodes


def main() -> None:
    url = sys.argv[1]
    h = get(url)
    title_m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', h)
    title = html_mod.unescape(title_m.group(1)) if title_m else "无标题"
    title = re.sub(r"\s*[-–—]\s*[^-]*cnBeta\.COM\s*$", "", title).strip()

    idx = h.find('class="article-content"')
    body_html = slice_div(h, h.rfind("<div", 0, idx)) if idx > 0 else ""
    nodes = to_nodes(body_html)

    print(f"标题: {title}")
    print(f"正文块: {len(nodes)} 个，字符数 {sum(len(str(n)) for n in nodes)}")

    if not TOKEN:
        print("（未传 access_token，仅解析不建页）")
        print(json.dumps(nodes[:3], ensure_ascii=False, indent=1)[:800])
        return

    data = urllib.parse.urlencode({
        "access_token": TOKEN,
        "title": title[:256],
        "author_name": "cnBeta",
        "content": json.dumps(nodes, ensure_ascii=False),
        "return_content": "false",
    }).encode()
    req = urllib.request.Request("https://api.telegra.ph/createPage", data=data)
    r = json.loads(opener().open(req, timeout=30).read().decode())
    print("ok:", r.get("ok"), r.get("error"))
    if r.get("ok"):
        print("页面:", r["result"]["url"])
        print("路径:", r["result"]["path"])


if __name__ == "__main__":
    main()
