# -*- coding: utf-8 -*-
"""知识蒸馏站 —— FastAPI 应用入口与路由（对照官方 Node 模板的接口形态）"""
import asyncio
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.sessions import sessions
from core.cache import content_cache, user_cache, user_key
from core import ai, analyze, oauth, reading_store, segments, store, zhihu

app = FastAPI(title=settings.PROJECT_NAME, docs_url=None, redoc_url=None)

# 书签小工具从 zhihu.com 页面跨域 POST 内容进来，需要放行 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
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
        except oauth.ZhihuError:
            session.profile = None  # 资料获取失败不阻断登录
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
@app.on_event("startup")
async def _startup():
    try:
        await store.init()
    except Exception as exc:  # 配置缺失/网络不通时给出明确告警，但不阻塞其他功能
        print(f"[store] 持久化层初始化失败：{exc}")


@app.post("/api/ingest")
async def ingest(request: Request):
    """接收书签小工具从知乎页面送来的全文（用户在知乎页面主动确认后触发）"""
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
            url = zhihu.clean_image_url(raw_url)
            if not url or url in seen_images:
                continue
            pos = item.get("pos") if isinstance(item, dict) else None
            if isinstance(pos, bool) or not isinstance(pos, int) or not (0 <= pos <= len(content)):
                pos = None      # 位置不可信就当没有：宁可退回文章级展示，也不要锚错地方
            seen_images.add(url)
            clean_images.append({"url": url, "pos": pos})
    key = _norm_key(url)
    existing = await store.get(key)
    if existing is not None:
        # 内容不随重复蒸馏变化（学习会话的位置锚定依赖全文稳定）：已存在直接返回
        return {"ok": True, "existed": True, "length": len(existing["content"]),
                "images": len(existing.get("images") or []), "total": await store.count()}
    await store.upsert(key, title, url, content, clean_images)
    return {"ok": True, "length": len(content), "images": len(clean_images),
            "total": await store.count()}


@app.get("/api/distilled")
async def distilled_index():
    """已蒸馏内容索引（供列表打标：哪些收藏已有全文）"""
    return {"ok": True, "items": await store.index()}


@app.delete("/api/distilled")
async def delete_distilled(request: Request, url: str):
    """「更新文章」前置：清空某篇已保存的全文与学习数据（之后用书签重新保存即可覆盖）"""
    session = _current_session(request)
    _token, login_error = _resolve_token(session)
    if login_error:
        return {"ok": False, "error": login_error}
    key = _norm_key(url)
    item = await store.get(key)
    if not item:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "这篇还没有保存过全文。"}}
    try:
        await store.delete(key)
        # 旧段落划分与位置标记失去参照，必须一起清（否则重新保存后会锚到错的文字上）
        await reading_store.delete_plan_and_nodes(key)
        await reading_store.delete_user_events(key, _user_key_id(session))
    except Exception:
        return {"ok": False, "error": {"code": "DB_FAILED", "message": "清空失败，请稍后再试。"}}
    return {"ok": True}


@app.get("/api/distilled/content")
async def distilled_content(url: str):
    """读取某篇已蒸馏文章的全文"""
    key = _norm_key(url)
    item = await store.get(key)
    if not item:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "这篇还没有全文，试试书签工具。"}}
    return {"ok": True, "title": item["title"], "url": item["url"],
            "content": item["content"], "images": item["images"],
            "length": len(item["content"]), "at": item["at"]}


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


# ---- 学习会话（逐段精读，设计见 docs/学习会话设计-定稿.md） ----
async def _reading_context(request: Request, url: str):
    """会话公共前置：登录校验 + 归一化 key + 取全文。返回 (item, key, uid, error)"""
    session = _current_session(request)
    _token, login_error = _resolve_token(session)
    if login_error:
        return None, None, None, login_error
    key = _norm_key(url)
    item = await store.get(key)
    if not item:
        return None, key, None, {"code": "NOT_DISTILLED", "message": "这篇还没有全文，先用书签保存。"}
    return item, key, _user_key_id(session), None


async def _ensure_init_explain(item: dict, key: str, seg_index: int, seg_text: str,
                               summary: str, uid: str):
    """初始解释：有则取，无则生成（按段懒生成）"""
    node = await reading_store.get_init(key, seg_index)
    if node:
        return node
    try:
        text = await ai.explain_segment(item["title"], summary, seg_text)
    except ai.AIError:
        return None
    return await reading_store.create_node(key, seg_index, "init", text, uid)


