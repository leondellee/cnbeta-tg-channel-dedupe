# cnBeta 五频道去重聚合

把 5 个 cnBeta 推送频道的重复内容合并成一条，按原文地址去重后推到你 Telegram 私聊。
**部署后不在你电脑上跑，不消耗任何 token，每 5 分钟自动汇总一次。**

> 链路：Cloudflare Worker（cron `*/5`，秒级准时）→ `workflow_dispatch` → GitHub Actions
> 抓取去重推送。Actions 自带的 schedule 会被节流成几小时一次，只留作兜底。

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
2. **文章 ID（二级键）**——`/articles/soft/1584994.htm` 会 301 跳到
   `/articles/tech/1584994.htm`（换栏目），地址归一化后路径不同，但仍是同一篇。
   所以额外用文章数字 ID 判重，避免换栏目后重复推送。
3. **标题指纹（兜底）**——地址缺失或换域名时用；只在 **6 小时**窗口内生效，
   防止把「同名不同文」（系列报道）误杀。
4. **同轮归组**——一轮抓取里同一篇出现在多个频道，先合并再决定发不发；
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

## 触发频率：GitHub 会节流，所以用 Worker 当定时器

Actions 的 `schedule` 只是「请求」而不是「定时器」。同账号下 zzzdm 仓库（同样写 `*/5`）
最近 60 次定时运行的真实间隔：

| 指标 | 实测值 |
|---|---|
| 间隔中位数 | **4.7 小时** |
| 最短 / 最长 | 1.9 小时 / 10.3 小时 |
| 换算 | 约 **5 次/天**，而非理论上的 288 次 |

**解决办法**：`cloudflare-cron/` 里放一个小 Worker，用 Cloudflare 的 cron
每 5 分钟调一次 `workflow_dispatch`。Cloudflare 的 cron 是准的（秒级），
Python 逻辑一行不用改，仍然跑在 Actions 上——Worker 只负责「戳一下」。

Actions 里的 `schedule` 保留每天三场作为兜底：Worker 万一挂了，还能收到三次。

> 单批条数回到 1～3 条（5 分钟一次），批次抬头自然不会出现（阈值 6 条）。
> `max_push_per_run: 60`、`max_age_hours: 36` 保持不变，只在长时间停机后才有意义。

### 部署 Worker

**已上线**：`cnbeta-dedupe-cron`，cron `*/5`，实测 19:40:53 准点触发、Actions 44 秒跑完。

首次部署用的是 Cloudflare REST API（`PUT /workers/scripts/{name}` + `/schedules` + `/secrets`），
因为 wrangler 3/4 在当前环境都报错。以后改 `worker.js` 只需重新 PUT 上传，不需要动 cron 和 secret。

命令行方式（供参考）：

```bash
cd cloudflare-cron
npx wrangler login                 # 浏览器授权
npx wrangler deploy
npx wrangler secret put GH_TOKEN
```

`GH_TOKEN` 是 fine-grained PAT，只需对 `cnbeta-tg-channel-dedupe` 一个仓库的
**Actions: Read and write**；建议 90 天有效期。

> **两个坑**：
> 1. 本地代理会缓存 `api.cloudflare.com` 的 GET 响应，表现为「写入成功但读不到」，
>    调用 API 时加 `--noproxy '*'` 直连。
> 2. `*.workers.dev` 在国内直连不通（tg-scheduler 那个也一样），但 cron 是 Cloudflare
>    内部触发，**不影响**；要用 `?key=TRIGGER_KEY` 手动触发请走代理。

---

## 卡片样式

和原频道一致：正文只有标题和分类，**下面的图、标题、描述全部由 Telegram 的链接预览生成**。

```
《GTA6》数字版11月10日开放预载 容量达212GB          ← 粗体，本身就是原文链接，点标题即跳转

分类：娱乐                                          ← 来源频道并集，纯文本（不做成链接）

┌──────────────────────────────────┐
│ [文章封面图，大图]                 │   ← Telegram 抓 og:image
│ cnBeta.COM                        │   ← og:site_name
│ 《GTA6》数字版11月10日开放预载…     │   ← og:title
│ 《GTA6》将于11月19日登陆PS5和XSX…   │   ← og:description
└──────────────────────────────────┘
```

实现上用 `sendMessage` + `link_preview_options`（`prefer_large_media: true`），
不再自己下载上传图片——比 `sendPhoto` 少一次下载、一次上传，也更不容易失败。

> 摘要刻意不写进正文：预览里的 `og:description` 就是同一段话，写两遍会重复。
> 只有拿不到原文地址时，才退回「标题 + 摘要 + 来源链接」的旧格式。

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

# 主流程回归（冷启动 / 批次 / 上限 / 全失败报错，12 项，离线跑）
python tests/test_flow.py

# 只看判定，不发消息
python src/service.py --dry-run

# 配置新 bot（校验 token、验证能否私聊、写回 config.json）
python tools/setup_bot.py <BOT_TOKEN> 123132586

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
| `pages` | 1 | 每频道抓几页（1 页 20 条，按 4.7 小时间隔算足够覆盖） |
| `max_push_per_run` | 60 | 单轮最多推几条，超出留下轮 |
| `max_age_hours` | 36 | 超过这个时长的旧闻只记账不推送（防停机后刷旧闻） |
| `batch_header` | true | 满 6 条时先发一条「上午场 · N 条」的抬头 |
| `preview_large_media` | true | 链接预览用大图（false 则用右侧小图） |
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
│   ├── setup_bot.py  新 bot 配置助手
│   └── deploy.py     一键推 GitHub
├── tests/
│   ├── test_dedupe.py      29 项回归（解析/去重）
│   ├── test_flow.py        12 项回归（主流程，离线）
│   └── fixtures/*.html     5 个真实频道页快照
├── .github/workflows/aggregate.yml
├── cloudflare-cron/
│   ├── worker.js    每 5 分钟戳一次 workflow_dispatch
│   └── wrangler.toml
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

**想改成别的频率？**
改 `.github/workflows/aggregate.yml` 的 cron 即可（UTC 时间，北京时间 = UTC+8），
但请记住它只是「请求」：实测能跑到的上限约 5 次/天，写更密不会有更多次运行。
一天三场配合 60 条/轮的上限，单批约 27 条，够用。

**发现重复或漏推怎么办？**
- 重复：把地址加进 `dedupe.normalize_url` 的主机别名表，或调小 `TITLE_WINDOW_SEC`
- 漏推：多半是 `max_age_hours` 或 `max_push_per_run` 卡住了，看 Actions 日志里的计数

**改了卡片样式怎么验？**
改 `src/notifier.py` 的 `build_card`，然后 `python tools/preview.py 3` 实发三条看效果。
