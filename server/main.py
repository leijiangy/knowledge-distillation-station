# -*- coding: utf-8 -*-
"""知识蒸馏站 —— FastAPI 应用入口与路由（对照官方 Node 模板的接口形态）"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.sessions import sessions
from core.cache import content_cache, user_cache, user_key
from core import ai, advanced, analyze, billing, content_versions, oauth, reading_store, segments, store, zhihu

_content_repo: content_versions.BareContentRepository | None = None
_content_worker_task: asyncio.Task | None = None
_billing_worker_task: asyncio.Task | None = None
_billing_inflight: set[asyncio.Task] = set()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _content_repo, _content_worker_task, _billing_worker_task
    try:
        await store.init()
    except Exception as exc:
        print(f"[store] 持久化层初始化失败：{exc}")
    if settings.CONTENT_GIT_DIR:
        try:
            _content_repo = content_versions.BareContentRepository(settings.CONTENT_GIT_DIR)
            _content_repo.initialize()
            _content_worker_task = asyncio.create_task(_content_worker_loop())
        except Exception as exc:
            _content_repo = None
            print(f"[content] Git版本存储初始化失败：{exc}")
    if settings.BILLING_ENABLED:
        _billing_worker_task = asyncio.create_task(_billing_worker_loop())
    try:
        yield
    finally:
        if _content_worker_task is not None:
            _content_worker_task.cancel()
            try:
                await _content_worker_task
            except asyncio.CancelledError:
                pass
            _content_worker_task = None
        if _billing_worker_task is not None:
            _billing_worker_task.cancel()
            try:
                await _billing_worker_task
            except asyncio.CancelledError:
                pass
            _billing_worker_task = None
        if _billing_inflight:
            await asyncio.gather(*tuple(_billing_inflight), return_exceptions=True)
        await store.close()


app = FastAPI(title=settings.PROJECT_NAME, docs_url=None, redoc_url=None,
              lifespan=_lifespan)

# 旧书签仅需要拿到升级错误；新导入通过本站弹窗和同源 Cookie 完成。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.zhihu.com", "https://zhuanlan.zhihu.com"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _current_session(request: Request):
    return sessions.get(request.cookies.get(settings.SESSION_COOKIE))


def _attach_cookie(response: Response, session) -> None:
    response.set_cookie(
        settings.SESSION_COOKIE,
        session.id,
        httponly=True,
        samesite="lax",
        max_age=settings.SESSION_TTL_SECONDS,
        secure=bool(settings.REDIRECT_URI),  # 部署为 HTTPS 后启用 Secure
    )


def _get_or_create(request: Request, response: Response):
    session = _current_session(request)
    if session is None:
        session = sessions.create()
        _attach_cookie(response, session)
    return session


def _user_key_id(session) -> str:
    """私有缓存的用户标识"""
    return str((session.profile or {}).get("uid") or "anon") if session else "self"


def require_account(request: Request):
    """Return a real Zhihu uid for private data, publishing and money flows."""
    session = _current_session(request)
    if session is None or not session.token:
        return None, {"code": "LOGIN_REQUIRED", "message": "请先登录知乎账号。"}
    if session.expires_at is not None and session.expires_at <= time.time():
        session.token = None
        session.profile = None
        return None, {"code": "SESSION_EXPIRED", "message": "授权已过期，请重新连接。"}
    uid = oauth.profile_uid(session.profile)
    if uid is None:
        return None, {"code": "ACCOUNT_ID_REQUIRED", "message": "账号缺少有效知乎 uid，请重新授权。"}
    return uid, None


def api_error(code: str, message: str, status: int, **details):
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse({"ok": False, "error": error}, status_code=status)


def _resolve_token(session):
    """返回 (oauth_token, error)。

    未登录时：仅当 ALLOW_SELF_MODE=1（本地调试开关）才允许以本人身份读取；
    否则返回 LOGIN_REQUIRED —— 公网部署下任何匿名请求都必须被拒绝。
    """
    if session and session.token:
        return session.token, None
    if settings.ALLOW_SELF_MODE:
        return None, None
    return None, {"code": "LOGIN_REQUIRED", "message": "请先登录知乎账号。"}


@app.get("/api/health")
async def health():
    return {"ok": True, "project": settings.PROJECT_NAME}


@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    """静态资源不做强缓存（HTML / CSS / JS）：更新后刷新即可拿到新版，避免旧页面、旧样式"""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js", ".mjs")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path.startswith(("/api/reading", "/api/billing", "/api/ingest")):
        response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/api/oauth/status")
async def oauth_status(request: Request, response: Response):
    session = _get_or_create(request, response)
    if session.expires_at and session.expires_at <= time.time():
        session.token = None
        session.profile = None
        session.error = {"code": "TOKEN_EXPIRED", "message": "授权已过期，请重新连接。"}
    return {
        "ok": True,
        "configured": settings.oauth_ready,
        "callback_configured": bool(settings.REDIRECT_URI),
        "self_mode": settings.ALLOW_SELF_MODE,  # 本地预览模式（未登录直接读本人收藏）
        "authorized": bool(session.token),
        "app_id": settings.APP_ID,
        "redirect_uri": settings.REDIRECT_URI or None,
        "profile": session.profile,
        "state_verified": session.state_verified,
        "error": session.error,
    }


@app.get("/api/oauth/start")
async def oauth_start(request: Request, response: Response, next: str = ""):
    session = _get_or_create(request, response)
    # 登录完要落到哪：保存后跳回精读靠它穿过授权流程（默认回到首页）
    session.next_path = oauth.local_path(next) or None
    try:
        session.state = oauth.new_state()
        session.error = None
        url = oauth.build_authorize_url(session.state)
        redirect = RedirectResponse(url, status_code=302)
        _attach_cookie(redirect, session)   # 新建的会话也要把 cookie 带出去
        return redirect
    except oauth.ZhihuError as exc:
        session.error = {"code": str(exc.code), "message": exc.message}
        redirect = RedirectResponse("/?oauth=error", status_code=302)
        _attach_cookie(redirect, session)
        return redirect


@app.get("/auth/callback")
async def auth_callback(request: Request):
    session = _current_session(request)
    if session is None:
        return RedirectResponse("/?oauth=error", status_code=302)
    params = request.query_params
    code = params.get("authorization_code") or params.get("code")
    returned_state = params.get("state")
    try:
        if not code:
            raise oauth.ZhihuError("CODE_MISSING", "回调缺少 authorization_code。")
        verdict = oauth.check_state(returned_state, session.state)
        if verdict == "missing":
            raise oauth.ZhihuError("STATE_MISSING", "登录状态已失效，请重新发起登录。")
        if verdict == "mismatch":
            raise oauth.ZhihuError("STATE_MISMATCH", "state 校验失败，登录已拒绝。")
        token = await oauth.exchange_token(code)
        session.token = token["access_token"]
        session.expires_at = (time.time() + token["expires_in"]) if token["expires_in"] else None
        session.state_verified = verdict == "verified"
        session.state = None  # state 一次性消费
        session.error = None
        try:
            session.profile = await oauth.fetch_profile(session.token)
        except oauth.ZhihuError as exc:
            session.profile = None  # 收藏读取仍可用；私人功能会要求重新授权取得 uid
            session.error = {"code": str(exc.code), "message": exc.message}
        redirect = RedirectResponse(session.next_path or "/?oauth=success", status_code=302)
        session.next_path = None
        _attach_cookie(redirect, session)
        return redirect
    except oauth.ZhihuError as exc:
        session.error = {"code": str(exc.code), "message": exc.message}
        redirect = RedirectResponse("/?oauth=error", status_code=302)
        _attach_cookie(redirect, session)
        return redirect


@app.post("/api/oauth/logout")
async def oauth_logout(request: Request):
    session = _current_session(request)
    if session:
        session.token = None
        session.expires_at = None
        session.profile = None
        session.state = None
        session.state_verified = None
        session.error = None
        sessions.drop(session.id)
    return {"ok": True}


def _enrich_with_shared_metrics(items: list) -> list:
    """为收藏条目附加三指标与排序用综合分；指标按内容 URL 全站共享缓存"""
    enriched = []
    for item in items:
        key = "metrics:" + str(item.get("Url") or item.get("Title") or "")
        metrics = content_cache.get(key)
        if metrics is None:
            metrics = analyze.metrics_for(item)
            content_cache.set(key, metrics)
        row = dict(item)
        row["metrics"] = metrics
        row["combined_score"] = analyze.combined_score(metrics)
        enriched.append(row)
    return enriched


@app.get("/api/favlists")
async def favlists(request: Request, force: int = 0):
    """收藏夹列表（需登录；本地 ALLOW_SELF_MODE 下可本人模式自测）"""
    session = _current_session(request)
    token, login_error = _resolve_token(session)
    if login_error:
        return {"ok": False, "error": login_error}
    cache_key = user_key("favlists", _user_key_id(session))
    if not force:
        cached = user_cache.get(cache_key)
        if cached is not None:
            return {"ok": True, "cached": True, "items": cached}
    try:
        items = await zhihu.fetch_favlists(oauth_token=token)
        user_cache.set(cache_key, items)
        return {"ok": True, "cached": False, "items": items}
    except zhihu.ZhihuError as exc:
        return {"ok": False, "error": {"code": str(exc.code), "message": exc.message}}


@app.get("/api/collections")
async def collections(request: Request, favlist: str | None = None, force: int = 0):
    """收藏全量读取（分页取全 + 用户私有缓存 + 三指标）。

    favlist 缺省时取用户第一个收藏夹；force=1 时绕过用户缓存主动取新数据（用户点「刷新」）。
    """
    session = _current_session(request)
    token, login_error = _resolve_token(session)
    if login_error:
        return {"ok": False, "error": login_error}
    cache_key = user_key("collections:" + str(favlist or "first"), _user_key_id(session))
    if not force:
        cached = user_cache.get(cache_key)
        if cached is not None:
            return {"ok": True, "cached": True, **cached}
    try:
        fav_meta = None
        if favlist:
            fav_token = favlist
        else:
            lists = await zhihu.fetch_favlists(oauth_token=token)
            if not lists:
                return {"ok": True, "cached": False, "favlist": None, "count": 0,
                        "items": [], "message": "账号下没有可读取的收藏夹。"}
            fav_meta = lists[0]
            fav_token = fav_meta.get("UrlToken")
        raw_items = await zhihu.fetch_all_favlist_contents(fav_token, oauth_token=token)
        payload = {
            "favlist": fav_meta,
            "count": len(raw_items),
            "items": _enrich_with_shared_metrics(raw_items),
            "loaded_at": int(time.time()),  # 数据加载时刻（缓存命中时返回原加载时间）
        }
        user_cache.set(cache_key, payload)
        return {"ok": True, "cached": False, **payload}
    except zhihu.ZhihuError as exc:
        return {"ok": False, "error": {"code": str(exc.code), "message": exc.message}}


@app.post("/api/article_meta")
async def article_meta(request: Request):
    """批量补充文章作者信息（头像/认证徽章/权威度/签名）——用标题搜索匹配，服务端缓存 1 天。

    前端按页批量调用（懒加载），单次最多 12 条，串行 + 温和限速。
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    items = (body.get("items") or [])[:12]
    meta_map = {}
    for item in items:
        url = str(item.get("url") or "")
        title = str(item.get("title") or "")
        if not url:
            continue
        key = "meta:" + url
        cached = content_cache.get(key)
        if cached is not None:
            meta_map[url] = cached
            continue
        if not title:
            meta_map[url] = None
            continue
        try:
            found = await zhihu.search_zhihu(title, count=3)
        except zhihu.ZhihuError:
            meta_map[url] = None
            continue
        base = url.split("?")[0]
        match = next((r for r in found if str(r.get("Url", "")).split("?")[0] == base), None)
        if match is None and found:
            match = found[0]
        meta = None
        if match:
            meta = {
                "author_name": match.get("AuthorName") or "",
                "avatar": match.get("AuthorAvatar") or "",
                "badge": match.get("AuthorBadge") or "",
                "badge_text": match.get("AuthorBadgeText") or "",
                "authority_level": match.get("AuthorityLevel"),
                "signature": match.get("AuthorSignature") or "",
                # 该接口的 AuthorSignature 实际是作者主页 UrlToken，用于拼主页链接
                "author_token": match.get("AuthorSignature") or "",
            }
            content_cache.set(key, meta)
        meta_map[url] = meta
        await asyncio.sleep(0.15)  # 温和限速，避免触发风控
    return {"ok": True, "meta": meta_map}


