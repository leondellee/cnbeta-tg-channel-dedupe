# -*- coding: utf-8 -*-
"""诊断：为什么部分卡片没有链接预览。

Telegram 抓预览时用的是 `TelegramBot (like TwitterBot)` 这个 UA。这里按同样的 UA
去取每一篇，看：状态码、是否重定向、有无 og:title / og:description / og:image。
"""

import json
import re
import urllib.error
import urllib.request

PROXY = "http://127.0.0.1:7890"
TG_UA = "TelegramBot (like TwitterBot)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

IDS = [1585002, 1585000, 1584998, 1584996, 1584994, 1584992, 1584990]


def opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua,
                                               "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        r = opener().open(req, timeout=25)
        return r.status, r.geturl(), r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        return e.code, getattr(e, "url", url), body
    except Exception as e:
        return 0, url, f"{type(e).__name__}: {e}"


def og(html, tag):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']' + tag + r'["\'][^>]*content=["\']([^"\']*)',
                  html, re.I)
    return m.group(1).strip() if m else ""


def main():
    print(f"{'文章':>8}  {'UA':8} {'状态':>5}  {'og:title':9} {'og:desc':7} {'og:image':8} 最终地址")
    print("-" * 104)
    for aid in IDS:
        url = f"https://www.cnbeta.com.tw/articles/soft/{aid}.htm"
        for label, ua in (("TG", TG_UA), ("浏览器", BROWSER_UA)):
            code, final, body = probe(url, ua)
            has_img = bool(og(body, "og:image"))
            print(f"{aid:>8}  {label:8} {code:>5}  "
                  f"{'有' if og(body,'og:title') else '无':9} "
                  f"{'有' if og(body,'og:description') else '无':7} "
                  f"{'有' if has_img else '无':8} "
                  f"{final.replace('https://www.cnbeta.com.tw','')[:40]}")
        print()


if __name__ == "__main__":
    main()
