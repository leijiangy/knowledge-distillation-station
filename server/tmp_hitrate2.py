# -*- coding: utf-8 -*-
"""复测：提高 Count 后，未命中文章能否被搜索到"""
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

env_file = Path(__file__).resolve().parent / ".env"
for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
    if line.startswith("ZHIHU_ACCESS_SECRET="):
        os.environ.setdefault("ZHIHU_ACCESS_SECRET", line.split("=", 1)[1].strip())
SECRET = os.environ.get("ZHIHU_ACCESS_SECRET", "")


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


local = get("http://127.0.0.1:4173/api/collections")
items = (local.get("items") or [])[:15]

headers = {"Authorization": "Bearer " + SECRET, "Content-Type": "application/json"}
hits = 0
for idx, it in enumerate(items, 1):
    title = str(it.get("Title") or "").strip()
    target = str(it.get("Url") or "").split("?")[0]
    headers["X-Request-Timestamp"] = str(int(time.time()))
    url = "https://developer.zhihu.com/api/v1/content/zhihu_search?" + urllib.parse.urlencode(
        {"Query": title, "Count": 10})
    try:
        data = get(url, headers)
        results = (data.get("Data") or {}).get("Items") or []
        pos = next((i + 1 for i, x in enumerate(results)
                    if str(x.get("Url", "")).split("?")[0] == target), 0)
        ct = ""
        if pos:
            ct = str(results[pos - 1].get("ContentText") or "")
            hits += 1
    except Exception as exc:
        pos = -1
        ct = ""
    mark = "✅" if pos > 0 else ("⚠️" if pos == 0 else "❌")
    print(f"[{idx:>2}] {mark} 位次{pos:>2} 片段{len(ct):>5}字  {title[:34]}")
    time.sleep(0.6)

print("=" * 60)
print(f"Count=10 命中率: {hits}/15 = {hits / 15 * 100:.0f}%")
