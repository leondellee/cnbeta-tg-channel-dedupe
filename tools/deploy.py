# -*- coding: utf-8 -*-
"""一键部署到 GitHub（推代码 + 打印要填的 Secrets）。

用法：
    python tools/deploy.py https://github.com/<用户名>/cnbeta-dedupe.git

前置：仓库需先在网页上建好（空的，不要勾 README），且 config.json 已就绪。
config.json 含 token，已被 .gitignore 排除，不会推上去。
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROXY = "http://127.0.0.1:7890"


def find_git() -> str:
    """优先用 PATH 里的 git，其次扫常见安装位置（不把本机用户名写进代码）。"""
    found = shutil.which("git")
    if found:
        return found
    candidates = [
        "C:/Program Files/Git/cmd/git.exe",
        "C:/Program Files (x86)/Git/cmd/git.exe",
        str(Path.home() / ".workbuddy/binaries/PortableGit/versions/*/cmd/git.exe"),
    ]
    for pat in candidates:
        hits = [p for p in glob.glob(pat) if "versions\\." not in p]
        if hits:
            return sorted(hits)[-1]
    sys.exit("找不到 git，请先安装 Git 或把它加入 PATH")


GIT = find_git()


def run(args: list[str], check: bool = True, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run([GIT] + args, cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="ignore",
                          env=env or os.environ.copy())
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        print(f"  命令失败：git {' '.join(args)}")
        print(f"  {out.strip()[:400]}")
    return proc.returncode, out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：python tools/deploy.py <仓库地址>")
    remote = sys.argv[1].strip()

    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        sys.exit("未找到 config.json，先复制 config.example.json 填好 token / chat_id")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        sys.exit("config.json 缺少 bot_token 或 chat_id")

    print("① 初始化仓库…")
    if not (ROOT / ".git").exists():
        run(["init"])
        run(["branch", "-M", "main"])
    else:
        print("   已存在 .git")

    print("② 确认 config.json 被忽略…")
    code, _ = run(["check-ignore", "config.json"], check=False)
    if code != 0:
        sys.exit("  ✗ config.json 未被忽略，中止以防 token 泄露")
    print("   ✓ 已忽略")

    print("③ 提交…")
    run(["add", "-A"])
    code, out = run(["commit", "-m", "feat: cnBeta 五频道去重聚合"], check=False)
    print("   已提交" if "nothing to commit" not in out else "   无新改动")

    print("④ 设置 origin…")
    code, _ = run(["remote", "get-url", "origin"], check=False)
    if code == 0:
        run(["remote", "set-url", "origin", remote])
    else:
        run(["remote", "add", "origin", remote])
    print(f"   {remote}")

    print("⑤ 推送（走代理）…")
    env = os.environ.copy()
    env.update({"HTTP_PROXY": PROXY, "HTTPS_PROXY": PROXY,
                "http_proxy": PROXY, "https_proxy": PROXY})
    code, out = run(["push", "-u", "origin", "main"], check=False, env=env)
    if code != 0:
        print("   推送失败：")
        print(out[:600])
        sys.exit(1)
    print("   ✓ 已推送")

    print("\n" + "=" * 62)
    print("最后一步：GitHub 仓库 → Settings → Secrets and variables → Actions")
    print("=" * 62)
    print(f"  TG_BOT_TOKEN = {cfg['bot_token']}")
    print(f"  TG_CHAT_ID   = {cfg['chat_id']}")
    print("\n填完到 Actions 标签页 → cnbeta-dedupe → Run workflow 手动跑一次。")
    print("首次运行只建立基线（不补发历史），之后每 5 分钟自动汇总。")


if __name__ == "__main__":
    main()
