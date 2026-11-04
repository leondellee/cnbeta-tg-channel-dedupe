# -*- coding: utf-8 -*-
"""卡片渲染 + Telegram 推送（纯标准库，零第三方依赖）。

卡片形态（Leon 2026-11-04 改版：对齐原频道）——正文只有两段：

    **窃听门本可避免？消息称OpenAI员工已警告安全漏洞多年**   ← 粗体，本身就是原文链接

    分类：精选 · 技术

下面的配图、标题、描述全部由 Telegram 的**链接预览**自动生成（读原文页的
og:image / og:description），和原频道看到的版式一致。因此这里不用 sendPhoto，
而是 sendMessage + link_preview_options；摘要不再写进正文，避免和预览重复。

原文页实测（2026-11）：og:title / og:description / og:image / og:site_name 齐全，
注意 /articles/soft/xxx.htm 会 301 到 /articles/tech/xxx.htm（换栏目），
Telegram 抓取时会自动跟随。
"""

from __future__ import annotations

import html as html_mod
import json
import re
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"

MAX_TEXT = 4096             # sendMessage 上限


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


def build_card(post, channels: list[str] | None = None) -> str:
    """渲染卡片正文（parse_mode=HTML），刻意对齐原频道的版式。

    原频道的样子：粗体标题（本身就是原文链接）+ 一排分类标签，下面是 Telegram
    自己抓出来的链接预览（图 + 标题 + 描述）。所以这里：

    - 标题做成 <a> 包 <b>，点标题就能跳原文；
    - 分类只留纯文本，不做成链接（Leon 2026-11-04 明确不需要）；
    - **不把摘要写进正文**——链接预览里的 og:description 就是这段摘要，
      再写一遍等于重复。只有拿不到原文地址时才退回「标题 + 摘要 + 来源」。
    """
    title = html_mod.escape(re.sub(r"\s+", " ", post.title or "").strip())
    url = (post.article_url or "").strip()

    if url:
        head = f'<b><a href="{html_mod.escape(url, quote=True)}">{title}</a></b>'
    else:
        head = f"<b>{title}</b>"

    parts = [head]
    if channels:
        parts.append("分类：" + " · ".join(channels))

    if not url:                      # 没有原文链接时，预览无从生成，才带摘要
        body = _shorten(html_mod.escape((post.summary or "").strip()), 800)
        if body:
            parts.append(body)
        if getattr(post, "link", ""):
            parts.append(f'来源：<a href="{html_mod.escape(post.link, quote=True)}">'
                         f"{html_mod.escape(post.link)}</a>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

def _opener(proxy: str | None):
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({} if not proxy
                                    else {"http": proxy, "https": proxy}))


def _api_call(token: str, method: str, payload: dict,
              proxy: str | None) -> tuple[bool, str, dict]:
    """返回 (ok, 错误描述, result)。result 里能拿到 message_id / web_page，
    用来判断这条消息到底有没有挂上链接预览。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API.format(token=token, method=method), data=data,
        headers={"Content-Type": "application/json"})
    try:
        resp = _opener(proxy).open(req, timeout=30)
        body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("ok")), body.get("description", ""), body.get("result") or {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        return False, f"HTTP {e.code}: {detail}", {}
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:140]}", {}


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
              proxy: str | None = None, large_media: bool = True,
              silent: bool = True) -> bool:
    """发一条卡片。

    走 sendMessage + link_preview_options，让 Telegram 自己抓原文页生成预览
    （大图 + 标题 + 描述），效果与原频道一致。这条路径不需要下载/上传图片，
    比原来的 sendPhoto 更快，也不受图床变化影响。

    实测补充（2026-11-04）：连带几条带链接的消息时，Telegram 抓预览会被节流，
    个别消息返回的 result 里没有 web_page（表现为卡片光秃秃的，只有标题和分类）。
    因此发送后检查 web_page，缺了就删掉、隔 6 秒重发一次。
    遇 429 仍按 retry_after 退避。
    """
    text = build_card(post, channels)
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 1] + "…"

    url = (post.article_url or "").strip()
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_notification": silent}
    if url:
        payload["link_preview_options"] = {
            "url": url,
            "prefer_large_media": bool(large_media),
            "show_above_text": False,
        }
    else:
        payload["link_preview_options"] = {"is_disabled": True}

    last_err = ""
    for attempt in range(2):
        ok, err, res = _api_call(token, "sendMessage", payload, proxy)
        if ok:
            # 挂没挂上预览，Telegram 会在 result.web_page 里告诉我们。
            # 短时间连发多条带链接的消息时，它抓取预览会被节流，偶尔不挂；
            # 这时删掉重发一次（隔几秒错开），比留一张光秃秃的卡片好。
            if url and not res.get("web_page") and attempt == 0 and res.get("message_id"):
                print(f"    未挂上预览，删除后重发：{post.title[:30]}")
                _api_call(token, "deleteMessage",
                          {"chat_id": chat_id, "message_id": res["message_id"]}, proxy)
                time.sleep(6)
                continue
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
    ok, err, _res = _api_call(token, "sendMessage",
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
