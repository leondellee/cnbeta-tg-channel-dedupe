# -*- coding: utf-8 -*-
"""解析 t.me/s/<channel> 公开预览页，适配 cnBeta 系列推送频道。

实测模板（2026-11）：

    <div class="tgme_widget_message ..." data-post="cnbeta_com/480225">
      ...
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        <a href="https://www.cnbeta.com.tw/articles/soft/1584976.htm"><b>标题</b></a><br/><br/>
        <a href="https://t.me/cnbeta_com"><u>[精选新闻]</u></a> ·
        <a href="https://t.me/cnbeta_com_tech"><u>[软硬件技术]</u></a> · ...
      </div>
      <a class="tgme_widget_message_link_preview" href="原文地址">
        <i class="link_preview_right_image" style="background-image:url('telesco 图床')"></i>
        <div class="link_preview_site_name">cnBeta.COM</div>
        <div class="link_preview_title">标题</div>
        <div class="link_preview_description">摘要</div>
      </a>
    </div>

要点：
- 标题在正文首个 <a><b> 里，该 <a> 的 href 就是原文地址 —— 这是跨频道去重的天然唯一键。
- 摘要与配图在 link preview 区块，配图为 800x450（telesco CDN），足够直接当卡片图。
- 五个频道正文里的 [分类] 标签是全量列表（每条都列五个），不代表本条归属，
  因此分类另由「来源频道」推断，见 service.py 的 CHANNELS。
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field


@dataclass
class Post:
    post_id: str            # "cnbeta_com/480225"
    channel: str            # "cnbeta_com"
    msg_id: int
    date: str               # ISO8601，如 2026-11-04T07:10:43+00:00
    title: str
    summary: str
    article_url: str
    image_url: str = ""
    site: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def link(self) -> str:
        return f"https://t.me/{self.post_id}"


_BLOCK = re.compile(r'(?=<div class="tgme_widget_message(?:\s[^"]*)?")', re.S)
_POST = re.compile(r'data-post="([^"]+)"')

_TEXT_DIV = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
# 标题锚点：<a href="原文"><b>标题</b></a>
_TITLE_LINK = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>\s*<b>(.*?)</b>\s*</a>', re.S)
_ANY_BOLD = re.compile(r"<b>(.*?)</b>", re.S)

_PREVIEW = re.compile(r'class="tgme_widget_message_link_preview"[^>]*href="([^"]+)"')
_DESC = re.compile(
    r'<div class="link_preview_description[^"]*"[^>]*>(.*?)</div>', re.S)
_SITE = re.compile(
    r'<div class="link_preview_site_name[^"]*"[^>]*>(.*?)</div>', re.S)
_IMAGE = re.compile(r"background-image:url\(['\"]?(https://[^'\")]+)", re.S)
_DATE = re.compile(r'datetime="([^"]+)"')
_TAG = re.compile(r"<u>\[([^\]]+)\]</u>")
_VIEWS = re.compile(r'class="tgme_widget_message_views">([^<]+)<')


def _clean(fragment: str) -> str:
    """去标签 + 反转义 + 归一化空白（保留换行）。"""
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html_mod.unescape(fragment)
    fragment = fragment.replace("\u200b", "").replace("\xa0", " ")
    lines = [ln.strip() for ln in fragment.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def _clean_title(fragment: str) -> str:
    """标题行内的多余空白压成单空格。"""
    return re.sub(r"\s+", " ", _clean(fragment)).strip()


def parse(html_text: str) -> list[Post]:
    posts: list[Post] = []
    for chunk in _BLOCK.split(html_text):
        m = _POST.search(chunk[:400])
        if not m:
            continue
        post_id = m.group(1)
        channel = post_id.rsplit("/", 1)[0]
        try:
            msg_id = int(post_id.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            msg_id = 0

        text_m = _TEXT_DIV.search(chunk)
        text_html = text_m.group(1) if text_m else ""

        title, article_url = "", ""
        tm = _TITLE_LINK.search(text_html)
        if tm:
            article_url = html_mod.unescape(tm.group(1)).replace("&amp;", "&")
            title = _clean_title(tm.group(2))
        else:
            bm = _ANY_BOLD.search(text_html)
            if bm:
                title = _clean_title(bm.group(1))

        tags = [_clean(t) for t in _TAG.findall(text_html)]

        # 无预览块时，正文首行兜底当标题，其余当摘要
        plain = _clean(text_html)
        lines = [ln for ln in plain.split("\n") if ln.strip()]
        if not title and lines:
            title = lines[0][:200]
        if not article_url:
            pm = _PREVIEW.search(chunk)
            if pm:
                article_url = html_mod.unescape(pm.group(1))

        desc_m = _DESC.search(chunk)
        summary = _clean(desc_m.group(1)) if desc_m else ""
        if not summary:
            body = [ln for ln in lines if ln != title and not re.fullmatch(r"\[.*?\]", ln)]
            summary = " ".join(body)[:300]

        site_m = _SITE.search(chunk)
        site = _clean(site_m.group(1)) if site_m else ""

        img_m = _IMAGE.search(chunk)
        image_url = html_mod.unescape(img_m.group(1)) if img_m else ""

        date_m = _DATE.search(chunk)
        date = date_m.group(1) if date_m else ""

        if not title:
            continue

        posts.append(Post(
            post_id=post_id, channel=channel, msg_id=msg_id, date=date,
            title=title, summary=summary, article_url=article_url,
            image_url=image_url, site=site, tags=tags,
        ))
    return posts


if __name__ == "__main__":  # 手工调试：python src/parser.py 页面.html
    import sys
    from pathlib import Path

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/cnbeta_com.html")
    ps = parse(src.read_text(encoding="utf-8"))
    print(f"解析到 {len(ps)} 条\n")
    for p in ps[:5]:
        print(f"[{p.post_id}] {p.title}")
        print(f"  原文={p.article_url}")
        print(f"  摘要={p.summary[:80]}")
        print(f"  图={p.image_url[:60]}  时间={p.date}")
        print()
