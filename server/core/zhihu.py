# -*- coding: utf-8 -*-
"""知乎开放平台接口封装（用户数据接口 + OAuth 凭据头）。

依据：skills/zhihu/references/user-api.md
- 用户数据接口 = Authorization: Bearer <Access Secret> + X-OAuth-Token + X-Request-Timestamp
- favlists 无分页（服务端忽略 Offset）；favlist_contents 有 Offset/Limit 分页
"""
import time

import httpx

from .config import settings

TIMEOUT = 30.0


def client() -> httpx.AsyncClient:
    """统一 HTTP 客户端。

    trust_env=False：部分开发环境通过 SSL_CERT_FILE 注入单证书自签 CA，会污染
    对公网站点的 TLS 验证；关闭环境读取后使用 certifi 公认 CA 库做标准验证。
    """
    return httpx.AsyncClient(timeout=TIMEOUT, trust_env=False)


class ZhihuError(Exception):
    """带错误码的知乎接口异常，消息可直接展示给用户"""

    def __init__(self, code: str | int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def user_api_headers(oauth_token: str | None) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.ACCESS_SECRET}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    }
    if oauth_token:
        headers["X-OAuth-Token"] = oauth_token
    return headers


async def user_api_get(path: str, oauth_token: str | None = None, **params) -> dict:
    """调用用户数据接口；oauth_token 为 None 时查询 Access Secret 所属账号（本人模式，本地自测用）"""
    if not settings.ACCESS_SECRET:
        raise ZhihuError("SECRET_REQUIRED", "开放平台 Access Secret 未配置。")
    clean = {k: v for k, v in params.items() if v is not None}
    async with client() as http:
        resp = await http.get(
            f"{settings.ZHIHU_API}{path}",
            headers=user_api_headers(oauth_token),
            params=clean,
        )
    try:
        payload = resp.json()
    except Exception:
        raise ZhihuError("API_PARSE_FAILED", "知乎接口返回了无法解析的响应。")
    code = payload.get("Code", payload.get("code"))
    if code != 0:
        message = payload.get("Message") or payload.get("message") or "用户数据接口失败。"
        raise ZhihuError(code if code is not None else "API_FAILED", str(message))
    return payload.get("Data", {}) or {}


async def fetch_favlists(oauth_token: str | None = None, limit: int = 50) -> list:
    data = await user_api_get("/api/v1/user/favlists", oauth_token, Limit=limit)
    return data.get("Items", [])


async def search_zhihu(query: str, count: int = 3) -> list:
    """知乎搜索（返回含 AuthorAvatar / AuthorBadge / AuthorityLevel / AuthorSignature / ContentText）"""
    data = await user_api_get("/api/v1/content/zhihu_search", None, Query=query, Count=count)
    return data.get("Items", []) or []


async def fetch_favlist_contents(favlist_token: int | str, oauth_token: str | None = None,
                                 offset: int = 0, limit: int = 50) -> dict:
    return await user_api_get(
        "/api/v1/user/favlist_contents", oauth_token,
        FavlistUrlToken=favlist_token, Offset=offset, Limit=limit,
    )


def parse_next_offset(paging: dict) -> int | None:
    """解析 Paging.NextOffset（服务端为 String，需严格转 int）。

    缺失或解析失败一律返回 None —— 调用方应停止翻页，不猜测、不静默截断。
    """
    if not isinstance(paging, dict):
        return None
    value = paging.get("NextOffset")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def fetch_all_favlist_contents(favlist_token: int | str, oauth_token: str | None = None,
                                     max_pages: int = 20, on_page=None) -> list:
    """按 Paging 协议取全一个收藏夹"""
    items: list = []
    offset = 0
    for page in range(1, max_pages + 1):
        data = await fetch_favlist_contents(favlist_token, oauth_token, offset=offset, limit=50)
        page_items = data.get("Items", []) or []
        items.extend(page_items)
        if on_page:
            on_page(page, len(items))
        paging = data.get("Paging", {}) or {}
        if paging.get("IsEnd") or not page_items:
            break
        next_offset = parse_next_offset(paging)
        if next_offset is None:
            break  # IsEnd=false 但 NextOffset 缺失/非法：协议不完整，停止而非猜测
        offset = next_offset
    return items
