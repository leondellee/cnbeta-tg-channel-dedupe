# cnBeta 五频道去重聚合

把 5 个 cnBeta 推送频道的重复内容合并成一条，按原文地址去重后推到你 Telegram 私聊。
**部署后不在你电脑上跑，不消耗任何 token，每 5 分钟云端自动检查一次。**

---

## 它做什么

```
cnbeta_com（精选）┐
cnbeta_com_tech   │
cnbeta_com_business├─→ 抓预览页 → 按原文地址归组 → 与历史比对 → 完整卡片 → 你的私聊
cnbeta_com_media  │        （同轮合并）      （跨轮去重）
cnbeta_com_buzz   ┘
```

一篇文章被推到 3 个频道，你只会收到 1 条，分类行自动合并成「精选 · 技术 · 商业」。

### 频道 → 分类映射

| 频道 | 卡片里显示 |
|---|---|
| `cnbeta_com` | 精选 |
| `cnbeta_com_tech` | 技术 |
| `cnbeta_com_business` | 商业 |
| `cnbeta_com_media` | 娱乐 |
| `cnbeta_com_buzz` | 八卦 |

> 每条消息正文里那排 `[精选新闻] · [软硬件技术] · …` 是**固定的五个频道链接**，
> 不代表本条归属，所以分类改由「来源频道」推断，出现几个频道就标几个。

---

## 去重怎么做的

三层，从硬到软：

1. **原文地址（主键）**——同一篇文章在 5 个频道里的 `href` 完全一致，只差主机名
   （`cnbeta.com` / `cnbeta.com.tw` / `m.` / `www.`）。归一化后是天然唯一键。
   实测 100 条样本里 85 个唯一地址、85 个唯一标题，一一对应，零碰撞。
2. **标题指纹（兜底）**——地址缺失或换域名时用；只在 **6 小时**窗口内生效，
   防止把「同名不同文」（系列报道）误杀。
3. **同轮归组**——一轮抓取里同一篇出现在多个频道，先合并再决定发不发；
   代表条目取摘要最长的那条，分类取并集。

状态存 `output/state.json`，由工作流提交回仓库；只保留 7 天、上限 4000 条。

### 实测（2026-11-04 抓的 5 个页面）

| 指标 | 数值 |
|---|---|
| 抓到帖子 | 100 条（每频道 20 条） |
| 唯一文章 | 85 篇 |
| 合并掉重复 | 15 条（15%） |
| 跨频道重复篇数 | 13 篇，最多同时出现在 3 个频道 |
| 预估日更 | 约 80 篇（合并后） |
| 各频道日更 | tech 42 / business 21 / buzz 19 / 精选 10 / media 4 |

> 注意：`cnbeta_com` 不是其余四个的超集，只是「精选」子集。五个频道**各有独家**，
> 所以要做并集，不能只跟总频道。

---

## 卡片样式

```
《GTA6》数字版11月10日开放预载 容量达212GB          ← 加粗标题

《GTA6》将于11月19日登陆PS5和XSX|S。现有预载信息显示，   ← 摘要（超长在句号处截断）
数字版文件大小为212GB，11月10日起开放预载……

分类：娱乐                                          ← 来源频道并集
原文：cnbeta.com.tw                                 ← 可点，跳原文
        ↑ 上方是文章配图（800×450，取自 t.me 预览页图床）
```

---

## 部署

### 第 1 步：建一个空仓库

GitHub 上新建，**不要勾 README / .gitignore / license**，名字随意（如 `cnbeta-dedupe`）。

### 第 2 步：配置

```bash
cp config.example.json config.json
python tools/setup_bot.py <BOT_TOKEN> 123132586
```

**用独立的 bot，不要和 zzzdm 共用**——私聊是一条时间线，共用会把促销和新闻混在一起。
脚本会依次校验 token、验证能否私聊（没点 START 会 403）、写回 `config.json`。

### 第 3 步：推送

