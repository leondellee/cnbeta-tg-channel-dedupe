# -*- coding: utf-8 -*-
"""新 bot 配置助手：校验 token → 验证能否私聊 → 写回 config.json。

用法：
    python tools/setup_bot.py <BOT_TOKEN> [chat_id]

三步分别验证：
1. getMe            —— token 是否有效，顺手打印 bot 用户名
2. sendMessage      —— 你是否已经点过这个新 bot 的 START（没点会 403）
3. 写入 config.json —— 只改 bot_token（chat_id 不变，私聊 chat_id 就是你的用户 ID）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from notifier import get_me, send_message  # noqa: E402

CONFIG = ROOT / "config.json"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：python tools/setup_bot.py <BOT_TOKEN> [chat_id]")
    token = sys.argv[1].strip()

    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    chat_id = (sys.argv[2] if len(sys.argv) > 2 else str(cfg.get("chat_id", ""))).strip()
    proxy = cfg.get("proxy") or None

    print("① 校验 token…")
    me = get_me(token, proxy)
    if not me or not me.get("ok"):
        sys.exit("  ✗ token 无效，检查是否复制完整（形如 8123456789:AAH...）")
    info = me["result"]
    print(f"  ✓ @{info.get('username')}（{info.get('first_name')}）")

    if not chat_id:
        sys.exit("  ✗ 缺 chat_id，跑到 @userinfobot 拿一下你的数字 ID")

    print(f"② 验证能否给 {chat_id} 发私聊…")
    ok = send_message(
        token, chat_id,
        f"<b>cnBeta 聚合 bot 已就绪</b>\n\n"
        f"我是 @{info.get('username')}，之后只负责发 cnBeta 去重资讯，"
        f"不会和别的推送混在一起。",
        proxy=proxy, silent=False)
    if not ok:
        print("  ✗ 发不出去。最常见原因：还没点 START——")
        print(f"    在 Telegram 搜 @{info.get('username')}，点一下 START，再重跑本脚本。")
        sys.exit(1)
    print("  ✓ 已送达")

    cfg["bot_token"] = token
    cfg["chat_id"] = chat_id
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"③ 已写入 {CONFIG.name}")

    print("\n" + "=" * 60)
    print("云端还要改一处 Secret（本地配置只影响调试）：")
    print("  GitHub → Settings → Secrets and variables → Actions")
    print(f"  TG_BOT_TOKEN = {token}")
    print(f"  TG_CHAT_ID   = {chat_id}（不变）")
    print("改完到 Actions 页 Run workflow 验证一次。")


if __name__ == "__main__":
    main()
