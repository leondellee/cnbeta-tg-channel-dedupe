# -*- coding: utf-8 -*-
"""卡片渲染 + Telegram 推送（纯标准库，零第三方依赖）。

卡片形态（Leon 2026-11-04 定稿：完整卡片）：

    <b>窃听门本可避免？消息称OpenAI员工已警告安全漏洞多年</b>

    11月4日，据路透社报道，OpenAI 在今年 10 月披露……

    分类：精选 · 技术
    原文：cnbeta.com.tw

配图取自 t.me 预览页的 telesco CDN（实测 800x450，够清晰），
用 sendPhoto 上传、caption 放卡片正文；取图失败自动降级为 sendMessage 纯文本，
不阻塞推送。
"""

from __future__ import annotations

import html as html_mod
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

API = "https://api.telegram.org/bot{token}/{method}"

MAX_CAPTION = 1024          # sendPhoto caption 上限
MAX_TEXT = 4096             # sendMessage 上限
MIN_SUMMARY = 90            # 摘要至少保留的字数


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def _shorten(text: str, limit: int) -> str:
    text = (text or "").strip()
    if limit <= 1:
        return ""
    if len(text) <= limit:
        return text
    # 尽量在句号处收尾，读起来不像被截断
    cut = text[:limit]
    for sep in ("。", "！", "？", "…", "；", ".", "!", "?"):
        idx = cut.rfind(sep)
        if idx >= limit * 0.6:
            return cut[: idx + 1]
    return cut.rstrip("，,、 ") + "…"


def build_card(post, channels: list[str] | None = None,
               summary_budget: int | None = None) -> str:
    """把一条帖子渲染成 HTML 卡片（parse_mode=HTML）。"""
    title = html_mod.escape(re.sub(r"\s+", " ", post.title or "").strip())
    summary = html_mod.escape((post.summary or "").strip())
    url = (post.article_url or "").strip()

    tags_line = ""
    if channels:
        tags_line = "分类：" + " · ".join(channels)

    host = urlsplit(url).netloc if url else ""
    host = host[4:] if host.startswith("www.") else host
    link_line = ""
    if url:
        link_line = f'原文：<a href="{html_mod.escape(url, quote=True)}">' \
                    f"{html_mod.escape(host)}</a>"

    if summary_budget is None:
        # 预留标题/分类/链接/换行的开销，剩余给摘要
        overhead = len(title) + len(tags_line) + len(link_line) + 60
        summary_budget = max(MIN_SUMMARY, MAX_CAPTION - overhead - 20)

    body = _shorten(summary, summary_budget)
    parts = [f"<b>{title}</b>"]
    if body:
        parts.append(body)
    tail = "\n".join(x for x in (tags_line, link_line) if x)
    if tail:
        parts.append(tail)
    return "\n\n".join(parts)


def build_message(post, channels: list[str] | None = None) -> str:
    """纯文本兜底（无图时用；上限更宽，摘要可以给足）。"""
    return build_card(post, channels, summary_budget=MAX_TEXT - 400)


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

def _opener(proxy: str | None):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({} if not proxy
                                    else {"http": proxy, "https": proxy}))


def download_photo(url: str, proxy: str | None, timeout: int = 30) -> bytes | None:
    """下载配图。失败返回 None，由调用方降级为纯文本。"""
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://t.me/"})
        resp = _opener(proxy).open(req, timeout=timeout)
        data = resp.read()
        return data if data and len(data) > 1024 else None
    except Exception as e:
        print(f"    图片下载失败 {type(e).__name__}: {str(e)[:110]}")
        return None


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----cb" + uuid.uuid4().hex
    buf = bytearray()
    for k, v in fields.items():
        buf += f"--{boundary}\r\n".encode()
        buf += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        buf += str(v).encode("utf-8") + b"\r\n"
    for k, (fname, content) in files.items():
        ctype = mimetypes.guess_type(fname)[0] or "image/jpeg"
        buf += f"--{boundary}\r\n".encode()
        buf += (f'Content-Disposition: form-data; name="{k}"; '
                f'filename="{fname}"\r\n').encode()
        buf += f"Content-Type: {ctype}\r\n\r\n".encode()
        buf += content + b"\r\n"
    buf += f"--{boundary}--\r\n".encode()
    return bytes(buf), f"multipart/form-data; boundary={boundary}"


