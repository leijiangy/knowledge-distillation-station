# -*- coding: utf-8 -*-
"""学习会话数据层（CloudBase PostgreSQL，经 REST/PostgREST 访问）。

五张表：
- reading_articles：article_key -> {summary, cuts, by_uid, at}（全局共享：划分 + 主旨）
- reading_nodes：账号私有节点树。kind: init（本人初始解释，每段唯一）/ explain（本人选区解释）/ ask（本人提问）
- reading_events：埋点（open / understood / deleted，只追加）
- reading_quizzes：自测题目与概括，按内容键全局共享（一人生成、全站复用）
- reading_quiz_attempts：作答记录，按用户私有（只追加，折叠取每题最新）
"""
import json
import time

import httpx

from .store import _REST, _check_config, _get_client, rpc


async def get_article(article_key: str, git_commit: str | None = None) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_articles",
        params={"article_key": f"eq.{article_key}",
                **({"git_commit": f"eq.{git_commit}"} if git_commit else {}),
                "limit": 1},
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


async def get_init(article_key: str, seg_index: int, uid: str) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_nodes",
        params={"article_key": f"eq.{article_key}", "seg_index": f"eq.{seg_index}",
                "kind": "eq.init", "uid": f"eq.{uid}", "limit": 1},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def list_segment_nodes(article_key: str, seg_index: int, uid: str,
                             *, segment_id: str | None = None,
                             include_history: bool = False) -> list:
    """某段的本人节点（含嵌套子树，按 id 升序）。"""
    _check_config()
    if segment_id is not None:
        data = await rpc("list_private_segment_nodes", {
            "actor_uid": uid,
            "p_article_key": article_key,
            "p_segment_id": segment_id,
            "p_include_history": bool(include_history),
        })
        return data if isinstance(data, list) else ([] if data is None else [data])
    params = {
        "article_key": f"eq.{article_key}",
        "seg_index": f"eq.{seg_index}",
        "uid": f"eq.{uid}",
        "order": "id.asc",
    }
    resp = await _get_client().get(f"{_REST}/reading_nodes", params=params)
    resp.raise_for_status()
    return resp.json()


async def get_private_node(node_id: int, uid: str, article_key: str,
                           segment_id: str, *, include_history: bool = False) -> dict | None:
    data = await rpc("get_private_node", {
        "actor_uid": uid,
        "p_node_id": node_id,
        "p_article_key": article_key,
        "p_segment_id": segment_id,
        "p_include_history": bool(include_history),
    })
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) and data else None


async def soft_delete_private_tree(node_id: int, uid: str, article_key: str,
                                   segment_id: str) -> int:
    data = await rpc("delete_private_node_tree", {
        "actor_uid": uid,
        "p_node_id": node_id,
        "p_article_key": article_key,
        "p_segment_id": segment_id,
    })
    if isinstance(data, list):
        data = data[0] if data else {}
    return int((data or {}).get("deleted_count") or 0)


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
            return await get_init(article_key, seg_index, uid)
        return None
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if isinstance(rows, list) and rows else None


async def find_overlap(article_key: str, seg_index: int, pos_start: int, pos_end: int,
                       uid: str, parent_id: int | None = None) -> list:
    """与给定区间重叠的本人同层解释。

    不重叠约束作用于同一父层之内；父子之间允许嵌套（在某一层里再划选生成子层）。
    """
    _check_config()
    params = {
        "article_key": f"eq.{article_key}", "seg_index": f"eq.{seg_index}",
        "uid": f"eq.{uid}",
        "kind": "eq.explain",
        "pos_start": f"lt.{pos_end}", "pos_end": f"gt.{pos_start}",
        "select": "id,pos_start,pos_end",
        "parent_id": "is.null" if parent_id is None else f"eq.{parent_id}",
    }
    resp = await _get_client().get(f"{_REST}/reading_nodes", params=params)
    resp.raise_for_status()
    return resp.json()


async def get_node(node_id: int, uid: str) -> dict | None:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_nodes",
        params={"id": f"eq.{node_id}", "uid": f"eq.{uid}", "limit": 1})
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def delete_node_tree(node_id: int, uid: str) -> int:
    """删除本人的节点及其子孙；不会沿修订关系扩散。"""
    _check_config()
    ids = [node_id]
    frontier = [node_id]
    while frontier:
        parent_list = ",".join(str(i) for i in frontier)
        resp = await _get_client().get(
            f"{_REST}/reading_nodes",
            params={"parent_id": f"in.({parent_list})", "uid": f"eq.{uid}",
                    "select": "id"},
        )
        resp.raise_for_status()
        children = [row["id"] for row in resp.json()]
        if not children:
            break
        ids.extend(children)
        frontier = children
    id_list = ",".join(str(i) for i in ids)
    resp = await _get_client().delete(
        f"{_REST}/reading_nodes",
        params={"id": f"in.({id_list})", "uid": f"eq.{uid}"},
    )
    resp.raise_for_status()
    return len(ids)


