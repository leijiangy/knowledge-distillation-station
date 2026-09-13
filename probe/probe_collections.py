# -*- coding: utf-8 -*-
"""
收藏接口探测脚本 —— 回答一个问题：能不能取全一个收藏夹？

用法：
  1. 设置环境变量（Windows CMD）：
     set ZHIHU_API_BASE=https://xxx   <- 换成数据开放平台文档里的真实域名
     set ZHIHU_ACCESS_TOKEN=xxx       <- 拿到 Access Secret 后填
  2. python probe_collections.py

探测点：
  A. limit 拉到最大（脚本会试 20 / 100 / 500）看实际返回条数
  B. 返回体里有没有分页字段（page / cursor / offset / has_more / next）
  C. 返回字段清单（标题、摘要、链接、三个计数、收藏夹归属是否都在）
"""

import os
import sys
import json
import urllib.request
import urllib.parse

BASE = os.environ.get("ZHIHU_API_BASE", "")
TOKEN = os.environ.get("ZHIHU_ACCESS_TOKEN", "")

if not BASE or not TOKEN:
    print("先设置 ZHIHU_API_BASE 和 ZHIHU_ACCESS_TOKEN 两个环境变量")
    sys.exit(1)

ENDPOINT = "/api/v1/user/collections"

PAGING_KEYS = ["page", "cursor", "offset", "has_more", "next", "is_end", "paging"]


def call(limit):
    url = BASE.rstrip("/") + ENDPOINT + "?" + urllib.parse.urlencode({"limit": limit})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(tag, data):
    print("=" * 60)
    print("本次请求：%s" % tag)
    if isinstance(data, dict):
        print("顶层字段：%s" % ", ".join(data.keys()))

        # 找分页线索
        hits = [k for k in PAGING_KEYS if k in data]
        print("分页字段命中：%s" % (", ".join(hits) if hits else "无 —— 大概率取不全"))

        # 找数据数组（不管它叫 data / items / collections）
        arr = None
        for key in ("data", "items", "collections", "list", "answers"):
            if isinstance(data.get(key), list):
                arr = data[key]
                break
        if arr is not None:
            print("返回条数：%d" % len(arr))
            if arr:
                print("单条字段：%s" % ", ".join(arr[0].keys()))
                print("首条样例：%s" % json.dumps(arr[0], ensure_ascii=False)[:500])
        else:
            print("（没找到数据数组，完整返回见下）")
            print(json.dumps(data, ensure_ascii=False)[:1500])
    else:
        print("非 dict 返回：%s" % json.dumps(data, ensure_ascii=False)[:1500])
    print()


if __name__ == "__main__":
    for lim in (20, 100, 500):
        try:
            summarize("limit=%d" % lim, call(lim))
        except Exception as e:
            print("limit=%d 请求失败：%r" % (lim, e))
    print("结论判断：若三个 limit 条数相同且无分页字段 → 接口只能拿『近期收藏』，"
          "文案降级为『访问你最近的收藏』，功能按 Top N 设计。")