```bash
python tools/deploy.py https://github.com/<用户名>/cnbeta-dedupe.git
```

脚本会校验 `config.json` 已被忽略、提交、走代理推送，最后把要填的 Secrets 打印出来。

### 第 4 步：填两个 Secrets

仓库 → Settings → Secrets and variables → Actions → New repository secret：

| 名称 | 值 |
|---|---|
| `TG_BOT_TOKEN` | 第 2 步的 bot_token |
| `TG_CHAT_ID` | 第 2 步的 chat_id |

然后 Actions 标签页 → `cnbeta-dedupe` → Run workflow 手动跑一次。

> **首次运行只建立基线，不补发历史**（否则一上线刷 85 条）。
> 日志里出现 `[冷启动] 建立基线 85 篇，未推送` 就是正常。

---

## 本地调试

```bash
# 回归测试（解析 / 归一化 / 归组 / 跨轮去重，29 项）
python tests/test_dedupe.py

# 只看判定，不发消息
python src/service.py --dry-run

# 发 3 条样卡到私聊（不动去重状态，改样式后用它验收）
python tools/preview.py 3

# 只打印卡片不发送
python tools/preview.py 3 --no-send

# 检查 bot token
python src/service.py --check
```

本地跑要开代理（`config.json` 的 `proxy`）；云端直连即可，无需代理。

---

## 配置项

| 字段 | 默认 | 说明 |
|---|---|---|
| `bot_token` | — | BotFather 给的 token |
| `chat_id` | — | 接收私聊的数字 ID |
| `proxy` | — | 本地代理，云端留空 |
| `pages` | 1 | 每频道抓几页（1 页 20 条，够覆盖 5 分钟间隔） |
| `max_push_per_run` | 25 | 单轮最多推几条，超出留下轮 |
| `max_age_hours` | 24 | 超过这个时长的旧闻只记账不推送（防停机后刷旧闻） |
| `with_photo` | true | 是否带配图 |
| `silent` | true | 静音推送（不弹通知） |
| `dry_run` | false | 只判定不发送 |

---

## 文件结构

```
cnbeta-dedupe/
├── src/
│   ├── parser.py     t.me/s 页面解析（cnBeta 模板）
│   ├── dedupe.py     归一化 / 指纹 / 归组 / 状态
│   ├── notifier.py   卡片渲染 + sendPhoto/sendMessage
│   └── service.py    主流程（抓 → 合并 → 去重 → 推）
├── tools/
│   ├── preview.py    样卡预览（验收样式用）
│   └── deploy.py     一键推 GitHub
├── tests/
│   ├── test_dedupe.py      29 项回归
│   └── fixtures/*.html     5 个真实频道页快照
├── .github/workflows/aggregate.yml
├── config.json      本地配置（含 token，已 gitignore）
└── output/state.json  去重状态（由工作流回写）
```

---

## 常见问题

**为什么不用 Telegram 原生转发？**
Bot 无法读取它不在其中的频道；即使能读，原生转发也会把同一篇文章转 3 次。
所以走「抓公开预览页 → 自己渲染卡片」的路子，不需要任何账号授权。

**会不会重复推？**
不会。发成功才记账（`dd.mark` 在 `send_card` 返回 ok 之后），失败的下一轮重试；
状态文件随工作流提交回仓库，跨次运行有效。

**Actions 每 5 分钟一次，会不会太频繁？**
一次运行 5 个 HTTP 请求 + 最多 25 条推送，免费额度完全够。
实际触发常有 1~10 分钟延迟，GitHub 免费 runner 的正常现象。

**发现重复或漏推怎么办？**
- 重复：把地址加进 `dedupe.normalize_url` 的主机别名表，或调小 `TITLE_WINDOW_SEC`
- 漏推：多半是 `max_age_hours` 或 `max_push_per_run` 卡住了，看 Actions 日志里的计数

**改了卡片样式怎么验？**
改 `src/notifier.py` 的 `build_card`，然后 `python tools/preview.py 3` 实发三条看效果。