async def append_event(article_key: str, uid: str, event: str,
                       seg_index: int | None = None, node_id: int | None = None,
                       *, git_commit: str | None = None,
                       segment_id: str | None = None) -> None:
    _check_config()
    body = {"article_key": article_key, "uid": uid, "event": event,
            "seg_index": seg_index, "node_id": node_id, "at": int(time.time())}
    if git_commit is not None:
        body["git_commit"] = git_commit
    if segment_id is not None:
        body["segment_id"] = segment_id
    resp = await _get_client().post(
        f"{_REST}/reading_events",
        json=body,
    )
    resp.raise_for_status()


def collapse_history(rows: list) -> list:
    """把 open 埋点折叠成学习记录：每篇文章只保留最后读到的那一段（纯函数，便于测试）"""
    latest = {}
    for row in rows or []:
        key = row.get("article_key")
        if not key:
            continue
        at = row.get("at") or 0
        cur = latest.get(key)
        if cur is None or at > cur["at"]:
            latest[key] = {"at": at, "seg_index": row.get("seg_index") or 0,
                           "git_commit": row.get("git_commit"),
                           "segment_id": row.get("segment_id")}
    out = []
    for key, value in latest.items():
        item = {"article_key": key, "seg_index": value["seg_index"], "at": value["at"]}
        if value["git_commit"] is not None:
            item["git_commit"] = value["git_commit"]
        if value["segment_id"] is not None:
            item["segment_id"] = value["segment_id"]
        out.append(item)
    out.sort(key=lambda r: r["at"], reverse=True)
    return out


async def list_recent_opened(uid: str, limit: int = 200) -> list:
    """学习记录：每篇文章最后读到哪一段（来自 open 埋点）"""
    _check_config()
    client = _get_client()
    params = {"select": "article_key,git_commit,segment_id,seg_index,at",
              "uid": f"eq.{uid}", "event": "eq.open",
              "order": "at.desc", "limit": str(limit)}
    resp = await client.get(f"{_REST}/reading_events", params=params)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.lower()
        if "git_commit" not in detail and "segment_id" not in detail:
            raise
        params["select"] = "article_key,seg_index,at"
        resp = await client.get(f"{_REST}/reading_events", params=params)
        resp.raise_for_status()
    return collapse_history(resp.json())


async def delete_plan_and_nodes(article_key: str) -> int:
    """删掉一篇的段落划分与全部节点，返回删除的节点数。

    全文被重置后，锚在它上面的段落切点与位置标记（pos_start / pos_end）失去参照，
    留着只会在重新保存全文后锚到错的文字上，所以必须一起清。
    """
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_nodes", params={"article_key": f"eq.{article_key}", "select": "id"})
    resp.raise_for_status()
    count = len(resp.json())
    for table in ("reading_nodes", "reading_articles"):
        resp = await _get_client().delete(f"{_REST}/{table}",
                                          params={"article_key": f"eq.{article_key}"})
        resp.raise_for_status()
    return count


async def delete_user_events(article_key: str, uid: str) -> None:
    """删掉某个用户在某一篇上的埋点（删的是他的学习记录本身，不动别人的）"""
    _check_config()
    resp = await _get_client().delete(
        f"{_REST}/reading_events",
        params={"article_key": f"eq.{article_key}", "uid": f"eq.{uid}"},
    )
    resp.raise_for_status()


async def get_image_explanation(image_url: str) -> str:
    """取某张配图的解释（按图片 URL 全局缓存：同一张图只生成一次，省额度）

    表 image_explanations(url text primary key, content text, at bigint)，见设计文档 7.2。
    """
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/image_explanations", params={"url": f"eq.{image_url}", "limit": 1})
    resp.raise_for_status()
    rows = resp.json()
    return (rows[0].get("content") or "") if rows else ""


async def save_image_explanation(image_url: str, content: str) -> None:
    _check_config()
    resp = await _get_client().post(
        f"{_REST}/image_explanations",
        headers={"Prefer": "resolution=merge-duplicates"},
        json={"url": image_url, "content": content, "at": int(time.time())},
    )
    resp.raise_for_status()


# ---- 自测（题目按内容键全局共享，一人生成全站复用；进度按用户私有） ----

