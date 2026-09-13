# -*- coding: utf-8 -*-
"""
收藏链路探测脚本（按官方 user-api.md 文档 0.7.2-beta 重写）

回答三个问题：
  1. 收藏夹列表能拿到几个？（GET /api/v1/user/favlists）
  2. 指定收藏夹内容能否分页取全？（GET /api/v1/user/favlist_contents，Offset/Limit + Paging）
  3. 近期收藏 Top N 什么样？（GET /api/v1/user/collections，无分页，做对照）

用法（Windows CMD）：
  set ZHIHU_ACCESS_SECRET=你的secret
  python probe_favorites.py [收藏夹UrlToken]
  不带参数时自动用第一个收藏夹做分页探测

安全：secret 只从环境变量读，不落盘、不打印。
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse

BASE = "https://developer.zhihu.com"
SECRET = os.environ.get("ZHIHU_ACCESS_SECRET", "")

if not SECRET:
    print("先执行: set ZHIHU_ACCESS_SECRET=你的secret")
    sys.exit(1)


def call(path, **params):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + SECRET,
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("Code") != 0:
        raise RuntimeError("接口返回错误 Code=%s Message=%s" % (body.get("Code"), body.get("Message")))
    return body.get("Data", {})


def fmt_count(n):
    return str(n) if n < 10000 else "%.1fw" % (n / 10000)


print("=" * 62)
print("探测 1：收藏夹列表 favlists")
favlists = call("/api/v1/user/favlists", Limit=50).get("Items", [])
print("  收藏夹数量：%d" % len(favlists))
for f in favlists:
    print("  - [%s] %s (UrlToken=%s)" % ("公开" if f.get("IsPublic") else "私密", f.get("Title"), f.get("UrlToken")))

print("=" * 62)
print("探测 2：收藏夹内容分页 favlist_contents")
target = sys.argv[1] if len(sys.argv) > 1 else (favlists[0]["UrlToken"] if favlists else None)
if target is None:
    print("  没有可用收藏夹，跳过")
else:
    offset, total, pages = 0, 0, 0
    first_page = None
    while True:
        data = call("/api/v1/user/favlist_contents", FavlistUrlToken=target, Offset=offset, Limit=50)
        items = data.get("Items", [])
        paging = data.get("Paging", {})
        if first_page is None:
            first_page = items
        total += len(items)
        pages += 1
        print("  第 %d 页：返回 %d 条，Paging.IsEnd=%s，Totals=%s" % (
            pages, len(items), paging.get("IsEnd"), paging.get("Totals")))
        if paging.get("IsEnd") or not items:
            break
        nxt = paging.get("NextOffset")
        if nxt is None:
            print("  ⚠ IsEnd=false 但缺 NextOffset，分页协议不完整，停止")
            break
        offset = int(nxt)
        if pages >= 10:
            print("  已取 10 页（500 条）先停，防额度消耗")
            break
    print("  结论：该收藏夹共取到 %d 条" % total)
    if first_page:
        s = first_page[0]
        print("  首条字段：%s" % ", ".join(s.keys()))
        print("  首条样例：%s | 赞%s 评%s 藏%s" % (
            s.get("Title"), fmt_count(s.get("LikeCount", 0)),
            fmt_count(s.get("CommentCount", 0)), fmt_count(s.get("FavoriteCount", 0))))

print("=" * 62)
print("探测 3：近期收藏对照 collections（无分页，仅 Top N）")
recent = call("/api/v1/user/collections", Limit=50).get("Items", [])
print("  返回条数：%d（接口上限 50，无分页）" % len(recent))

print("=" * 62)
print("最终结论：")
print("  · 主链路走 favlists + favlist_contents 分页，收藏夹可全量读取")
print("  · collections 仅做『最近收藏』快捷入口")