async def _segment_payload(item: dict, key: str, summary: str, cuts: list,
                           index: int, uid: str):
    seg_text, total, seg_images = segments.segment_slice(
        item["content"], cuts, index, item.get("images"))
    if seg_text is None:
        return None, total
    init_node = await _ensure_init_explain(item, key, index, seg_text, summary, uid)
    nodes = await reading_store.list_segment_nodes(key, index, uid)
    return {
        "index": index,
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
    """打开阅读会话：无划分则生成划分与全文主旨，返回第 0 段"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    item, key, uid, error = await _reading_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    content = item["content"]
    plan = await reading_store.get_article(key)
    if not plan:
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
    payload, total = await _segment_payload(item, key, summary, cuts, 0, uid)
    await reading_store.append_event(key, uid, "open", 0, None)
    return {"ok": True,
            "article": {"key": key, "title": item["title"], "summary": summary, "total": total,
                        "author": _author_from_collections(uid, key),
                        "images": item.get("images") or []},
            "segment": payload}


@app.get("/api/reading/segment")
async def reading_segment(request: Request, url: str, seg: int = 0):
    """进入某一段：返回段文本、初始解释（可懒生成）与该段可见节点"""
    item, key, uid, error = await _reading_context(request, url)
    if error:
        return {"ok": False, "error": error}
    plan = await reading_store.get_article(key)
    if not plan:
        return {"ok": False, "error": {"code": "NOT_OPENED", "message": "这个阅读会话还没开始。"}}
    cuts = plan.get("cuts") or []
    summary = plan.get("summary") or ""
    payload, total = await _segment_payload(item, key, summary, cuts, seg, uid)
    if payload is None:
        return {"ok": False, "error": {"code": "BAD_SEG", "message": "段落不存在。"}}
    await reading_store.append_event(key, uid, "open", seg, None)
    return {"ok": True,
            "article": {"key": key, "title": item["title"], "summary": summary, "total": total},
            "segment": payload}


@app.get("/api/reading/history")
async def reading_history(request: Request):
    """学习记录：每篇文章最后读到的段落位置（来自 open 埋点）"""
    session = _current_session(request)
    _token, login_error = _resolve_token(session)
    if login_error:
        return {"ok": False, "error": login_error}
    uid = _user_key_id(session)
    try:
        rows = await reading_store.list_recent_opened(uid)
    except Exception:
        return {"ok": False, "error": {"code": "DB_FAILED", "message": "读取学习记录失败，请稍后再试。"}}
    items = []
    for row in rows[:30]:
        art = await store.get(row["article_key"])
        if not art:
            continue
        plan = await reading_store.get_article(row["article_key"])
        total = len((plan or {}).get("cuts") or []) + 1
        items.append({"url": row["article_key"], "title": art.get("title") or row["article_key"],
                      "seg": row["seg_index"], "total": total, "at": row["at"]})
    return {"ok": True, "items": items}


@app.delete("/api/reading/history")
async def reading_history_delete(request: Request, url: str):
    """删除一条学习记录，并把该文章重置为「未保存全文」的状态（可以重新用书签保存）

    ⚠️ 全文是按内容键全局共享的：重置会连带删掉这篇的分段/解释/位置标记，
    其他用户对这篇的学习进度与标记也会一起失效（设计取舍见 docs/学习会话设计-定稿.md 7.1）。
    """
    session = _current_session(request)
    _token, login_error = _resolve_token(session)
    if login_error:
        return {"ok": False, "error": login_error}
    key = _norm_key(url)
    if not key:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "缺少文章地址。"}}
    uid = _user_key_id(session)
    try:
        # 顺序：先清锚在全文上的分段与解释，再清本人的记录，最后删全文本身
        await reading_store.delete_plan_and_nodes(key)
        await reading_store.delete_user_events(key, uid)
        await store.delete(key)
    except Exception as exc:
        print(f"[reading] 重置文章失败：{exc}")
        return {"ok": False, "error": {"code": "DB_FAILED", "message": "删除失败，请稍后再试。"}}
    # 自测数据单独清：表未建时也不能让上面的删除（全文）失败
    try:
        await reading_store.delete_quiz(key)
        await reading_store.delete_quiz_attempts(key, uid)
    except Exception as exc:
        print(f"[reading] 清理自测数据失败（全文已删除）：{exc}")
    return {"ok": True}


@app.post("/api/reading/selection")
async def reading_selection(request: Request):
    """提交选区（纯选中）：生成共享解释，段内区间不重叠"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    item, key, uid, error = await _reading_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    try:
        seg = int(body.get("seg") or 0)
        pos_start = int(body.get("pos_start"))
        pos_end = int(body.get("pos_end"))
    except (TypeError, ValueError):
        return {"ok": False, "error": {"code": "BAD_RANGE", "message": "选区参数不合法。"}}
    plan = await reading_store.get_article(key)
    if not plan:
        return {"ok": False, "error": {"code": "NOT_OPENED", "message": "这个阅读会话还没开始。"}}
    seg_text, _total, _seg_start = segments.split_at_cuts(item["content"], plan.get("cuts") or [], seg)
    if seg_text is None:
        return {"ok": False, "error": {"code": "BAD_SEG", "message": "段落不存在。"}}
    parent_id = body.get("parent_id")
    parent_id = int(parent_id) if parent_id is not None else None
    # 层内选区：偏移始终相对父节点的内容文本（节点内容 = 该层的父文本）
    scope_text = seg_text
    if parent_id is not None:
        parent_node = await reading_store.get_node(parent_id)
        if parent_node is not None:
            scope_text = parent_node.get("content") or ""
    if not (0 <= pos_start < pos_end <= len(scope_text)):
        return {"ok": False, "error": {"code": "BAD_RANGE", "message": "选区范围不合法。"}}
    overlap = await reading_store.find_overlap(key, seg, pos_start, pos_end, parent_id)
    if overlap:
        return {"ok": False, "error": {"code": "OVERLAP", "message": "这段文字已经有解释了。"}}
    try:
        text = await ai.explain_selection(item["title"], plan.get("summary") or "",
                                          seg_text, scope_text[pos_start:pos_end])
    except ai.AIError as exc:
        return {"ok": False, "error": {"code": "AI_FAILED", "message": str(exc)}}
    node = await reading_store.create_node(key, seg, "explain", text, uid,
                                           parent_id=parent_id,
                                           pos_start=pos_start, pos_end=pos_end)
    if node is None:
        return {"ok": False, "error": {"code": "CONFLICT", "message": "该处已有解释。"}}
    return {"ok": True, "node": node}


@app.post("/api/reading/ask")
async def reading_ask(request: Request):
    """私有提问：可锚定选区或父节点（追问链通过 parent_id 嵌套）"""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": {"code": "BAD_REQUEST", "message": "请求体不是合法 JSON。"}}
    question = str(body.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": {"code": "EMPTY_QUESTION", "message": "请输入问题。"}}
    item, key, uid, error = await _reading_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    try:
        seg = int(body.get("seg") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": {"code": "BAD_RANGE", "message": "段落参数不合法。"}}
    parent_id = body.get("parent_id")
    pos_start = body.get("pos_start")
    pos_end = body.get("pos_end")
    plan = await reading_store.get_article(key)
    if not plan:
        return {"ok": False, "error": {"code": "NOT_OPENED", "message": "这个阅读会话还没开始。"}}
    seg_text, _total, _seg_start = segments.split_at_cuts(item["content"], plan.get("cuts") or [], seg)
    if seg_text is None:
        return {"ok": False, "error": {"code": "BAD_SEG", "message": "段落不存在。"}}
    anchor_text = ""
    # 选区校验（无效则清空）；层内偏移始终相对父节点的内容文本
    scope_text = seg_text
    if parent_id is not None:
        parent_node = await reading_store.get_node(int(parent_id))
        if parent_node is not None:
            scope_text = parent_node.get("content") or ""
    if not (isinstance(pos_start, int) and isinstance(pos_end, int)
            and 0 <= pos_start < pos_end <= len(scope_text)):
        pos_start = pos_end = None
    # 提问锚点：优先用显式传入的选中文本（右栏解释里选中的内容），否则由区间/父节点推导
    anchor_text = str(body.get("anchor_text") or "").strip()[:600]
    if not anchor_text and pos_start is not None:
        anchor_text = scope_text[pos_start:pos_end]
    if not anchor_text and parent_id is not None:
        parent = await reading_store.get_node(int(parent_id))
        if parent:
            anchor_text = parent.get("question") or parent.get("content") or ""
    try:
        answer = await ai.answer_question(question, plan.get("summary") or "",
                                          anchor_text, seg_text)
    except ai.AIError as exc:
        return {"ok": False, "error": {"code": "AI_FAILED", "message": str(exc)}}
    node = await reading_store.create_node(key, seg, "ask", answer, uid,
                                           parent_id=int(parent_id) if parent_id is not None else None,
                                           pos_start=pos_start, pos_end=pos_end, question=question)
    if node is None:
        return {"ok": False, "error": {"code": "CONFLICT", "message": "保存失败，请重试。"}}
    return {"ok": True, "node": node}


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
    item, key, uid, error = await _reading_context(request, str(body.get("url") or ""))
    if error:
        return {"ok": False, "error": error}
    seg_index = body.get("seg_index")
    node_id = body.get("node_id")
    await reading_store.append_event(
        key, uid, event,
        int(seg_index) if isinstance(seg_index, int) else None,
        int(node_id) if isinstance(node_id, int) else None,
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

    cache_key = "review:" + key if mode == "general" else ""
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
async def reading_delete_node(request: Request, node_id: int, url: str = ""):
    """删除解释/提问（含全部子层）：共享解释人人可删（自愈），私有提问仅本人可删"""
    item, key, uid, error = await _reading_context(request, url)
    if error:
        return {"ok": False, "error": error}
    node = await reading_store.get_node(node_id)
    if not node:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "节点不存在。"}}
    if node.get("kind") == "ask" and node.get("uid") != uid:
        return {"ok": False, "error": {"code": "FORBIDDEN", "message": "只能删除自己的提问。"}}
    deleted = await reading_store.delete_node_tree(node_id)
    await reading_store.append_event(key, uid, "deleted", node.get("seg_index"), node_id)
    return {"ok": True, "deleted": deleted}


# 静态文件（前端单页）——挂在最后，避免吞掉 /api 与 /auth 路由
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