def _api_call(token: str, method: str, payload: dict,
              proxy: str | None) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API.format(token=token, method=method), data=data,
        headers={"Content-Type": "application/json"})
    try:
        resp = _opener(proxy).open(req, timeout=30)
        body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("ok")), body.get("description", "")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:140]}"


def _api_upload(token: str, method: str, fields: dict, files: dict,
                proxy: str | None) -> tuple[bool, str]:
    body, ctype = _multipart(fields, files)
    req = urllib.request.Request(
        API.format(token=token, method=method), data=body,
        headers={"Content-Type": ctype})
    try:
        resp = _opener(proxy).open(req, timeout=60)
        r = json.loads(resp.read().decode("utf-8"))
        return bool(r.get("ok")), r.get("description", "")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:140]}"


# ---------------------------------------------------------------------------
# 发送
# ---------------------------------------------------------------------------

def _retry_after(err: str) -> int:
    """从 Telegram 的 429 描述里取出建议等待秒数（上限 90s，避免卡死流程）。"""
    m = re.search(r"retry after (\d+)", err, re.I)
    return min(int(m.group(1)), 90) if m else 0


def _explain(err: str, token: str) -> None:
    """把 Telegram 的常见错误翻译成可执行的下一步。"""
    if "403" in err or "blocked" in err or "chat not found" in err:
        print("    → 新 bot 必须先点 START：在 Telegram 搜到它，点一下开始，"
              "否则它无权给你发私聊")
    elif "401" in err or "Unauthorized" in err:
        print("    → token 无效，检查 BotFather 给的那串是否复制完整")
    elif "429" in err or "Flood" in err:
        print("    → 触发限流，本轮剩余的下一轮再发")


def send_card(token: str, chat_id: str, post, channels: list[str] | None = None,
              proxy: str | None = None, with_photo: bool = True,
              silent: bool = True) -> bool:
    """发一条卡片：优先带图，失败降级纯文本；遇 429 按 retry_after 退避重试一次。"""
    caption = build_card(post, channels)
    text = build_message(post, channels)
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1] + "…"

    blob = download_photo(post.image_url, proxy) if (with_photo and post.image_url) else None
    last_err = ""

    for attempt in range(2):
        if blob:
            ok, err = _api_upload(
                token, "sendPhoto",
                {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML",
                 "disable_notification": "true" if silent else "false"},
                {"photo": ("cover.jpg", blob)}, proxy)
            if ok:
                return True
            last_err = err
            print(f"    sendPhoto 失败：{err}")
            if not _retry_after(err):
                blob = None        # 非限流错误（格式/权限）→ 本轮改发纯文本

        ok, err = _api_call(token, "sendMessage",
                            {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                             "disable_web_page_preview": True,
                             "disable_notification": silent}, proxy)
        if ok:
            return True
        last_err = err
        print(f"    sendMessage 失败：{err}")

        wait = _retry_after(err)
        if attempt == 0 and wait:
            print(f"    触发限流，等待 {wait}s 后重试")
            time.sleep(wait)
            continue
        break

    _explain(last_err, token)
    return False


def send_message(token: str, chat_id: str, text: str,
                 disable_preview: bool = True, proxy: str | None = None,
                 silent: bool = False) -> bool:
    ok, err = _api_call(token, "sendMessage",
                        {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                         "disable_web_page_preview": disable_preview,
                         "disable_notification": silent}, proxy)
    if not ok:
        print(f"    sendMessage 失败：{err}")
        _explain(err, token)
    return ok


def get_me(token: str, proxy: str | None = None) -> dict | None:
    try:
        resp = _opener(proxy).open(
            urllib.request.Request(API.format(token=token, method="getMe")),
            timeout=20)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"getMe 失败：{type(e).__name__} {str(e)[:150]}")
        return None
