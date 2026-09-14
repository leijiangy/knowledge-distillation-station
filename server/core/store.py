# -*- coding: utf-8 -*-
"""持久化数据层（T9：CloudBase 共享集群 PostgreSQL —— 经 REST API 访问）。

共享集群不提供直连地址（内网/外网都没有），官方接入方式是 PostgREST 规范的 REST API：
    https://<envId>.api.tcloudbasegateway.com/v1/rdb/rest/<table>

环境变量：
- CLOUDBASE_ENV_ID   环境 ID（如 zhishizhizengliuzhan）
- CLOUDBASE_API_KEY  服务端密钥（控制台「接入指引」里获取）
- CLOUDBASE_API_BASE 可选，默认按环境 ID 拼装

建表由控制台 SQL 编辑器执行（见 docs 或交付说明），本模块只做数据读写。
"""
import os
import time
from typing import Any

import httpx

_ENV_ID = os.environ.get("CLOUDBASE_ENV_ID") or ""
_API_KEY = os.environ.get("CLOUDBASE_API_KEY") or ""
_API_BASE = os.environ.get("CLOUDBASE_API_BASE") or (
    f"https://{_ENV_ID}.api.tcloudbasegateway.com" if _ENV_ID else ""
)
_REST = _API_BASE.rstrip("/") + "/v1/rdb/rest" if _API_BASE else ""

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=20.0,
            trust_env=False,
            headers={"Authorization": f"Bearer {_API_KEY}"},
        )
    return _client


def _check_config() -> None:
    if not _REST or not _API_KEY:
        raise RuntimeError(
            "CloudBase 数据层未配置：请设置环境变量 CLOUDBASE_ENV_ID 与 CLOUDBASE_API_KEY。"
        )


async def init() -> None:
    """探活：验证配置与网络可达（建表请在控制台 SQL 编辑器执行）。"""
    _check_config()
    resp = await _get_client().get(f"{_REST}/distilled", params={"select": "key", "limit": 1})
    resp.raise_for_status()


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def rpc(name: str, payload: dict) -> Any:
    """Call one audited PostgreSQL function through the existing PostgREST gateway."""
    _check_config()
    if not name or not name.replace("_", "").isalnum():
        raise ValueError("RPC 名称不合法")
    resp = await _get_client().post(
        f"{_REST}/rpc/{name}", headers={"Prefer": "return=representation"}, json=payload,
    )
    resp.raise_for_status()
    if not resp.content:
        return None
    return resp.json()


async def upsert(key: str, title: str, url: str, content: str, images: list,
                 at: int | None = None, *, current_commit: str | None = None,
                 updated_by_uid: str | None = None) -> None:
    _check_config()
    row = {
        "key": key,
        "title": title,
        "url": url,
        "content": content,
        "images": images or [],
        "length": len(content),
        "at": at or int(time.time()),
    }
    if current_commit is not None:
        row["current_commit"] = current_commit
    if updated_by_uid is not None:
        row["updated_by_uid"] = updated_by_uid
    resp = await _get_client().post(
        f"{_REST}/distilled",
        headers={"Prefer": "resolution=merge-duplicates"},
        json=row,
    )
    resp.raise_for_status()


async def index() -> dict:
    """已蒸馏索引：key -> {title, length, at, images, cover}

    images 统一成纯 URL 列表（库里可能存的是 {url,pos} 对象，卡片只关心图本身）。
    """
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/distilled",
        params={"select": "key,title,length,images,at,current_commit"},
    )
    resp.raise_for_status()
    out = {}
    for row in resp.json():
        raw = row.get("images") or []
        if isinstance(raw, str):
            raw = []
        urls = [x.get("url") if isinstance(x, dict) else x for x in raw]
        urls = [u for u in urls if isinstance(u, str) and u]
        out[row["key"]] = {
            "title": row.get("title") or "",
            "length": row.get("length") or 0,
            "at": row.get("at") or 0,
            "images": urls,
            "cover": urls[0] if urls else None,
            "current_commit": row.get("current_commit"),
        }
    return out


