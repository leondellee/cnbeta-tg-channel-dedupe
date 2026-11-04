# -*- coding: utf-8 -*-
"""cnBeta 多频道去重聚合 · 主服务

流程：
    抓 5 个频道预览页 → 解析 → 按原文地址归组（同轮去重）
    → 与历史状态比对（跨轮去重）→ 按时间正序推完整卡片到私聊

设计要点：
- 纯标准库，零第三方依赖，云端无需 pip install。
- 去重状态落 output/state.json，由工作流提交回仓库，跨次运行不重复。
- 首次运行不补发历史（只建立基线），避免一上线刷 80 条。
- 抓不到任何页面时**报错退出**，不在云端静默空跑。
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dedupe import Deduper, Group, group_posts  # noqa: E402
from notifier import get_me, send_card, send_message  # noqa: E402
from parser import Post, parse  # noqa: E402

# 源频道 → 展示用分类（顺序即卡片里的排列顺序）
CHANNELS: list[tuple[str, str]] = [
    ("cnbeta_com", "精选"),
    ("cnbeta_com_tech", "技术"),
    ("cnbeta_com_business", "商业"),
    ("cnbeta_com_media", "娱乐"),
    ("cnbeta_com_buzz", "八卦"),
]
LABEL = dict(CHANNELS)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PROXY_CANDIDATES = ["http://127.0.0.1:7890", "http://127.0.0.1:7897",
                    "http://127.0.0.1:10809"]

# 状态文件路径可用环境变量覆盖（本地测试指向临时文件，避免污染真实状态）
STATE_PATH = Path(os.environ.get("CNBETA_STATE") or (ROOT / "output" / "state.json"))
CONFIG_PATH = Path(os.environ.get("CNBETA_CONFIG") or (ROOT / "config.json"))


# ---------------------------------------------------------------------------
# 配置 / 状态
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        cfg = {}
    cfg.setdefault("bot_token", os.environ.get("TG_BOT_TOKEN", ""))
    cfg.setdefault("chat_id", os.environ.get("TG_CHAT_ID", ""))
    cfg.setdefault("proxy", os.environ.get("TG_PROXY", ""))
    cfg.setdefault("pages", 1)
    cfg.setdefault("max_push_per_run", 60)
    cfg.setdefault("max_age_hours", 36)
    cfg.setdefault("batch_header", True)
    cfg.setdefault("with_photo", True)
    cfg.setdefault("silent", True)
    cfg.setdefault("dry_run", False)
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[警告] state.json 解析失败（{e}），按空状态处理")
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------

def _proxy_chain(configured: str) -> list[str | None]:
    if os.environ.get("CI"):           # GitHub Actions 直连即可
        return [configured or None]
    chain: list[str | None] = [configured] if configured else []
    chain += [p for p in PROXY_CANDIDATES if p != configured]
    chain.append(None)
    return chain


def fetch(url: str, chain: list[str | None], timeout: int = 25) -> str | None:
    import urllib.request
    errors: list[str] = []
    for px in chain:
        label = px or "直连"
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(
                    {} if not px else {"http": px, "https": px}))
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            resp = opener.open(req, timeout=timeout)
            body = resp.read().decode("utf-8", "ignore")
            if "tgme_widget_message" not in body:
                errors.append(f"{label}: 页面无消息结构（len={len(body)}）")
                continue
            return body
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__} {str(e)[:80]}")
    print(f"    抓取失败 {url}")
    for e in errors:
        print(f"      - {e}")
    return None


def fetch_channel(channel: str, chain: list[str | None],
                  pages: int = 1) -> tuple[list[Post], bool]:
    """抓单个频道最近 N 页。返回 (帖子列表, 是否至少成功一页)。"""
    out: dict[str, Post] = {}
    before: str | None = None
    ok_any = False
    for _ in range(max(1, pages)):
        url = f"https://t.me/s/{channel}"
        if before:
            url += f"?before={before}"
        html_text = fetch(url, chain)
        if not html_text:
            break
        ok_any = True
        ps = parse(html_text)
        if not ps:
            break
        for p in ps:
            out.setdefault(p.post_id, p)
        before = str(min(p.msg_id for p in ps))
        time.sleep(0.4)
    return list(out.values()), ok_any


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def collect(config: dict) -> tuple[list[Post], list[str]]:
    chain = _proxy_chain(config.get("proxy") or "")
    pages = int(config.get("pages", 1))
    posts: list[Post] = []
    failed: list[str] = []
    for channel, _label in CHANNELS:
        ps, ok = fetch_channel(channel, chain, pages)
        if not ok:
            failed.append(channel)
            continue
        if not ps:
            print(f"  [警告] {channel} 解析到 0 条，页面结构可能变了")
        posts.extend(ps)
    return posts, failed


def slot_label() -> str:
    """按北京时间判断当前属于哪一场（用于批次抬头）。"""
    h = datetime.now(timezone(timedelta(hours=8))).hour
    if h < 12:
        return "上午场"
    if h < 18:
        return "下午场"
    return "晚上场"


def run_once(config: dict, state: dict, verbose: bool = True) -> dict:
    token = config.get("bot_token", "")
    chat_id = str(config.get("chat_id", ""))
    proxy = config.get("proxy") or None
    dry = bool(config.get("dry_run"))
    cap = int(config.get("max_push_per_run", 25))
    with_photo = bool(config.get("with_photo", True))
    silent = bool(config.get("silent", True))

    # 缺密钥必须显式失败：否则云端会安静地空跑，看起来「成功」但什么都没发
    if not dry and (not token or not chat_id):
        raise RuntimeError("缺少 TG_BOT_TOKEN / TG_CHAT_ID，请先在仓库 Secrets 里配置")

    posts, failed = collect(config)
    if verbose:
        print(f"抓到 {len(posts)} 条 / {len(CHANNELS) - len(failed)} 个频道"
              + (f"（失败：{', '.join(failed)}）" if failed else ""))
    if not posts:
        raise RuntimeError("抓取失败：5 个频道均未取到内容，请检查网络或页面结构")
    if len(failed) == len(CHANNELS):
        raise RuntimeError("抓取失败：所有频道均不可用")

    groups = group_posts(posts)
    dd = Deduper(state)
    if verbose:
        dup_in_run = len(posts) - len(groups)
        print(f"归并后 {len(groups)} 篇（同轮内合并 {dup_in_run} 条重复）")

    # ---- 冷启动：只建基线，不补发 -------------------------------------
    if not dd.initialized:
        dd.mark_all(groups)
        dd.set_initialized()
        dd.prune()
        save_state(dd.to_dict())
        msg = (f"<b>cnBeta 聚合已启动</b>\n\n"
               f"已收录基线 {len(groups)} 篇（不补发历史），"
               f"之后新文章将实时推送。\n"
               f"来源：{'、'.join(label for _, label in CHANNELS)}")
        if not dry and token and chat_id:
            send_message(token, chat_id, msg, proxy=proxy, silent=False)
        print(f"[冷启动] 建立基线 {len(groups)} 篇，未推送")
        return {"mode": "seed", "fetched": len(posts), "groups": len(groups),
                "pushed": 0, "skipped": 0, "failed_channels": failed}

    # ---- 常规：筛选新文章 ---------------------------------------------
    fresh: list[Group] = []
    skipped = 0
    for g in groups:
        dup, reason = dd.is_duplicate(g)
        if dup:
            skipped += 1
            if verbose and reason != "原文地址已收录":
                print(f"    跳过（{reason}）：{g.primary.title[:40]}")
            continue
        fresh.append(g)

    fresh.sort(key=lambda g: g.first_ts)      # 按时间正序，读起来顺

    # 过期保护：长时间停机/状态丢失后，别把前几天的旧闻当新闻刷出来
    max_age = float(config.get("max_age_hours", 24)) * 3600
    if max_age > 0:
        now = time.time()
        stale = [g for g in fresh if g.first_ts and now - g.first_ts > max_age]
        if stale:
            for g in stale:
                dd.mark(g)                    # 记账但不再推
            fresh = [g for g in fresh if g not in stale]
            if verbose:
                print(f"过期跳过 {len(stale)} 篇（超过 {max_age / 3600:.0f} 小时），"
                      f"如：{stale[0].primary.title[:30]}")

    if verbose:
        print(f"新文章 {len(fresh)} 篇，历史命中 {skipped} 篇"
              + (f"，本轮上限 {cap} 篇" if len(fresh) > cap else ""))

    sent = 0
    attempted = 0
    if dry:
        if fresh:
            from notifier import build_card
            print("[dry_run] 未发送。首条预览：\n" + "-" * 50)
            g = fresh[0]
            print(build_card(g.primary, [LABEL[c] for c in g.channels]))
            print("-" * 50)
            print(f"[dry_run] 另有 {max(0, len(fresh) - 1)} 篇待发")
    else:
        batch = fresh[:cap]
        # 三场制下一批可能十几二十条，加个抬头便于在对话里区分批次
        if len(batch) >= 6 and config.get("batch_header", True):
            head = (f"<b>{slot_label()} · {len(batch)} 条</b>\n"
                    f"{datetime.now(timezone(timedelta(hours=8))):%m-%d %H:%M}")
            send_message(token, chat_id, head, proxy=proxy, silent=silent)

        for g in batch:
            labels = [LABEL.get(c, c) for c in g.channels]
            attempted += 1
            ok = send_card(token, chat_id, g.primary, labels, proxy=proxy,
                           with_photo=with_photo, silent=silent)
            if ok:
                dd.mark(g)          # 关键：只有发成功才记账，失败下轮重试
                sent += 1
            else:
                print(f"    推送失败，下轮重试：{g.primary.title[:40]}")
            time.sleep(2.5)         # Telegram 单聊约 20 条/分钟，留足余量

    dropped = dd.prune()
    if verbose and dropped:
        print(f"状态清理：移除 {dropped} 条过期记录")

    save_state(dd.to_dict())
    stats = dd.stats()

    # 全部发送失败 = 配置/权限问题（被拉黑、chat_id 写错），必须让 Actions 变红，
    # 否则任务一直绿着，人以为在正常推送。状态已先落盘，不会导致重发。
    if not dry and attempted and sent == 0:
        raise RuntimeError(
            f"{attempted} 条全部发送失败，检查 bot 是否被 block、chat_id 是否正确")

    return {"mode": "run", "fetched": len(posts), "groups": len(groups),
            "fresh": len(fresh), "pushed": sent, "skipped": skipped,
            "failed_channels": failed, "state": stats}


def main() -> None:
    args = sys.argv[1:]
    dry_override = "--dry-run" in args
    config = load_config()
    if dry_override:
        config["dry_run"] = True

    if "--check" in args:
        me = get_me(config.get("bot_token", ""), config.get("proxy") or None)
        print(json.dumps(me, ensure_ascii=False, indent=1))
        return

    state = load_state()
    try:
        result = run_once(config, state)
    except RuntimeError as e:
        print(f"运行失败：{e}")
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