async def get_quiz(article_key: str) -> dict | None:
    """取一篇的题目与概括（表 reading_quizzes，见设计文档 8.3）"""
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_quizzes",
        params={"article_key": f"eq.{article_key}", "limit": 1},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def save_quiz(article_key: str, summary: str, questions: list, by_uid: str) -> None:
    """保存题目与概括（首次生效：并发时先到者胜，保证所有人做到同一套题）"""
    _check_config()
    resp = await _get_client().post(
        f"{_REST}/reading_quizzes",
        headers={"Prefer": "resolution=ignore-duplicates"},
        json={"article_key": article_key, "summary": summary,
              "questions": json.dumps(questions, ensure_ascii=False),
              "by_uid": by_uid, "at": int(time.time())},
    )
    resp.raise_for_status()


def load_questions(row: dict | None) -> list:
    """题目字段在库里是文本，取出来要能容忍损坏（纯函数，便于测试）"""
    if not row:
        return []
    raw = row.get("questions")
    if isinstance(raw, list):
        return raw
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


async def delete_quiz(article_key: str) -> None:
    """重置一篇时连题目一起删（题目锚在这篇全文上）"""
    _check_config()
    resp = await _get_client().delete(
        f"{_REST}/reading_quizzes", params={"article_key": f"eq.{article_key}"})
    resp.raise_for_status()


async def append_quiz_attempt(article_key: str, uid: str, q_index: int,
                              correct: bool, chosen: str) -> None:
    """记一次作答（只追加；同一个问题重答会有多条，取最新的看进度）"""
    _check_config()
    resp = await _get_client().post(
        f"{_REST}/reading_quiz_attempts",
        json={"article_key": article_key, "uid": uid, "q_index": q_index,
              "correct": bool(correct), "chosen": chosen, "at": int(time.time())},
    )
    resp.raise_for_status()


async def list_quiz_attempts(article_key: str, uid: str) -> list:
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_quiz_attempts",
        params={"article_key": f"eq.{article_key}", "uid": f"eq.{uid}",
                "order": "at.asc"},
    )
    resp.raise_for_status()
    return resp.json()


def collapse_attempts(rows: list) -> dict:
    """把作答记录折叠成每题的最新一次（纯函数，便于测试）"""
    latest = {}
    for row in rows or []:
        idx = row.get("q_index")
        if not isinstance(idx, int):
            continue
        at = row.get("at") or 0
        cur = latest.get(idx)
        if cur is None or at >= cur["at"]:
            latest[idx] = {"at": at, "correct": bool(row.get("correct")),
                           "chosen": row.get("chosen") or ""}
    return latest


async def delete_quiz_attempts(article_key: str, uid: str) -> None:
    """用户的作答记录（随学习记录一起重置）"""
    _check_config()
    resp = await _get_client().delete(
        f"{_REST}/reading_quiz_attempts",
        params={"article_key": f"eq.{article_key}", "uid": f"eq.{uid}"},
    )
    resp.raise_for_status()


async def list_user_questions(article_key: str, uid: str, limit: int = 20) -> list:
    """我在这一篇上问过的问题（私有提问节点），按时间升序——复习时用来点出我卡住的地方"""
    _check_config()
    resp = await _get_client().get(
        f"{_REST}/reading_nodes",
        params={"select": "content,seg_index", "article_key": f"eq.{article_key}",
                "uid": f"eq.{uid}", "kind": "eq.ask",
                "order": "id.asc", "limit": str(limit)},
    )
    resp.raise_for_status()
    return [row.get("content") or "" for row in resp.json() if row.get("content")]


def collect_weak_points(questions: list, attempts: dict) -> list:
    """这一轮的薄弱点 = 答错的题 + 跳过的题（纯函数，便于测试）。

    「跳过 = 用户不懂」，所以没作答记录的题也算薄弱点；
    但用户还没开始自测（attempts 为空且一题都没答）时不算——那时谈不上薄弱点。
    取每题最新一次作答（来自 collapse_attempts），已答对的题不再翻旧账。
    """
    out = []
    for i, q in enumerate(questions or []):
        if not isinstance(q, dict):
            continue
        done = attempts.get(i)
        if done and done.get("correct"):
            continue                      # 这轮答对了，说明会了
        answer = q.get("answer")
        if q.get("kind") == "choice":
            options = q.get("options") or []
            correct_text = options[answer] if isinstance(answer, int) and not isinstance(answer, bool) \
                and 0 <= answer < len(options) else ""
        else:
            correct_text = "对" if answer is True else ("错" if answer is False else "")
        out.append({"stem": q.get("stem") or "", "answer": correct_text,
                    "chosen": (done or {}).get("chosen") or "",
                    "skipped": done is None})
    return out