async def get(key: str) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/distilled", params={"key": f"eq.{key}", "limit": 1}
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    row = rows[0]
    imgs = row.get("images") or []
    if isinstance(imgs, str):
        imgs = []
    return {
        "key": row["key"], "title": row.get("title") or "", "url": row.get("url") or "",
        "content": row.get("content") or "", "images": imgs, "at": row.get("at") or 0,
        "current_commit": row.get("current_commit"),
    }


async def get_content_update(update_id: str, uid: str) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/content_updates",
        params={"id": f"eq.{update_id}", "uid": f"eq.{uid}", "limit": 1},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def get_article_version(article_key: str, git_commit: str) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_articles",
        params={"article_key": f"eq.{article_key}", "git_commit": f"eq.{git_commit}",
                "limit": 1},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def list_article_versions(article_key: str, *, limit: int = 20,
                                before: str | None = None) -> list:
    _check_config()
    params = {
        "article_key": f"eq.{article_key}",
        "select": "article_key,git_commit,parent_commit,summary,manifest_sha256,published_at",
        "order": "published_at.desc",
        "limit": str(max(1, min(limit, 100))),
    }
    if before:
        params["published_at"] = f"lt.{before}"
    resp = await _get_client().get(f"{_REST}/reading_articles", params=params)
    resp.raise_for_status()
    return resp.json()


async def begin_content_update(*, uid: str, update_id: str, idempotency_key: str,
                               article_key: str, expected_commit: str | None,
                               request_hash: str, candidate_json: dict) -> dict:
    data = await rpc("begin_content_update", {
        "actor_uid": uid,
        "p_update_id": update_id,
        "p_idempotency_key": idempotency_key,
        "p_article_key": article_key,
        "p_expected_commit": expected_commit,
        "p_request_hash": request_hash,
        "p_candidate_json": candidate_json,
    })
    return data[0] if isinstance(data, list) and data else (data or {})


async def claim_content_update(executor_token: str) -> dict | None:
    data = await rpc("claim_content_update", {"p_executor_token": executor_token})
    return data[0] if isinstance(data, list) and data else None


async def save_content_candidate(update_id: str, executor_token: str,
                                 candidate_json: dict) -> None:
    await rpc("save_content_candidate", {
        "p_update_id": update_id,
        "p_executor_token": executor_token,
        "p_candidate_json": candidate_json,
    })


async def mark_content_git_saved(update_id: str, executor_token: str,
                                 candidate_commit: str) -> None:
    await rpc("mark_content_git_saved", {
        "p_update_id": update_id,
        "p_executor_token": executor_token,
        "p_candidate_commit": candidate_commit,
    })


async def publish_content_update(update_id: str, executor_token: str) -> dict:
    data = await rpc("publish_content_update", {
        "p_update_id": update_id,
        "p_executor_token": executor_token,
    })
    return data[0] if isinstance(data, list) and data else (data or {})


async def fail_content_update(update_id: str, executor_token: str,
                              error_code: str) -> None:
    await rpc("fail_content_update", {
        "p_update_id": update_id,
        "p_executor_token": executor_token,
        "p_error_code": error_code,
    })


async def delete(key: str) -> None:
    """删除一篇全文（「更新文章」会先删再重新保存）。

    ⚠️ 不可恢复；全文按内容键全局共享，删了所有人都要重新保存。
    """
    _check_config()
    resp = await _get_client().delete(f"{_REST}/distilled", params={"key": f"eq.{key}"})
    resp.raise_for_status()


async def keys() -> set:
    _check_config()
    resp = await _get_client().get(f"{_REST}/distilled", params={"select": "key"})
    resp.raise_for_status()
    return {row["key"] for row in resp.json()}


async def count() -> int:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/distilled",
        params={"select": "key", "limit": 1},
        headers={"Prefer": "count=exact"},
    )
    resp.raise_for_status()
    content_range = resp.headers.get("content-range") or "*/0"
    try:
        return int(content_range.split("/")[-1])
    except ValueError:
        return len(resp.json())
