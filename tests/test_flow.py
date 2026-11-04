# -*- coding: utf-8 -*-
"""主流程逻辑回归（离线跑，不发消息、不联网）。

用 fixtures 里的真实页面，把抓取和发送替换成假函数，验证：
- 冷启动只建基线不推送
- 增量轮按时间正序、只发新文章
- 批次抬头在 ≥6 条时出现、文案正确
- 单轮上限生效、超出部分留到下一轮
- 全部发送失败会抛错（让 Actions 变红）

跑法：
    python tests/test_flow.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import service  # noqa: E402
from dedupe import Deduper, group_posts  # noqa: E402
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


def load_posts():
    out = []
    for ch in CHANNELS:
        out.extend(parse((FIXTURES / f"{ch}.html").read_text(encoding="utf-8")))
    return out


POSTS = load_posts()
GROUPS = group_posts(POSTS)


def base_cfg(**over):
    cfg = {"bot_token": "x", "chat_id": "1", "proxy": "", "dry_run": False,
           "max_push_per_run": 60, "max_age_hours": 36, "batch_header": True,
           "preview_large_media": True, "silent": True, "pages": 1}
    cfg.update(over)
    return cfg


def run_with(state, cfg, send_ok=True):
    """替换网络层，记录发送内容。"""
    sent_cards, sent_msgs = [], []

    def fake_card(token, chat_id, post, channels=None, **kw):
        if not send_ok:
            return False
        sent_cards.append((post.title, tuple(channels or ())))
        return True

    def fake_msg(token, chat_id, text, **kw):
        sent_msgs.append(text)
        return True

    orig = (service.collect, service.send_card, service.send_message, service.STATE_PATH)
    # run_once 会落盘，测试必须指向临时文件，否则会写脏生产用的 output/state.json
    tmp_state = Path(tempfile.gettempdir()) / "cnbeta_flow_test_state.json"
    service.STATE_PATH = tmp_state
    service.collect = lambda c: (POSTS, [])
    service.send_card = fake_card
    service.send_message = fake_msg
    try:
        result = service.run_once(cfg, state, verbose=False)
    finally:
        (service.collect, service.send_card, service.send_message,
         service.STATE_PATH) = orig
        tmp_state.unlink(missing_ok=True)
    return result, sent_cards, sent_msgs


def main() -> None:
    print("\n[1] 冷启动")
    state: dict = {}
    res, cards, msgs = run_with(state, base_cfg())
    check("只建基线不推卡片", res["mode"] == "seed" and not cards, str(res))
    check("基线 85 篇", len(state["seen"]) == 85, str(len(state["seen"])))
    check("发了一条启动提示", len(msgs) == 1 and "已启动" in msgs[0])

    print("\n[2] 增量：单轮上限与抬头")
    groups = sorted(GROUPS, key=lambda g: g.first_ts)
    # 抹掉最新 10 篇的记账，制造 10 条新文章
    state2 = {"version": 2, "initialized": True,
              "seen": dict(list(state["seen"].items())[:75])}
    for g in groups[-10:]:
        state2["seen"].pop(g.key, None)
    res, cards, msgs = run_with(state2, base_cfg(max_push_per_run=6))
    check("本轮只发 6 条（上限）", res["pushed"] == 6, str(res["pushed"]))
    check("抬头出现且写 6 条",
          len(msgs) == 1 and "6 条" in msgs[0], str(msgs))
    check("抬头是场次文案", any(k in (msgs[0] if msgs else "") for k in ("上午场", "下午场", "晚上场")))
    check("卡片按时序（最早先发）",
          cards and cards[0][0] == groups[-10].primary.title, cards[0][0][:20] if cards else "")

    print("\n[3] 下一轮接着发剩下的")
    state3 = state2
    res2, cards2, _ = run_with(state3, base_cfg(max_push_per_run=6))
    check("第二轮发出剩余 4 条", res2["pushed"] == 4, str(res2["pushed"]))
    check("两轮合计 10 条不重不漏",
          len({c[0] for c in cards + cards2}) == 10,
          str(len({c[0] for c in cards + cards2})))

    print("\n[4] 不足 6 条不加抬头")
    state4 = {"version": 2, "initialized": True,
              "seen": {k: v for k, v in state3["seen"].items()}}
    for g in groups[-1:]:
        state4["seen"].pop(g.key, None)
    res3, cards3, msgs3 = run_with(state4, base_cfg())
    check("3 条以下无抬头", res3["pushed"] == 1 and not msgs3, str(msgs3))

    print("\n[5] 分类合并正确")
    multi = [g for g in GROUPS if len(g.channels) > 1]
    g0 = multi[0]
    res4, cards4, _ = run_with(
        {"version": 2, "initialized": True,
         "seen": {k: v for k, v in state["seen"].items() if k != g0.key}},
        base_cfg())
    labels = [c[1] for c in cards4 if c[0] == g0.primary.title]
    check("多频道文章标了多个分类", labels and len(labels[0]) == len(g0.channels),
          str(labels))

    print("\n[6] 全部发送失败要抛错")
    try:
        run_with({"version": 2, "initialized": True,
                  "seen": dict(list(state["seen"].items())[:75])},
                 base_cfg(), send_ok=False)
        check("应抛 RuntimeError", False, "没有抛错")
    except RuntimeError as e:
        check("应抛 RuntimeError", "全部发送失败" in str(e), str(e))

    print(f"\n{'=' * 46}\n通过 {passed} 项，失败 {failed} 项\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
