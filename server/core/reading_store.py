# -*- coding: utf-8 -*-
"""学习会话数据层（CloudBase PostgreSQL，经 REST/PostgREST 访问）。

三张表：
- reading_articles：article_key -> {summary, cuts, by_uid, at}（全局共享：划分 + 主旨）
- reading_nodes：节点树。kind: init（初始解释，每段唯一）/ explain（共享选区解释）/ ask（私有提问）
- reading_events：埋点（open / understood / deleted，只追加）
"""
import time

from .store import _REST, _check_config, _get_client


async def get_article(article_key: str) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_articles",
        params={"article_key": f"eq.{article_key}", "limit": 1},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def save_article(article_key: str, summary: str, cuts: list, by_uid: str) -> None:
    """保存划分（首次生效：并发时先到者胜，避免位置锚定被覆盖）"""
    _check_config()
    resp = await _get_client().post(
        f"{_REST}/reading_articles",
        headers={"Prefer": "resolution=ignore-duplicates"},
        json={"article_key": article_key, "summary": summary, "cuts": cuts,
              "by_uid": by_uid, "at": int(time.time())},
    )
    resp.raise_for_status()


async def get_init(article_key: str, seg_index: int) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_nodes",
        params={"article_key": f"eq.{article_key}", "seg_index": f"eq.{seg_index}",
                "kind": "eq.init", "limit": 1},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def list_segment_nodes(article_key: str, seg_index: int, uid: str) -> list:
    """某段的可见节点：共享的 init/explain + 自己的 ask（含嵌套子树，按 id 升序）"""
    _check_config()
    params = {
        "article_key": f"eq.{article_key}",
        "seg_index": f"eq.{seg_index}",
        "or": f"(kind.in.(init,explain),and(kind.eq.ask,uid.eq.{uid}))",
        "order": "id.asc",
    }
    resp = await _get_client().get(f"{_REST}/reading_nodes", params=params)
    resp.raise_for_status()
    return resp.json()


async def create_node(article_key: str, seg_index: int, kind: str, content: str, uid: str,
                      parent_id: int | None = None, pos_start: int | None = None,
                      pos_end: int | None = None, question: str = "") -> dict | None:
    """新建节点。init 每段唯一：并发冲突（409）时返回已存在的那条。"""
    _check_config()
    body = {
        "article_key": article_key, "seg_index": seg_index, "kind": kind,
        "content": content, "uid": uid, "parent_id": parent_id,
        "pos_start": pos_start, "pos_end": pos_end, "question": question,
        "at": int(time.time()),
    }
    resp = await _get_client().post(
        f"{_REST}/reading_nodes",
        headers={"Prefer": "return=representation"},
        json=body,
    )
    if resp.status_code == 409:
        if kind == "init":
            return await get_init(article_key, seg_index)
        return None
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if isinstance(rows, list) and rows else None


async def find_overlap(article_key: str, seg_index: int, pos_start: int, pos_end: int,
                       parent_id: int | None = None) -> list:
    """与给定区间重叠的**同层**共享解释。

    不重叠约束作用于同一父层之内；父子之间允许嵌套（在某一层里再划选生成子层）。
    """
    _check_config()
    params = {
        "article_key": f"eq.{article_key}", "seg_index": f"eq.{seg_index}",
        "kind": "eq.explain",
        "pos_start": f"lt.{pos_end}", "pos_end": f"gt.{pos_start}",
        "select": "id,pos_start,pos_end",
        "parent_id": "is.null" if parent_id is None else f"eq.{parent_id}",
    }
    resp = await _get_client().get(f"{_REST}/reading_nodes", params=params)
    resp.raise_for_status()
    return resp.json()


async def get_node(node_id: int) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_nodes", params={"id": f"eq.{node_id}", "limit": 1})
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def delete_node_tree(node_id: int) -> int:
    """删除节点及其全部子孙（递归收集后一次删除），返回删除数量。"""
    _check_config()
    ids = [node_id]
    frontier = [node_id]
    while frontier:
        parent_list = ",".join(str(i) for i in frontier)
        resp = await _get_client().get(
            f"{_REST}/reading_nodes",
            params={"parent_id": f"in.({parent_list})", "select": "id"},
        )
        resp.raise_for_status()
        children = [row["id"] for row in resp.json()]
        if not children:
            break
        ids.extend(children)
        frontier = children
    id_list = ",".join(str(i) for i in ids)
    resp = await _get_client().delete(f"{_REST}/reading_nodes", params={"id": f"in.({id_list})"})
    resp.raise_for_status()
    return len(ids)


async def append_event(article_key: str, uid: str, event: str,
                       seg_index: int | None = None, node_id: int | None = None) -> None:
    _check_config()
    resp = await _get_client().post(
        f"{_REST}/reading_events",
        json={"article_key": article_key, "uid": uid, "event": event,
              "seg_index": seg_index, "node_id": node_id, "at": int(time.time())},
    )
    resp.raise_for_status()
