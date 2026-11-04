# -*- coding: utf-8 -*-
"""样式预览：抓当前最新内容，推 N 条卡片到私聊（不动去重状态）。

用法：
    python tools/preview.py            # 发 3 条
    python tools/preview.py 5          # 发 5 条
    python tools/preview.py 3 --no-send  # 只打印卡片，不发送

挑选逻辑：优先挑「多频道重复」的那几篇，能直观看到合并后的分类行；
再补一条无图的，确认纯文本降级样式。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dedupe import Group, group_posts  # noqa: E402
from notifier import build_card, get_me, send_card, send_message  # noqa: E402
from service import CHANNELS, LABEL, collect, load_config  # noqa: E402


def pick(groups: list[Group], count: int) -> list[Group]:
    """优先最近的多频道重复篇（能看出合并效果），再补最近的单频道篇。"""
    recent = sorted(groups, key=lambda g: g.first_ts, reverse=True)
    multi = [g for g in recent if len(g.channels) > 1]
    single = [g for g in recent if len(g.channels) == 1]
    picks = multi[:1] + single
    if len(picks) < count:
        picks += multi[1:]
    # 若挑中的都有图，换一条无图的进来验证纯文本降级
    noimg = next((g for g in single if not g.primary.image_url), None)
    if noimg and all(g.primary.image_url for g in picks):
        picks[-1] = noimg
    return picks[:count]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    count = int(args[0]) if args else 3
    no_send = "--no-send" in sys.argv

    cfg = load_config()
    token, chat_id = cfg.get("bot_token", ""), str(cfg.get("chat_id", ""))
    proxy = cfg.get("proxy") or None

    me = get_me(token, proxy)
    if not me or not me.get("ok"):
        sys.exit("bot token 无效，先检查 config.json")
    print(f"bot: @{me['result'].get('username')} → chat_id {chat_id}\n")

    posts, failed = collect(cfg)
    print(f"抓到 {len(posts)} 条，归并为 {len(group_posts(posts))} 篇"
          + (f"（失败频道：{failed}）" if failed else ""))
    groups = group_posts(posts)
    picks = pick(groups, count)

    if not no_send and chat_id:
        send_message(token, chat_id,
                     "<b>样式预览</b>\n下面几条是聚合频道的实际卡片样式，"
                     "看完告诉我哪里要改。", proxy=proxy, silent=False)

    for i, g in enumerate(picks, 1):
        labels = [LABEL.get(c, c) for c in g.channels]
        print(f"\n--- 样本 {i}（{'+'.join(g.channels)}）---")
        print(build_card(g.primary, labels))
        if not no_send and chat_id:
            ok = send_card(token, chat_id, g.primary, labels, proxy=proxy,
                           with_photo=cfg.get("with_photo", True), silent=False)
            print(f"[{'已发送' if ok else '发送失败'}]")

    print("\n完成。本次预览不写入去重状态，不影响正式推送。")


if __name__ == "__main__":
    main()
