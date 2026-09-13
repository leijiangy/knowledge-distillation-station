# -*- coding: utf-8 -*-
"""两级缓存（额度生命线）。

设计（企划书 T3）：
- 内容键共享区：三指标 / 讲解 / 搜索 等「基于公开内容生成」的成果，全站用户共享
  ——蒸馏成果沉淀在站里，第一个人生成、后面所有人秒用
- 用户键私有区：收藏列表等私有数据，键含用户标识，短 TTL，防同一用户反复刷新打接口
- 单实例内存实现；容量有上限，超出按最久未访问淘汰（简单 LRU 近似）
"""
import time
from collections import OrderedDict
from typing import Any

MAX_ENTRIES = 2000  # 容量上限，防内存无限增长


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = MAX_ENTRIES):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.time() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)  # 淘汰最久未访问

    def drop(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


# 内容键共享区：用户私有数据之外的蒸馏成果（TTL 1 天）
content_cache = TTLCache(ttl_seconds=24 * 3600)

# 用户键私有区：收藏列表等（TTL 1 天，团队决定：额度优先；
# 用户点「刷新」时前端传 force=1 绕过缓存主动取新数据）
user_cache = TTLCache(ttl_seconds=24 * 3600, max_entries=500)


def user_key(scope: str, user_id: str | None) -> str:
    """私有缓存键：无登录身份时用本人模式标记（本地自测）"""
    return f"{scope}:{user_id or 'self'}"
