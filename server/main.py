# -*- coding: utf-8 -*-
"""知识蒸馏站 —— FastAPI 应用入口与路由（对照官方 Node 模板的接口形态）"""
import asyncio
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.sessions import sessions
from core.cache import content_cache, user_cache, user_key
from core import analyze, oauth, zhihu

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
async def oauth_start(request: Request, response: Response):
    session = _get_or_create(request, response)
    try:
        session.state = oauth.new_state()
        session.error = None
        url = oauth.build_authorize_url(session.state)
        return RedirectResponse(url, status_code=302)
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
        redirect = RedirectResponse("/?oauth=success", status_code=302)
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
            }
            content_cache.set(key, meta)
        meta_map[url] = meta
        await asyncio.sleep(0.15)  # 温和限速，避免触发风控
    return {"ok": True, "meta": meta_map}


# ---- 书签小工具：全文接收与存储（演示阶段为内存存储，后续接 T9 持久化） ----
distilled_store: dict = {}   # 内容 URL(去参) -> {title, url, content, at}
DISTILL_MAX_CHARS = 200_000


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
    if not url or "zhihu.com" not in url:
        return {"ok": False, "error": {"code": "BAD_URL", "message": "需要知乎内容链接。"}}
    if len(content) < 100:
        return {"ok": False, "error": {"code": "TOO_SHORT", "message": "内容过短，可能不是文章页。"}}
    if len(content) > DISTILL_MAX_CHARS:
        return {"ok": False, "error": {"code": "TOO_LONG", "message": "内容过长。"}}
    clean_images: list = []
    if isinstance(images, list):
        for item in images[:9]:
            s = str(item or "").strip()
            if s.startswith("http") and len(s) <= 500 and s not in clean_images:
                clean_images.append(s)
    key = url.split("?")[0].split("#")[0]
    distilled_store[key] = {"title": title, "url": url, "content": content,
                            "images": clean_images, "at": int(time.time())}
    return {"ok": True, "length": len(content), "images": len(clean_images),
            "total": len(distilled_store)}


@app.get("/api/distilled")
async def distilled_index():
    """已蒸馏内容索引（供列表打标：哪些收藏已有全文）"""
    return {
        "ok": True,
        "items": {
            k: {"title": v["title"], "length": len(v["content"]), "at": v["at"],
                "images": v.get("images") or [],
                "cover": (v.get("images") or [None])[0]}
            for k, v in distilled_store.items()
        },
    }


@app.get("/api/distilled/content")
async def distilled_content(url: str):
    """读取某篇已蒸馏文章的全文"""
    key = url.split("?")[0].split("#")[0]
    item = distilled_store.get(key)
    if not item:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "这篇还没有全文，试试书签工具。"}}
    return {"ok": True, "title": item["title"], "url": item["url"],
            "content": item["content"], "images": item.get("images") or [],
            "length": len(item["content"]), "at": item["at"]}


# 静态文件（前端单页）——挂在最后，避免吞掉 /api 与 /auth 路由
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
