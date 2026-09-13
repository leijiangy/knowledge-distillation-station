# -*- coding: utf-8 -*-
"""知识蒸馏站 —— FastAPI 应用入口与路由（对照官方 Node 模板的接口形态）"""
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.sessions import sessions
from core.cache import content_cache, user_cache, user_key
from core import analyze, oauth, zhihu

app = FastAPI(title=settings.PROJECT_NAME, docs_url=None, redoc_url=None)

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
        if returned_state and session.state and not secrets.compare_digest(returned_state, session.state):
            raise oauth.ZhihuError("STATE_MISMATCH", "state 校验失败，登录已拒绝。")
        token = await oauth.exchange_token(code)
        session.token = token["access_token"]
        session.expires_at = (time.time() + token["expires_in"]) if token["expires_in"] else None
        session.state_verified = bool(returned_state)
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
    """为收藏条目附加三指标；指标按内容 URL 全站共享缓存（蒸馏成果沉淀）"""
    enriched = []
    for item in items:
        key = "metrics:" + str(item.get("Url") or item.get("Title") or "")
        metrics = content_cache.get(key)
        if metrics is None:
            metrics = analyze.metrics_for(item)
            content_cache.set(key, metrics)
        row = dict(item)
        row["metrics"] = metrics
        enriched.append(row)
    return enriched


@app.get("/api/favlists")
async def favlists(request: Request):
    """收藏夹列表（登录用户；本地未登录时走本人模式自测）"""
    session = _current_session(request)
    token = session.token if session else None
    try:
        items = await zhihu.fetch_favlists(oauth_token=token)
        return {"ok": True, "items": items}
    except zhihu.ZhihuError as exc:
        return {"ok": False, "error": {"code": str(exc.code), "message": exc.message}}


@app.get("/api/collections")
async def collections(request: Request, favlist: str | None = None, force: int = 0):
    """收藏全量读取（分页取全 + 用户私有缓存 + 三指标）。

    favlist 缺省时取用户第一个收藏夹；force=1 时绕过用户缓存主动取新数据（用户点「刷新」）。
    """
    session = _current_session(request)
    token = session.token if session else None
    uid = str((session.profile or {}).get("uid") or "anon") if session else "self"
    cache_key = user_key("collections:" + str(favlist or "first"), uid)
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
        }
        user_cache.set(cache_key, payload)
        return {"ok": True, "cached": False, **payload}
    except zhihu.ZhihuError as exc:
        return {"ok": False, "error": {"code": str(exc.code), "message": exc.message}}


# 静态文件（前端单页）——挂在最后，避免吞掉 /api 与 /auth 路由
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