# ---- 书签小工具：全文接收与存储（T9：CloudBase PostgreSQL，经 REST API 访问） ----
DISTILL_MAX_CHARS = 200_000

# URL 归一化：收藏列表给回答是短格式 /answer/<id>，知乎页面地址是长格式
# /question/<qid>/answer/<id> —— 统一成短格式，保证卡片匹配与去重一致。
# 问号段用 [^/]+ 而不是 \d+：知乎回答页的 og:url 会出现 /question/undefined/answer/<id>，
# 只认数字会让这类地址漏过归一化，键与卡片永远对不上
_ANSWER_URL_RE = re.compile(r"^https?://(?:www\.)?zhihu\.com/question/[^/]+/answer/(\d+)")


def _norm_key(url: str) -> str:
    s = str(url or "").split("?")[0].split("#")[0]
    match = _ANSWER_URL_RE.match(s)
    if match:
        return f"https://www.zhihu.com/answer/{match.group(1)}"
    return s


def _canonical_uuid(value, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{name}必须是UUID") from exc


async def _process_content_update(job: dict, executor_token: str) -> None:
    if _content_repo is None:
        raise content_versions.GitStorageError("VERSION_STORAGE_UNAVAILABLE")
    update_id = str(job["id"])
    expected = job.get("expected_commit")
    candidate = job.get("candidate_json") or {}
    if isinstance(candidate, str):
        candidate = json.loads(candidate)

    if job.get("state") == "git_saved" and job.get("candidate_commit"):
        published = await store.publish_content_update(update_id, executor_token)
        if published.get("state") == "published":
            try:
                _content_repo.update_branch(
                    str(job["article_key"]), str(job["candidate_commit"]), expected
                )
            except content_versions.GitRefConflict:
                pass
        return

    if "manifest" not in candidate or "summary" not in candidate:
        source = candidate.get("source") or candidate
        content = str(source.get("content") or "")
        result = await ai.segment_article(str(source.get("title") or ""), content)
        cuts = ai.normalize_cuts(result.get("cuts"), len(content))
        summary = str(result.get("summary") or "")
        histories: list[content_versions.HistoricalVersion] = []
        if expected:
            old = _content_repo.read_version(str(expected))
            parent = old.parent
            while parent and len(histories) < 100:
                historical = _content_repo.read_version(parent)
                histories.append(content_versions.HistoricalVersion(
                    historical.content, historical.manifest
                ))
                parent = historical.parent
            manifest = content_versions.inherit_manifest(
                old.content, old.manifest, content, cuts,
                historical_versions=histories,
                block_provider=_content_repo.git_unchanged_blocks,
            )
        else:
            manifest = content_versions.build_manifest(content, cuts)
        candidate = {
            "source": source,
            "summary": summary,
            "cuts": cuts,
            "manifest": manifest,
            "manifest_sha256": content_versions.manifest_sha256(manifest),
            "committed_at": job.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }
        await store.save_content_candidate(update_id, executor_token, candidate)

    source = candidate["source"]
    commit = _content_repo.create_version(
        article_key=str(job["article_key"]),
        update_id=update_id,
        content=str(source["content"]),
        meta={
            "schema_version": 1,
            "article_key": str(job["article_key"]),
            "url": str(source["url"]),
            "title": str(source["title"]),
            "images": source.get("images") or [],
            "summary": str(candidate["summary"]),
            "update_id": update_id,
        },
        manifest=candidate["manifest"],
        parent=str(expected) if expected else None,
        committed_at=str(candidate["committed_at"]),
    )
    await store.mark_content_git_saved(update_id, executor_token, commit)
    published = await store.publish_content_update(update_id, executor_token)
    if published.get("state") == "published":
        try:
            _content_repo.update_branch(str(job["article_key"]), commit, expected)
        except content_versions.GitRefConflict:
            pass


async def _content_worker_loop() -> None:
    while True:
        executor_token = str(uuid4())
        try:
            job = await store.claim_content_update(executor_token)
            if job is None:
                await asyncio.sleep(1)
                continue
            try:
                await _process_content_update(job, executor_token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[content] 更新任务失败：{exc}")
                try:
                    await store.fail_content_update(
                        str(job["id"]), executor_token, type(exc).__name__[:80]
                    )
                except Exception as save_exc:
                    print(f"[content] 记录任务失败状态失败：{save_exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[content] 领取更新任务失败：{exc}")
            await asyncio.sleep(30)


async def _run_billing_operation(job: dict) -> None:
    operation_id = str(job["id"])
    executor_token = str(job["executor_token"])
    try:
        if job.get("status") == billing.OperationStatus.RESULT_RECORDED.value:
            await billing.finalize_operation(operation_id)
            return
        prepared = job.get("messages_json") or {}
        if isinstance(prepared, str):
            prepared = json.loads(prepared)
        if job.get("action") == "advanced":
            if prepared.get("executor") != "dsh":
                raise ai.AIError("高级解释执行器不匹配。")
            result = await advanced.execute(prepared)
        else:
            result = await ai.execute_chat(prepared)
        await billing.record_result(operation_id, executor_token, result)
        await billing.finalize_operation(operation_id)
    except billing.BillingContractError as exc:
        await billing.waive_operation(operation_id, exc.code)
    except ai.AIError:
        await billing.waive_operation(operation_id, "AI_FAILED")
    except Exception as exc:
        print(f"[billing] 操作恢复等待：{operation_id} {exc}")


async def _billing_worker_loop() -> None:
    while True:
        try:
            _billing_inflight.difference_update(
                task for task in _billing_inflight if task.done()
            )
            if len(_billing_inflight) >= 4:
                await asyncio.sleep(0.1)
                continue
            executor_token = str(uuid4())
            job = await billing.claim_operation(executor_token)
            if job is None:
                await asyncio.sleep(1)
                continue
            task = asyncio.create_task(_run_billing_operation(job))
            _billing_inflight.add(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[billing] 领取操作失败：{exc}")
            await asyncio.sleep(30)


@app.post("/api/ingest")
async def ingest(request: Request):
    """预览或创建一个异步内容版本更新任务。"""
    if request.headers.get("origin") in {
        "https://www.zhihu.com", "https://zhuanlan.zhihu.com"
    }:
        return api_error(
            "BOOKMARKLET_UPDATE_REQUIRED",
            "书签工具已升级，请从本站重新复制后再保存文章。",
            410,
        )
    uid, account_error = require_account(request)
    if account_error:
        return api_error(account_error["code"], account_error["message"], 401)
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip()[:200]
    content = str(body.get("content") or "").strip()
    images = body.get("images") or []
    # 图片地址要排在前面判：zhimg.com 不含子串 zhihu.com，否则会被下一条笼统地拦掉
    if zhihu.is_image_url(url):
        return {"ok": False, "error": {
            "code": "BAD_URL",
            "message": "这是图片地址而不是文章页（可能点开了大图）：请回到文章页面再保存。"}}
    if not url or "zhihu.com" not in url:
        return {"ok": False, "error": {"code": "BAD_URL", "message": "需要知乎内容链接。"}}
    if len(content) < 100:
        return {"ok": False, "error": {"code": "TOO_SHORT", "message": "内容过短，可能不是文章页。"}}
    if len(content) > DISTILL_MAX_CHARS:
        return {"ok": False, "error": {"code": "TOO_LONG", "message": "内容过长。"}}
    clean_images: list = []
    seen_images: set = set()
    if isinstance(images, list):
        for item in images[:9]:
            # 兼容两种形态：早期只存 URL 字符串；现在带正文里的字符位置
            raw_url = item.get("url") if isinstance(item, dict) else item
            image_url = zhihu.clean_image_url(raw_url)
            if not image_url or image_url in seen_images:
                continue
            pos = item.get("pos") if isinstance(item, dict) else None
            if isinstance(pos, bool) or not isinstance(pos, int) or not (0 <= pos <= len(content)):
                pos = None      # 位置不可信就当没有：宁可退回文章级展示，也不要锚错地方
            seen_images.add(image_url)
            clean_images.append({"url": image_url, "pos": pos})
    key = _norm_key(url)
    existing = await store.get(key)
    if existing is not None and not existing.get("current_commit"):
        return api_error(
            "VERSION_MIGRATION_REQUIRED",
            "这篇文章尚未建立版本基线，请先完成内容迁移。",
            503,
        )
    current_commit = (existing or {}).get("current_commit")
    unchanged = bool(existing and existing.get("title") == title
                     and existing.get("url") == url
                     and existing.get("content") == content
                     and (existing.get("images") or []) == clean_images)
    if body.get("preview_only") is True:
        return {"ok": True, "current_commit": current_commit,
                "unchanged": unchanged, "length": len(content),
                "images": len(clean_images)}
    if _content_repo is None:
        return api_error(
            "VERSION_STORAGE_UNAVAILABLE", "内容版本存储尚未配置。", 503
        )
    if unchanged:
        return {"ok": True, "unchanged": True, "commit": current_commit,
                "length": len(content), "images": len(clean_images)}
    if "expected_current_commit" not in body:
        return api_error("INVALID_INPUT", "缺少expected_current_commit。", 422)
    expected = body.get("expected_current_commit")
    if expected is not None:
        expected = str(expected)
    if expected != current_commit:
        return api_error(
            "VERSION_CONFLICT", "文章版本已变化，请重新预览。", 409,
            latest_commit=current_commit,
        )
    try:
        idempotency_key = _canonical_uuid(body.get("idempotency_key"), "idempotency_key")
    except ValueError as exc:
        return api_error("INVALID_INPUT", str(exc), 422)
    update_id = str(uuid4())
    source = {"url": url, "title": title, "content": content, "images": clean_images}
    request_hash = billing.stable_json_hash({
        "article_key": key, "expected_commit": expected, "source": source
    })
    try:
        task = await store.begin_content_update(
            uid=uid, update_id=update_id, idempotency_key=idempotency_key,
            article_key=key, expected_commit=expected, request_hash=request_hash,
            candidate_json={"source": source},
        )
    except Exception as exc:
        print(f"[content] 创建更新任务失败：{exc}")
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法创建内容更新任务。", 503)
    return JSONResponse({"ok": True, "update_id": task.get("id", update_id),
                         "state": task.get("state", "queued")}, status_code=202)


@app.get("/api/ingest/{update_id}")
async def ingest_status(request: Request, update_id: str):
    uid, account_error = require_account(request)
    if account_error:
        return api_error(account_error["code"], account_error["message"], 401)
    try:
        update_id = _canonical_uuid(update_id, "update_id")
        task = await store.get_content_update(update_id, uid)
    except ValueError as exc:
        return api_error("INVALID_INPUT", str(exc), 422)
    except Exception:
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法读取更新任务。", 503)
    if task is None:
        return api_error("UPDATE_NOT_FOUND", "更新任务不存在。", 404)
    return {"ok": True, "update_id": task["id"], "state": task["state"],
            "commit": task.get("candidate_commit") if task["state"] == "published" else None,
            "error_code": task.get("error_code")}


@app.get("/api/distilled")
async def distilled_index():
    """已蒸馏内容索引（供列表打标：哪些收藏已有全文）"""
    return {"ok": True, "items": await store.index()}


@app.delete("/api/distilled")
async def delete_distilled(request: Request, url: str):
    """旧更新协议已停用；更新必须创建新版本，不能删除共享资产。"""
    _uid, account_error = require_account(request)
    if account_error:
        return api_error(account_error["code"], account_error["message"], 401)
    return api_error(
        "ARTICLE_UPDATE_REQUIRES_IMPORT",
        "更新文章请使用新版书签导入，它会保留旧版本和已有解释。",
        410,
    )


@app.get("/api/distilled/content")
async def distilled_content(url: str, version: str | None = None):
    """读取某篇已蒸馏文章的全文"""
    key = _norm_key(url)
    item = await store.get(key)
    if not item:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "这篇还没有全文，试试书签工具。"}}
    actual_commit = item.get("current_commit")
    if version is not None:
        if _content_repo is None:
            return api_error("VERSION_STORAGE_UNAVAILABLE", "历史版本存储暂时不可用。", 503)
        try:
            row = await store.get_article_version(key, version)
            if row is None:
                return api_error("VERSION_NOT_FOUND", "文章版本不存在。", 404)
            stored = _content_repo.read_version(version)
        except content_versions.ContentVersionError:
            return api_error("VERSION_NOT_FOUND", "文章版本不存在。", 404)
        except Exception:
            return api_error("VERSION_STORAGE_UNAVAILABLE", "历史版本存储暂时不可用。", 503)
        if stored.meta.get("article_key") != key:
            return api_error("VERSION_NOT_FOUND", "文章版本不存在。", 404)
        return {"ok": True, "title": stored.meta["title"], "url": stored.meta["url"],
                "content": stored.content, "images": stored.meta.get("images") or [],
                "summary": stored.meta.get("summary") or "", "manifest": stored.manifest,
                "length": len(stored.content), "version": stored.commit}
    return {"ok": True, "title": item["title"], "url": item["url"],
            "content": item["content"], "images": item["images"],
            "length": len(item["content"]), "at": item["at"],
            "version": actual_commit}


# ---- 智能推荐：基于收藏画像搜索同主题公共内容（v1） ----
RECOMMEND_BATCH = 12


def _content_type_of(url: str) -> str:
    if "/answer/" in url:
        return "answer"
    if "/zvideo/" in url:
        return "zvideo"
    if "/pin/" in url:
        return "pin"
    if "/p/" in url:
        return "article"
    if "/question/" in url:
        return "question"
    return "content"


async def _build_recommend_pool(session, token) -> list:
    """画像 = 收藏里综合分最高的几篇标题；用标题去知乎搜索同主题内容，排除已收藏/已蒸馏。

    多个主题的结果交错合并，避免前几条全被同一个主题占据。
    """
    fav_payload = user_cache.get(user_key("collections:first", _user_key_id(session))) or {}
    fav_items = fav_payload.get("items") or []
    owned = {str(it.get("Url") or "").split("?")[0].split("#")[0] for it in fav_items}
    seeds = sorted(fav_items, key=lambda it: it.get("combined_score") or 0, reverse=True)[:3]
    seeds = [str(it.get("Title") or "").strip() for it in seeds if str(it.get("Title") or "").strip()]
    if not seeds:
        seeds = ["如何高效学习", "认知科学"]
    seen = set(owned) | await store.keys()
    groups: list = []
    for seed in seeds:
        try:
            found = await zhihu.search_zhihu(seed, count=8)
        except zhihu.ZhihuError:
            continue
        group = []
        for row in found:
            url = str(row.get("Url") or "")
            key = url.split("?")[0].split("#")[0]
            if not url or key in seen:
                continue
            seen.add(key)
            raw_title = str(row.get("Title") or "")
            group.append({
                "Title": re.sub(r"\s*[-—|]\s*知乎\s*$", "", raw_title).strip() or raw_title,
                "Url": url,
                "ContentText": (row.get("ContentText") or "")[:400],
                "AuthorName": row.get("AuthorName") or "",
                "AuthorAvatar": row.get("AuthorAvatar") or "",
                "AuthorBadge": row.get("AuthorBadge") or "",
                "AuthorSignature": row.get("AuthorSignature") or "",
                "ContentType": _content_type_of(url),
                "seed": seed,
            })
        if group:
            groups.append(group)
        await asyncio.sleep(0.15)  # 温和限速，避免触发风控
    pool: list = []
    for i in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if i < len(group):
                pool.append(group[i])
    return pool


@app.get("/api/recommend")
async def recommend(request: Request, batch: int = 0, force: int = 0):
    """智能推荐（v1）：基于你的收藏画像，从知乎搜索里挑同主题的公共内容。"""
    session = _current_session(request)
    token, login_error = _resolve_token(session)
    if login_error:
        return {"ok": False, "error": login_error}
    cache_key = user_key("recommend_pool", _user_key_id(session))
    pool = None if force else user_cache.get(cache_key)
    if pool is None:
        try:
            pool = await _build_recommend_pool(session, token)
        except Exception:
            pool = []
        if pool:
            user_cache.set(cache_key, pool)
    total = len(pool)
    if total == 0:
        return {"ok": True, "items": [], "total": 0, "batch": 0,
                "message": "暂时没能取到推荐内容，稍后再试。"}
    start = (batch * RECOMMEND_BATCH) % total
    items = pool[start:start + RECOMMEND_BATCH]
    if len(items) < RECOMMEND_BATCH and total > len(items):
        items += pool[:min(RECOMMEND_BATCH - len(items), total)]
    return {"ok": True, "items": items, "total": total, "batch": batch}


# ---- 内容搜索：空收藏夹用户的冷启动入口（企划书 F32） ----
SEARCH_COUNT = 15
SEARCH_QUERY_MAX = 60


async def _seen_keys(session) -> set:
    """要从搜索结果里排除的内容键：已保存全文（全局）+ 已收藏（登录且有缓存时）"""
    seen = set(await store.keys())
    if session:
        fav_payload = user_cache.get(user_key("collections:first", _user_key_id(session))) or {}
        for it in (fav_payload.get("items") or []):
            seen.add(str(it.get("Url") or "").split("?")[0].split("#")[0])
    return seen


@app.get("/api/search")
async def search_content(request: Request, q: str = "", force: int = 0):
    """按关键词搜索知乎公共内容。

    未登录也可用：搜索接口走 Access Secret，不依赖用户 OAuth。
    结果按 query 全站共享缓存（同一个词多人搜索只花一次额度）；指标随结果进缓存，
    排除逻辑（已收藏/已保存）因人而异，在缓存之后逐请求执行。
    """
    query = str(q or "").strip()[:SEARCH_QUERY_MAX]
    if not query:
        return {"ok": False, "error": {"code": "EMPTY_QUERY", "message": "请输入搜索关键词。"}}
    session = _current_session(request)
    cache_key = "search:" + query
    pool = None if force else content_cache.get(cache_key)
    if pool is None:
        try:
            rows = await zhihu.search_zhihu(query, count=SEARCH_COUNT)
        except zhihu.ZhihuError as exc:
            return {"ok": False, "error": {"code": str(exc.code), "message": exc.message}}
        pool = []
        for row in rows:
            url = str(row.get("Url") or "")
            if not url:
                continue
            raw_title = str(row.get("Title") or "")
            text = (row.get("ContentText") or "")[:400]
            pool.append({
                "Title": re.sub(r"\s*[-—|]\s*知乎\s*$", "", raw_title).strip() or raw_title,
                "Url": url,
                "ContentText": text,
                # 搜索行没有 Summary 字段，复制一份让「信息量」指标有东西可算
                "Summary": text,
                "AuthorName": row.get("AuthorName") or "",
                "AuthorAvatar": row.get("AuthorAvatar") or "",
                "AuthorBadge": row.get("AuthorBadge") or "",
                "AuthorBadgeText": row.get("AuthorBadgeText") or "",
                "AuthorityLevel": row.get("AuthorityLevel"),
                "ContentType": _content_type_of(url),
            })
        pool = _enrich_with_shared_metrics(pool)
        content_cache.set(cache_key, pool)
    seen = await _seen_keys(session)
    items = [row for row in pool
             if str(row.get("Url") or "").split("?")[0].split("#")[0] not in seen]
    return {"ok": True, "query": query, "total": len(items), "items": items,
            "message": "" if items else "没有搜到合适的内容，换个关键词试试。"}


# ---- 学习会话（逐段精读，设计见 docs/学习会话设计-定稿.md） ----
async def _reading_context(request: Request, url: str, version: str | None = None):
    """会话公共前置：登录校验 + 归一化 key + 取全文。返回 (item, key, uid, error)"""
    uid, account_error = require_account(request)
    if account_error:
        return None, None, None, account_error
    key = _norm_key(url)
    item = await store.get(key)
    if not item:
        return None, key, None, {"code": "NOT_DISTILLED", "message": "这篇还没有全文，先用书签保存。"}
    requested = version or item.get("current_commit")
    if requested is not None:
        requested = str(requested)
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", requested):
            return None, key, None, {"code": "VERSION_NOT_FOUND", "message": "文章版本不存在。"}
        try:
            version_row = await store.get_article_version(key, requested)
        except Exception:
            return None, key, None, {"code": "DEPENDENCY_UNAVAILABLE", "message": "暂时无法读取文章版本。"}
        if version_row is None:
            return None, key, None, {"code": "VERSION_NOT_FOUND", "message": "文章版本不存在。"}
        if requested != item.get("current_commit"):
            if _content_repo is None:
                return None, key, None, {"code": "VERSION_STORAGE_UNAVAILABLE", "message": "历史版本存储暂时不可用。"}
            try:
                stored = _content_repo.read_version(requested)
            except Exception:
                return None, key, None, {"code": "VERSION_STORAGE_UNAVAILABLE", "message": "历史版本存储暂时不可用。"}
            if stored.meta.get("article_key") != key:
                return None, key, None, {"code": "VERSION_NOT_FOUND", "message": "文章版本不存在。"}
            item = {"key": key, "title": stored.meta["title"], "url": stored.meta["url"],
                    "content": stored.content, "images": stored.meta.get("images") or [],
                    "at": version_row.get("published_at") or 0,
                    "current_commit": requested}
        else:
            item = dict(item)
            item["current_commit"] = requested
    return item, key, uid, None


async def _segment_payload(item: dict, key: str, summary: str, cuts: list,
                           index: int, uid: str, segment_id: str | None = None):
    seg_text, total, seg_images = segments.segment_slice(
        item["content"], cuts, index, item.get("images"))
    if seg_text is None:
        return None, total
    nodes = await reading_store.list_segment_nodes(
        key, index, uid, segment_id=segment_id
    )
    nodes = [{**node,
              "id": str(node["id"]) if node.get("id") is not None else None,
              "parent_id": (str(node["parent_id"])
                            if node.get("parent_id") is not None else None),
              "supersedes_id": (str(node["supersedes_id"])
                                if node.get("supersedes_id") is not None else None)}
             for node in nodes]
    init_node = next((node for node in nodes if node.get("kind") == "init"), None)
    return {
        "index": index,
        "segment_id": segment_id,
        "total": total,
        "text": seg_text,
        "explain": (init_node or {}).get("content", ""),
        "explain_id": (init_node or {}).get("id"),
        "nodes": nodes,
        "images": seg_images,
    }, total


def _author_from_collections(uid: str, key: str) -> dict | None:
    """从收藏夹缓存里按 URL 精确取作者（收藏数据带 Author.Name，取不到返回 None）"""
    if not uid:
        return None
    coll = user_cache.get(user_key("collections:first", uid))
    for it in ((coll or {}).get("items") or []):
        if _norm_key(str(it.get("Url") or "")) == key:
            a = it.get("Author") or {}
            name = a.get("Name") or ""
            if name:
                return {"name": name, "url_token": a.get("UrlToken") or "",
                        "url": a.get("Url") or ""}
            return None
    return None


@app.post("/api/reading/open")
async def reading_open(request: Request):
    """打开固定文章版本；读取本身不生成解释。"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    item, key, uid, error = await _reading_context(
        request, str(body.get("url") or ""), body.get("version")
    )
    if error:
        return {"ok": False, "error": error}
    content = item["content"]
    commit = item.get("current_commit")
    plan = await reading_store.get_article(key, commit)
    if not plan:
        if commit:
            return api_error(
                "VERSION_DATA_UNAVAILABLE",
                "这个版本的段落数据不可用，请等待导入任务完成。",
                503,
            )
        try:
            result = await ai.segment_article(item["title"], content)
        except ai.AIError as exc:
            return {"ok": False, "error": {"code": "AI_FAILED", "message": str(exc)}}
        cuts = ai.normalize_cuts(result.get("cuts"), len(content))
        summary = result.get("summary") or ""
        await reading_store.save_article(key, summary, cuts, uid)
        plan = {"summary": summary, "cuts": cuts}
    cuts = plan.get("cuts") or []
    summary = plan.get("summary") or ""
    rows = plan.get("segments") or []
    if isinstance(rows, dict):
        rows = rows.get("segments") or []
    requested_segment_id = body.get("segment_id")
    if requested_segment_id:
        matches = [i for i, row in enumerate(rows) if row.get("segment_id") == requested_segment_id]
        if not matches:
            return api_error("SEGMENT_NOT_FOUND", "这个版本中没有该段落。", 404)
        start_index = matches[0]
    else:
        raw_start = body.get("start_seg", 0)
        start_index = raw_start if isinstance(raw_start, int) and not isinstance(raw_start, bool) else 0
    segment_id = rows[start_index].get("segment_id") if 0 <= start_index < len(rows) else None
    payload, total = await _segment_payload(
        item, key, summary, cuts, start_index, uid, segment_id
    )
    if payload is None:
        return api_error("SEGMENT_NOT_FOUND", "这个版本中没有该段落。", 404)
    return {"ok": True,
            "article": {"key": key, "title": item["title"], "summary": summary, "total": total,
                        "author": _author_from_collections(uid, key),
                        "images": item.get("images") or [], "version": commit},
            "segment": payload}


@app.get("/api/reading/segment")
async def reading_segment(request: Request, url: str, version: str | None = None,
                          segment_id: str | None = None, seg: int = 0):
    """纯读取固定版本的一段及本人已有解释。"""
    item, key, uid, error = await _reading_context(request, url, version)
    if error:
        return {"ok": False, "error": error}
    commit = item.get("current_commit")
    plan = await reading_store.get_article(key, commit)
    if not plan:
        return {"ok": False, "error": {"code": "NOT_OPENED", "message": "这个阅读会话还没开始。"}}
    cuts = plan.get("cuts") or []
    summary = plan.get("summary") or ""
    rows = plan.get("segments") or []
    if isinstance(rows, dict):
        rows = rows.get("segments") or []
    if segment_id:
        matches = [i for i, row in enumerate(rows) if row.get("segment_id") == segment_id]
        if not matches:
            return api_error("SEGMENT_NOT_FOUND", "这个版本中没有该段落。", 404)
        seg = matches[0]
    actual_segment_id = rows[seg].get("segment_id") if 0 <= seg < len(rows) else None
    payload, total = await _segment_payload(
        item, key, summary, cuts, seg, uid, actual_segment_id
    )
    if payload is None:
        return {"ok": False, "error": {"code": "BAD_SEG", "message": "段落不存在。"}}
    return {"ok": True,
            "article": {"key": key, "title": item["title"], "summary": summary,
                        "total": total, "version": commit},
            "segment": payload}


@app.get("/api/reading/versions")
async def reading_versions(request: Request, url: str, before: str | None = None,
                           limit: int = 20):
    _uid, account_error = require_account(request)
    if account_error:
        return api_error(account_error["code"], account_error["message"], 401)
    key = _norm_key(url)
    current = await store.get(key)
    if current is None:
        return api_error("ARTICLE_NOT_FOUND", "这篇文章还没有保存。", 404)
    try:
        rows = await store.list_article_versions(key, limit=limit, before=before)
    except Exception:
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法读取版本列表。", 503)
    return {"ok": True, "items": [{
        "commit": row.get("git_commit"),
        "parent_commit": row.get("parent_commit"),
        "published_at": row.get("published_at"),
        "current": row.get("git_commit") == current.get("current_commit"),
    } for row in rows]}


@app.get("/api/reading/history")
async def reading_history(request: Request):
    """学习记录：每篇文章最后读到的段落位置（来自 open 埋点）"""
    uid, account_error = require_account(request)
    if account_error:
        return api_error(account_error["code"], account_error["message"], 401)
    try:
        rows = await reading_store.list_recent_opened(uid)
    except Exception as exc:
        print(f"[reading] 读取个人学习记录失败：{exc}")
        return {"ok": False, "error": {"code": "DB_FAILED", "message": "读取学习记录失败，请稍后再试。"}}
    items = []
    for row in rows[:30]:
        art = await store.get(row["article_key"])
        if not art:
            continue
        plan = await reading_store.get_article(row["article_key"], row.get("git_commit"))
        total = len((plan or {}).get("cuts") or []) + 1
        items.append({"url": row["article_key"], "title": art.get("title") or row["article_key"],
                      "seg": row["seg_index"], "segment_id": row.get("segment_id"),
                      "version": row.get("git_commit"), "total": total, "at": row["at"]})
    return {"ok": True, "items": items}


@app.delete("/api/reading/history")
async def reading_history_delete(request: Request, url: str):
    """仅清除当前用户的阅读和自测进度，保留原文、解释与账单。"""
    uid, account_error = require_account(request)
    if account_error:
        return api_error(account_error["code"], account_error["message"], 401)
    key = _norm_key(url)
    if not key:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "缺少文章地址。"}}
    try:
        await reading_store.delete_user_events(key, uid)
        await reading_store.delete_quiz_attempts(key, uid)
    except Exception as exc:
        print(f"[reading] 删除个人学习记录失败：{exc}")
        return {"ok": False, "error": {"code": "DB_FAILED", "message": "删除失败，请稍后再试。"}}
    return {"ok": True, "history_removed": True}


def _points(microcredits) -> int:
    try:
        value = int(microcredits or 0)
    except (TypeError, ValueError):
        value = 0
    return value // billing.MICROCREDITS_PER_CREDIT


def _public_operation(row: dict) -> dict:
    """Only expose the fields a user needs to resume one paid generation."""
    result = row.get("result_json") or {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = {}
    return {
        "operation_id": str(row.get("id") or ""),
        "action": row.get("action"),
        "status": row.get("status"),
        "quoted_points": _points(row.get("quoted_microcredits")),
        "reserved_tokens": int(row.get("reserved_total_tokens") or
                               _points(row.get("reserved_microcredits"))),
        "usage_tokens": int(row.get("usage_tokens") or
                            ((result.get("usage") or {}).get("total_tokens")
                             if isinstance(result, dict) else 0) or 0),
        "daily_consumed_tokens": int(row.get("charged_daily_tokens") or 0),
        "wallet_charged_points": _points(row.get("charged_wallet_microcredits")),
        "expires_at": row.get("quote_expires_at"),
        "result_node_id": (str(row.get("result_node_id") or
                               (result.get("node_id") if isinstance(result, dict) else ""))
                           or None),
        "error_code": row.get("error_code"),
    }


def _prepared_input_token_upper_bound(prepared: dict) -> int:
    """Conservative prompt bound; settlement uses provider total_tokens.

    Only messages contribute input tokens. Sampling controls such as temperature are
    excluded from the immutable byte count, while protocol headroom covers role and
    message framing.
    """
    messages = prepared.get("messages")
    if not isinstance(messages, list) or not messages:
        raise billing.BillingError("INVALID_INPUT", "模型消息不能为空")
    return len(billing.canonical_json_bytes({"messages": messages})) + 256


async def _quote_context(request: Request, body: dict):
    item, key, uid, error = await _reading_context(
        request, str(body.get("url") or ""), body.get("version")
    )
    if error:
        return None, api_error(error["code"], error["message"], 401 if error["code"] in {
            "LOGIN_REQUIRED", "SESSION_EXPIRED", "ACCOUNT_ID_REQUIRED"
        } else 404)
    commit = item.get("current_commit")
    if not commit:
        return None, api_error("VERSION_REQUIRED", "这篇文章尚未建立版本。", 503)
    plan = await reading_store.get_article(key, commit)
    if not plan:
        return None, api_error("VERSION_DATA_UNAVAILABLE", "这个版本的段落数据不可用。", 503)
    rows = plan.get("segments") or []
    if isinstance(rows, dict):
        rows = rows.get("segments") or []
    segment_id = str(body.get("segment_id") or "")
    indices = [i for i, row in enumerate(rows) if row.get("segment_id") == segment_id]
    if not indices:
        return None, api_error("SEGMENT_NOT_FOUND", "这个版本中没有该段落。", 404)
    seg_index = indices[0]
    seg_text, _total, _start = segments.split_at_cuts(
        item["content"], plan.get("cuts") or [], seg_index
    )
    if seg_text is None:
        return None, api_error("SEGMENT_NOT_FOUND", "这个版本中没有该段落。", 404)
    return {
        "item": item, "key": key, "uid": uid, "commit": commit,
        "plan": plan, "segment_id": segment_id, "seg_index": seg_index,
        "segment_text": seg_text,
    }, None


@app.get("/api/billing/account")
async def billing_account(request: Request):
    uid, error = require_account(request)
    if error:
        return api_error(error["code"], error["message"], 401)
    try:
        row = await billing.account(uid)
    except Exception:
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法读取积分账户。", 503)
    return {"ok": True, "account": {
        "membership_tier": row.get("membership_tier") or "standard",
        "membership_expires_at": row.get("membership_expires_at"),
        "quota_period": row.get("quota_period") or billing.quota_period_key(
            datetime.now(timezone.utc)
        ),
        "daily_limit_tokens": int(row.get("daily_limit_tokens") or
            billing.daily_token_limit(row.get("membership_tier") or "standard")),
        "daily_used_tokens": int(row.get("daily_used_tokens") or 0),
        "daily_reserved_tokens": int(row.get("daily_reserved_tokens") or 0),
        "daily_available_tokens": int(row.get("daily_available_tokens") or 0),
        "balance_points": _points(row.get("balance_microcredits")),
        "ai_reserved_points": _points(row.get("ai_reserved_microcredits")),
        "refund_reserved_points": _points(row.get("refund_reserved_microcredits")),
        "wallet_available_points": _points(row.get("available_microcredits")),
        "billing_rule": "1 Token = 1 积分",
        "daily_reset": "Asia/Shanghai 04:00",
    }}


@app.get("/api/billing/ledger")
async def billing_ledger(request: Request, limit: int = 20,
                         before_id: int | None = None):
    uid, error = require_account(request)
    if error:
        return api_error(error["code"], error["message"], 401)
    try:
        rows = await billing.ledger(uid, limit=limit, before_id=before_id)
    except Exception:
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法读取积分明细。", 503)
    items = [{
        "id": str(row.get("id")), "kind": row.get("kind"),
        "points_delta": _points(row.get("balance_delta")),
        "points_after": _points(row.get("balance_after")),
        "created_at": row.get("created_at"),
        "operation_id": row.get("ai_operation_id"),
        "payment_order_id": row.get("payment_order_id"),
        "refund_id": row.get("refund_id"),
    } for row in rows]
    return {"ok": True, "items": items,
            "next_before_id": items[-1]["id"] if len(items) == max(1, min(limit, 100)) else None}


@app.get("/api/billing/plans")
async def billing_plans(request: Request):
    _uid, error = require_account(request)
    if error:
        return api_error(error["code"], error["message"], 401)
    return {"ok": True,
            "membership": {"available": settings.RECHARGE_ENABLED,
                           "tier": "premium", "duration_days": 30,
                           "monthly_price_fen": billing.PREMIUM_MONTHLY_PRICE_FEN,
                           "daily_token_limit": billing.PREMIUM_DAILY_TOKENS}}


@app.post("/api/billing/membership")
async def billing_membership(request: Request):
    uid, error = require_account(request)
    if error:
        return api_error(error["code"], error["message"], 401)
    if not settings.RECHARGE_ENABLED:
        return api_error(
            "MEMBERSHIP_PURCHASE_UNAVAILABLE", "会员开通尚未启用。", 503
        )
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是对象")
        idempotency_key = _canonical_uuid(
            body.get("idempotency_key"), "idempotency_key"
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return api_error("INVALID_INPUT", str(exc) or "会员开通请求无效。", 422)

    request_hash = billing.stable_json_hash({
        "product": "premium_30_days",
        "amount_fen": billing.PREMIUM_MONTHLY_PRICE_FEN,
        "duration_days": 30,
    })
    try:
        row = await billing.apply_demo_membership(
            uid=uid, entitlement_id=str(uuid4()),
            idempotency_key=idempotency_key,
            request_hash_value=request_hash,
            amount_fen=billing.PREMIUM_MONTHLY_PRICE_FEN,
        )
    except Exception as exc:
        print(f"[billing] 演示会员开通失败：{exc}")
        return api_error("DEPENDENCY_UNAVAILABLE", "会员开通暂时失败，请重试。", 503)
    if row.get("error_code") == "IDEMPOTENCY_CONFLICT":
        return api_error("IDEMPOTENCY_CONFLICT", "同一会员请求的内容不一致。", 409)
    return {"ok": True, "membership_id": str(row.get("id") or ""),
            "activated": row.get("activated") is True, "tier": "premium",
            "amount_fen": billing.PREMIUM_MONTHLY_PRICE_FEN,
            "duration_days": 30,
            "membership_expires_at": row.get("membership_expires_at")}


async def _require_advanced_member(uid: str):
    try:
        account = await billing.account(uid)
        expires = datetime.fromisoformat(str(account.get("membership_expires_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return api_error("MEMBERSHIP_REQUIRED", "高级解释为会员功能，请联系管理员开通。", 403)
    except Exception:
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法核验会员状态，请稍后重试。", 503)
    if (account.get("membership_tier") != "premium" or expires.tzinfo is None
            or expires <= datetime.now(timezone.utc)):
        return api_error("MEMBERSHIP_REQUIRED", "高级解释为会员功能，请联系管理员开通。", 403)
    return None


@app.post("/api/reading/quote")
async def reading_quote(request: Request):
    if not settings.BILLING_ENABLED:
        return api_error("BILLING_UNAVAILABLE", "积分计费尚未启用。", 503)
    try:
        body = await request.json()
    except Exception:
        return api_error("BAD_REQUEST", "请求体不是合法 JSON。", 400)
    ctx, error_response = await _quote_context(request, body)
    if error_response:
        return error_response
    action = str(body.get("action") or "")
    if action == "advanced":
        member_error = await _require_advanced_member(ctx["uid"])
        if member_error is not None:
            return member_error
    parent_id = body.get("parent_id")
    replace_node_id = body.get("replace_node_id")
    try:
        parent_id = int(parent_id) if parent_id is not None else None
        replace_node_id = int(replace_node_id) if replace_node_id is not None else None
        pos_start = body.get("pos_start")
        pos_end = body.get("pos_end")
        if pos_start is not None:
            pos_start = int(pos_start)
        if pos_end is not None:
            pos_end = int(pos_end)
        question = str(body.get("question") or "") if action in {"ask", "advanced"} else None
        intent = billing.AIRequestIntent(
            action, ctx["key"], ctx["commit"], ctx["segment_id"],
            parent_id=parent_id, replace_node_id=replace_node_id,
            pos_start=pos_start, pos_end=pos_end, question=question,
        )
    except (TypeError, ValueError, billing.BillingError) as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        return api_error(code, str(exc), 422)
    scope_text = ctx["segment_text"]
    anchor_text = ""
    if parent_id is not None:
        parent = await reading_store.get_private_node(
            parent_id, ctx["uid"], ctx["key"], ctx["segment_id"]
        )
        if not parent:
            return api_error("PARENT_NOT_FOUND", "上一级解释不存在。", 404)
        scope_text = str(parent.get("content") or "")
        anchor_text = str(parent.get("question") or parent.get("content") or "")
    if action == "init":
        prepared = ai.prepare_explain_segment(
            ctx["item"]["title"], ctx["plan"].get("summary") or "", ctx["segment_text"]
        )
    elif action == "explain":
        if not (0 <= pos_start < pos_end <= len(scope_text)):
            return api_error("INVALID_OFFSET", "选区范围不合法。", 422)
        overlap = await reading_store.find_overlap(
            ctx["key"], ctx["seg_index"], pos_start, pos_end, ctx["uid"], parent_id
        )
        if overlap and replace_node_id is None:
            return api_error("OVERLAP", "这段文字已经有解释了。", 409)
        prepared = ai.prepare_explain_selection(
            ctx["item"]["title"], ctx["plan"].get("summary") or "",
            ctx["segment_text"], scope_text[pos_start:pos_end],
        )
    elif action == "advanced":
        if pos_start is not None or pos_end is not None:
            if not (isinstance(pos_start, int) and isinstance(pos_end, int)
                    and 0 <= pos_start < pos_end <= len(scope_text)):
                return api_error("INVALID_OFFSET", "选区范围不合法。", 422)
            anchor_text = scope_text[pos_start:pos_end]
        try:
            prepared = advanced.prepare(ctx["item"]["title"], ctx["item"]["content"],
                                        ctx["segment_text"], anchor_text, question)
        except ai.AIError as exc:
            return api_error("INVALID_INPUT", str(exc), 422)
    elif action == "ask":
        if pos_start is not None or pos_end is not None:
            if not (0 <= pos_start < pos_end <= len(scope_text)):
                return api_error("INVALID_OFFSET", "选区范围不合法。", 422)
            anchor_text = scope_text[pos_start:pos_end]
        prepared = ai.prepare_answer_question(
            question, ctx["plan"].get("summary") or "", anchor_text,
            ctx["segment_text"],
        )
    else:
        return api_error("INVALID_INPUT", "未知生成类型。", 422)
    try:
        idem = _canonical_uuid(body.get("idempotency_key"), "idempotency_key")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=billing.QUOTE_TTL_SECONDS)
        budget_end = expires + timedelta(
            seconds=billing.MAX_QUEUE_SECONDS + billing.DISPATCH_DEADLINE_SECONDS
        )
        policy = billing.TokenBillingPolicy(
            requested_model=prepared["model"],
            allowed_returned_models=(prepared["model"],),
            tokenizer_revision="utf8-byte-upper-bound-v1",
            prompt_template_revision=("advanced-" + prepared["plugin_revision"]
                                      if action == "advanced" else "reading-prompts-v1"),
            output_limit=int(prepared["max_tokens"]),
        )
        quote = billing.quote_budget(
            policy, input_token_upper_bound=(advanced.INPUT_BUDGET if action == "advanced"
                                             else _prepared_input_token_upper_bound(prepared)),
            quoted_at=now, quote_expires_at=expires, budget_valid_until=budget_end,
        )
        operation_id = str(uuid4())
        row = await billing.create_quote_record(
            uid=ctx["uid"], operation_id=operation_id, idempotency_key=idem,
            request_hash_value=(billing.stable_json_hash({"intent": intent.fingerprint_payload(),
                                "prepared": prepared}) if action == "advanced" else intent.request_hash()), intent=intent,
            prepared_request=prepared, policy=policy, quote=quote,
            quoted_at=now, quote_expires_at=expires,
        )
    except billing.BillingError as exc:
        status = 409 if exc.code == "IDEMPOTENCY_CONFLICT" else 422
        return api_error(exc.code, exc.message, status)
    except Exception as exc:
        print(f"[billing] 创建报价失败：{exc}")
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法创建积分报价。", 503)
    return {"ok": True, **_public_operation(row or {
        "id": operation_id, "action": action, "status": "quoted",
        "quoted_microcredits": quote.quoted_microcredits,
        "quote_expires_at": expires.isoformat(),
    }), "billing_rule": "1 Token = 1 积分"}


async def _submit_billed_operation(request: Request, expected_action: str):
    if not settings.BILLING_ENABLED:
        return api_error("BILLING_UNAVAILABLE", "积分计费尚未启用。", 503)
    uid, error = require_account(request)
    if error:
        return api_error(error["code"], error["message"], 401)
    try:
        body = await request.json()
        operation_id = _canonical_uuid(body.get("operation_id"), "operation_id")
        existing = await billing.get_operation(uid, operation_id)
        if not existing:
            return api_error("OPERATION_NOT_FOUND", "生成任务不存在。", 404)
        if existing.get("action") != expected_action:
            return api_error("OPERATION_MISMATCH", "生成任务类型不匹配。", 409)
        if expected_action == "advanced":
            member_error = await _require_advanced_member(uid)
            if member_error is not None:
                return member_error
        row = await billing.reserve_operation(uid, operation_id)
    except (ValueError, billing.BillingError) as exc:
        return api_error(getattr(exc, "code", "INVALID_INPUT"), str(exc), 422)
    except Exception as exc:
        print(f"[billing] 提交生成失败：{exc}")
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法提交生成任务。", 503)
    return JSONResponse({"ok": True, **_public_operation(row)}, status_code=202)


@app.post("/api/reading/init")
async def reading_init(request: Request):
    return await _submit_billed_operation(request, "init")


@app.post("/api/reading/selection")
async def reading_selection(request: Request):
    """Execute an accepted selection quote; targets are immutable in the operation."""
    return await _submit_billed_operation(request, "explain")


@app.post("/api/reading/ask")
async def reading_ask(request: Request):
    """Execute an accepted question quote; the question cannot change after quote."""
    return await _submit_billed_operation(request, "ask")


@app.post("/api/reading/advanced")
async def reading_advanced(request: Request):
    """Member explanation, using the same immutable quote and settlement lifecycle."""
    return await _submit_billed_operation(request, "advanced")


@app.get("/api/reading/operations/{operation_id}")
async def reading_operation(request: Request, operation_id: str):
    uid, error = require_account(request)
    if error:
        return api_error(error["code"], error["message"], 401)
    try:
        operation_id = _canonical_uuid(operation_id, "operation_id")
        row = await billing.get_operation(uid, operation_id)
    except ValueError as exc:
        return api_error("INVALID_INPUT", str(exc), 422)
    except Exception:
        return api_error("DEPENDENCY_UNAVAILABLE", "暂时无法读取生成任务。", 503)
    if not row:
        return api_error("OPERATION_NOT_FOUND", "生成任务不存在。", 404)
    return {"ok": True, **_public_operation(row)}


@app.post("/api/reading/image")
async def reading_image(request: Request):
    """解释原文配图：按图片 URL 全局缓存（同一张图只生成一次，省额度）"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    item, key, uid, error = await _reading_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    image = zhihu.clean_image_url(body.get("image"))
    if not image:
        return {"ok": False, "error": {"code": "BAD_IMAGE", "message": "配图地址不合法。"}}
    # 只解释这篇文章自己的配图：否则接口会变成任意图片的抓取+解释入口
    if image not in segments.image_urls(item.get("images")):
        return {"ok": False, "error": {"code": "BAD_IMAGE", "message": "这张图不属于这篇文章。"}}
    try:
        cached = await reading_store.get_image_explanation(image)
    except Exception as exc:
        print(f"[reading] 读取配图解释失败：{exc}")
        return {"ok": False, "error": {
            "code": "DB_FAILED",
            "message": "配图解释的存储不可用（需要在数据库建 image_explanations 表，见设计文档 7.2）。"}}
    if cached:
        return {"ok": True, "image": image, "explain": cached, "cached": True}
    # 上下文由服务端按抓取时记录的位置取：图片就插在这两段之间
    before, after = segments.image_context(item.get("content") or "",
                                   segments.image_pos(item.get("images"), image))
    try:
        data_url = await ai.fetch_image_data_url(image)
        explain = await ai.explain_image(item["title"], item.get("summary") or "",
                                         before, after, data_url)
    except ai.AIError as exc:
        return {"ok": False, "error": {"code": "AI_FAILED", "message": str(exc)}}
    try:
        await reading_store.save_image_explanation(image, explain)
    except Exception as exc:
        print(f"[reading] 保存配图解释失败：{exc}")   # 存不下也要把结果给用户
    return {"ok": True, "image": image, "explain": explain, "cached": False}


@app.post("/api/reading/event")
async def reading_event(request: Request):
    """埋点：open / understood / deleted / finished（只追加，不改变任何呈现）"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    event = str(body.get("event") or "")
    if event not in ("open", "understood", "deleted", "finished", "reviewed", "quiz_done"):
        return {"ok": False, "error": {"code": "BAD_EVENT", "message": "未知事件。"}}
    item, key, uid, error = await _reading_context(
        request, str(body.get("url") or ""), body.get("version")
    )
    if error:
        return {"ok": False, "error": error}
    seg_index = body.get("seg_index")
    segment_id = body.get("segment_id")
    commit = item.get("current_commit")
    plan = await reading_store.get_article(key, commit)
    rows = (plan or {}).get("segments") or []
    if isinstance(rows, dict):
        rows = rows.get("segments") or []
    if commit and (not isinstance(segment_id, str)
                   or segment_id not in {row.get("segment_id") for row in rows}):
        return api_error("SEGMENT_NOT_FOUND", "这个版本中没有该段落。", 404)
    node_id = body.get("node_id")
    if isinstance(node_id, str) and node_id.isdecimal():
        node_id = int(node_id)
    await reading_store.append_event(
        key, uid, event,
        int(seg_index) if isinstance(seg_index, int) else None,
        node_id if isinstance(node_id, int) and not isinstance(node_id, bool) else None,
        git_commit=commit, segment_id=segment_id,
    )
    return {"ok": True}


# ---- 自测（读完之后的检测环节；题目按内容键全站共享） ----

QUIZ_COUNT = 5          # 每篇出的题数
QUIZ_MAX_CHARS = 20000  # 出题时给模型的原文上限（超长文截断，避免超出上下文）


def _quiz_public(questions: list, attempts: dict) -> list:
    """给前端的题目：不带答案与解析（判定留在后端，前端拿不到正确答案）"""
    out = []
    for i, q in enumerate(questions or []):
        if not isinstance(q, dict):
            continue
        item = {"index": i, "kind": q.get("kind"), "focus": q.get("focus"),
                "stem": q.get("stem") or ""}
        if q.get("kind") == "choice":
            item["options"] = q.get("options") or []
        done = attempts.get(i)
        if done:
            item["done"] = True
            item["correct"] = bool(done.get("correct"))
        out.append(item)
    return out


async def _quiz_context(request: Request, url: str):
    """自测公共前置：登录 + 全文 + 题目（无则生成，按内容键全站复用）"""
    item, key, uid, error = await _reading_context(request, url)
    if error:
        return None, None, None, None, error
    plan = await reading_store.get_article(key)
    if not plan:
        return None, None, None, None, {
            "code": "NOT_OPENED", "message": "这个阅读会话还没开始。"}
    # 题目表未建时（见部署指南），自测不可用但绝不能连累精读与提问
    try:
        row = await reading_store.get_quiz(key)
    except Exception as exc:
        print(f"[quiz] 读取题目失败：{exc}")
        return None, None, None, None, {
            "code": "DB_FAILED",
            "message": "自测暂时不可用（需要在数据库建 reading_quizzes 表，见部署指南）。"}
    questions = reading_store.load_questions(row)
    if not questions:
        content = (item.get("content") or "")[:QUIZ_MAX_CHARS]
        try:
            result = await ai.quiz_questions(item["title"], plan.get("summary") or "",
                                             content, QUIZ_COUNT)
            questions = result.get("questions") or []
            summary = await ai.quiz_summary(item["title"], plan.get("summary") or "", content)
        except ai.AIError as exc:
            return None, None, None, None, {"code": "AI_FAILED", "message": str(exc)}
        if not questions:
            return None, None, None, None, {
                "code": "AI_FAILED", "message": "这次没能生成有效的题目，请稍后再试。"}
        try:
            await reading_store.save_quiz(key, summary, questions, uid)
        except Exception as exc:
            print(f"[quiz] 保存题目失败：{exc}")   # 存不下也要把题给用户
        row = {"summary": summary}
    return item, key, uid, {"plan": plan, "row": row, "questions": questions}, None


@app.get("/api/reading/quiz")
async def reading_quiz(request: Request, url: str):
    """自测：取题目（首次访问现场生成，约 10~30 秒）与我的作答进度"""
    item, key, uid, ctx, error = await _quiz_context(request, url)
    if error:
        return {"ok": False, "error": error}
    try:
        attempts = reading_store.collapse_attempts(
            await reading_store.list_quiz_attempts(key, uid))
    except Exception:
        attempts = {}      # 进度读不出来不影响做题
    row = ctx["row"] or {}
    return {"ok": True,
            "article": {"key": key, "title": item["title"], "total":
                        len((ctx["plan"].get("cuts") or [])) + 1},
            "summary": row.get("summary") or "",
            "questions": _quiz_public(ctx["questions"], attempts),
            "answered": sum(1 for i in attempts if i < len(ctx["questions"]))}


@app.post("/api/reading/quiz/answer")
async def reading_quiz_answer(request: Request):
    """自测作答：判定在后端；答错额外让模型针对这个选项讲一次"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    item, key, uid, ctx, error = await _quiz_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    questions = ctx["questions"]
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "题号不合法。"}}
    if not 0 <= index < len(questions):
        return {"ok": False, "error": {"code": "BAD_INDEX", "message": "这道题不存在。"}}
    q = questions[index]
    chosen_raw = body.get("answer")
    right_index, right_bool = None, None
    if q.get("kind") == "judgment":
        if not isinstance(chosen_raw, bool):
            return {"ok": False, "error": {"code": "BAD_ANSWER", "message": "请选择对或错。"}}
        right_bool = bool(q.get("answer"))
        correct = (chosen_raw == right_bool)
        chosen, correct_text = ("对" if chosen_raw else "错"), ("对" if right_bool else "错")
    else:
        try:
            pick = int(chosen_raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": {"code": "BAD_ANSWER", "message": "请选一个选项。"}}
        options = q.get("options") or []
        if isinstance(chosen_raw, bool) or not 0 <= pick < len(options):
            return {"ok": False, "error": {"code": "BAD_ANSWER", "message": "选项不存在。"}}
        answer = q.get("answer")
        if not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer < len(options):
            return {"ok": False, "error": {"code": "BAD_QUESTION", "message": "这道题的答案有问题，请重做一遍。"}}
        right_index = answer
        correct = (pick == answer)
        chosen = options[pick]
        correct_text = options[answer]

    explain = ""
    if not correct:
        try:
            explain = await ai.quiz_explain(item["title"], ctx["plan"].get("summary") or "",
                                            q.get("stem") or "", chosen, correct_text,
                                            q.get("explanation") or "")
        except ai.AIError as exc:
            explain = ""       # 讲解失败不影响判定，前端退回出题时的解析
            print(f"[quiz] 生成讲解失败：{exc}")
    try:
        await reading_store.append_quiz_attempt(key, uid, index, correct, chosen)
    except Exception as exc:
        print(f"[quiz] 保存作答失败：{exc}")   # 进度存不下不影响本次作答
    return {"ok": True, "correct": correct, "chosen": chosen,
            "answer": correct_text,
            "right_index": right_index, "right_bool": right_bool,
            "explain": explain or (q.get("explanation") or "")}


@app.post("/api/reading/review")
async def reading_review(request: Request):
    """复习：一段概括性文字。

    mode=general  从岔路口进入——只据原贴内容（+ 我问过的问题），按内容键共享缓存
    mode=personal 从自测结束页进入——带上最近一轮答错与跳过的题（跳过视为不懂），
                  因人而异，不缓存

    两条路径的按钮文案都是「复习一下」，模式对用户不可见。
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    item, key, uid, error = await _reading_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    mode = "personal" if str(body.get("mode") or "") == "personal" else "general"
    plan = await reading_store.get_article(key)
    if not plan:
        return {"ok": False, "error": {"code": "NOT_OPENED", "message": "这个阅读会话还没开始。"}}
    summary = plan.get("summary") or ""
    content = (item.get("content") or "")[:QUIZ_MAX_CHARS]
    weak_points, questions = [], []

    if mode == "personal":
        # 自测数据读不出来就退化成通用版，不能因此让复习不可用
        try:
            row = await reading_store.get_quiz(key)
            quiz_questions = reading_store.load_questions(row)
            attempts = reading_store.collapse_attempts(
                await reading_store.list_quiz_attempts(key, uid))
            weak_points = ([] if not attempts        # 一题都没做过：谈不上薄弱点
                           else reading_store.collect_weak_points(quiz_questions, attempts))
        except Exception as exc:
            print(f"[review] 读取自测反馈失败，退回通用版：{exc}")
            mode = "general"
        if not weak_points:
            mode = "general"        # 全对（或没做过）时没有薄弱点可讲
    if mode == "general":
        # 我问过的问题：说明我卡在哪里（通用版也带上）
        try:
            questions = await reading_store.list_user_questions(key, uid)
        except Exception as exc:
            print(f"[review] 读取提问失败：{exc}")

    cache_key = ""
    if mode == "general":
        if questions:
            fingerprint = hashlib.sha256(json.dumps(
                questions, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            cache_key = f"review:{uid}:{key}:{fingerprint}"
        else:
            cache_key = "review:" + key + ":no-personal-input"
    if cache_key:
        cached = content_cache.get(cache_key)
        if cached:
            return {"ok": True, "mode": mode, "text": cached, "cached": True}
    try:
        text = await ai.review_summary(item["title"], summary, content,
                                       weak_points=weak_points, questions=questions)
    except ai.AIError as exc:
        return {"ok": False, "error": {"code": "AI_FAILED", "message": str(exc)}}
    if cache_key and text:
        content_cache.set(cache_key, text)
    return {"ok": True, "mode": mode, "text": text, "cached": False}


@app.delete("/api/reading/node/{node_id}")
async def reading_delete_node(request: Request, node_id: int, url: str = "",
                              version: str | None = None,
                              segment_id: str | None = None):
    """软删除本人的节点和parent后代，不沿修订链扩散。"""
    item, key, uid, error = await _reading_context(request, url, version)
    if error:
        return {"ok": False, "error": error}
    if not item.get("current_commit") or not segment_id:
        return api_error("INVALID_INPUT", "删除解释需要version和segment_id。", 422)
    node = await reading_store.get_private_node(
        node_id, uid, key, segment_id, include_history=True
    )
    if not node:
        return api_error("NODE_NOT_FOUND", "节点不存在。", 404)
    deleted = await reading_store.soft_delete_private_tree(node_id, uid, key, segment_id)
    await reading_store.append_event(
        key, uid, "deleted", node.get("seg_index"), node_id,
        git_commit=item.get("current_commit"), segment_id=segment_id,
    )
    return {"ok": True, "deleted": deleted}


# 静态文件（前端单页）——挂在最后，避免吞掉 /api 与 /auth 路由
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
