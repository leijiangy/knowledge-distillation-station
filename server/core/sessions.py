# -*- coding: utf-8 -*-
"""会话存储：单实例内存 dict + TTL（部署单实例，够用且零中间件）"""
import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    # OAuth 流程状态
    state: str | None = None            # 本次登录请求的 state（一次性）
    token: str | None = None            # 用户 OAuth access_token
    expires_at: float | None = None     # token 过期时间（epoch 秒）
    profile: dict | None = None         # 用户公开信息（昵称/头像等）
    state_verified: bool | None = None  # 回调是否带回了 state
    error: dict | None = None           # 最近一次错误 {"code", "message"}


class SessionStore:
    def __init__(self, ttl_seconds: int = 8 * 3600):
        self._sessions: dict[str, Session] = {}
        self.ttl = ttl_seconds

    def create(self) -> Session:
        self.cleanup()
        sid = secrets.token_urlsafe(24)
        session = Session(id=sid)
        self._sessions[sid] = session
        return session

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        session = self._sessions.get(sid)
        if session is None:
            return None
        if time.time() - session.last_seen > self.ttl:
            self._sessions.pop(sid, None)
            return None
        session.last_seen = time.time()
        return session

    def drop(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def cleanup(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._sessions.items() if now - s.last_seen > self.ttl]
        for sid in stale:
            self._sessions.pop(sid, None)


sessions = SessionStore()
