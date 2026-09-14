# -*- coding: utf-8 -*-
"""知乎 OAuth 流程（照官方协议实现）。

依据：skills/zhihu/references/hackathon-oauth.md +
      skills/zhihu/references/hackathon-user-profile-api.md
- 授权 URL / Token 交换 / state 透传与校验要求
- /user 接口只带 Bearer <oauth_token>（无 Access Secret、无时间戳）
"""
import secrets
import urllib.parse

from .config import settings
from .zhihu import ZhihuError, client


def new_state() -> str:
    """密码学安全随机 state（每次登录生成，一次性消费）"""
    return secrets.token_urlsafe(24)


def profile_uid(profile: dict | None) -> str | None:
    """Return the stable decimal Zhihu uid used for private data and billing."""
    raw = (profile or {}).get("uid")
    if isinstance(raw, bool):
        return None
    value = str(raw or "").strip()
    if not value.isdecimal():
        return None
    try:
        number = int(value)
        return str(number) if number > 0 else None
    except ValueError:
        return None


def check_state(returned: str | None, expected: str | None) -> str:
    """回调 state 校验（结果四态，调用方据此决定放行或拒绝）。

    - missing:    未发起过登录（expected 缺失）→ 拒绝
    - mismatch:   returned 与 expected 不一致 → 拒绝
    - verified:   一致 → 通过（state 校验标记为已验证）
    - unverified: 官方实测回调可能不回传 state，容忍但标记为未验证（仅联调可用）
    """
    if not expected:
        return "missing"
    if not returned:
        return "unverified"
    return "verified" if secrets.compare_digest(str(returned), str(expected)) else "mismatch"


def build_authorize_url(state: str) -> str:
    if not settings.REDIRECT_URI:
        raise ZhihuError("DEPLOYMENT_REQUIRED", "本地地址无法完成知乎登录，请先部署并配置公网回调地址。")
    params = {
        "redirect_uri": settings.REDIRECT_URI,
        "app_id": settings.APP_ID,
        "response_type": "code",
        "state": state,
    }
    return f"{settings.ZHIHU_OAUTH}/authorize?" + urllib.parse.urlencode(params)


async def exchange_token(code: str) -> dict:
    """授权码换 access_token（表单 POST）"""
    if not settings.APP_KEY:
        raise ZhihuError("APP_KEY_REQUIRED", "OAuth app_key 尚未配置。")
    form = {
        "app_id": settings.APP_ID,
        "app_key": settings.APP_KEY,
        "grant_type": "authorization_code",
        "redirect_uri": settings.REDIRECT_URI,
        "code": code,
    }
    async with client() as http:
        resp = await http.post(f"{settings.ZHIHU_OAUTH}/access_token", data=form)
    try:
        payload = resp.json()
    except Exception:
        raise ZhihuError("TOKEN_EXCHANGE_FAILED", "换取访问令牌时收到无法解析的响应。")
    data = payload.get("data") or payload.get("Data") or payload
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        message = data.get("message") if isinstance(data, dict) else None
        raise ZhihuError(payload.get("code", "TOKEN_EXCHANGE_FAILED"), message or "未获得 OAuth access token。")
    expires_in = data.get("expires_in") if isinstance(data, dict) else None
    return {"access_token": token, "expires_in": int(expires_in) if expires_in else None}


async def fetch_profile(oauth_token: str) -> dict:
    """GET /user（官方 profile 文档：只带 Bearer oauth_token，code 20000 亦为成功）"""
    async with client() as http:
        resp = await http.get(
            f"{settings.ZHIHU_OAUTH}/user",
            headers={"Authorization": f"Bearer {oauth_token}"},
        )
    try:
        payload = resp.json()
    except Exception:
        raise ZhihuError("PROFILE_FAILED", "获取用户信息时收到无法解析的响应。")
    code = payload.get("code")
    if code is not None and code not in (0, 20000):
        raise ZhihuError(code, payload.get("data") or payload.get("message") or "获取用户信息失败。")
    source = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    uid = profile_uid(source if isinstance(source, dict) else None)
    if uid is None:
        raise ZhihuError("ACCOUNT_ID_REQUIRED", "用户信息响应缺少有效知乎 uid，请重新授权。")
    return {
        "uid": uid,
        "name": source.get("fullname"),
        "avatar_url": source.get("avatar_path"),
        "headline": source.get("headline"),
        "url": source.get("url"),
    }


def local_path(raw: str) -> str:
    """把「登录后要落回哪里」限制为站内相对路径，不合法返回空串（避免开放重定向）

    只用于 OAuth 流程的落地地址：必须是 / 开头的本机路径，不能是协议相 URL、协议相对 URL，
    也不能带反斜杠（浏览器会把反斜杠当作 / 处理，能被绕过）。
    """
    s = str(raw or "").strip()
    if not s.startswith("/") or s.startswith("//") or "\\" in s or "://" in s:
        return ""
    return s[:300]
