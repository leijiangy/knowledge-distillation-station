# -*- coding: utf-8 -*-
"""配置加载：.env 文件 + 环境变量（环境变量优先）"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # server/


def load_env_file() -> None:
    """加载 server/.env（该文件被 .gitignore 忽略，绝不入库）"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


load_env_file()


class Settings:
    PROJECT_NAME = "知识蒸馏站"

    # 知乎开放平台
    ACCESS_SECRET = os.environ.get("ZHIHU_ACCESS_SECRET", "")
    APP_ID = os.environ.get("ZHIHU_OAUTH_APP_ID", "441")
    APP_KEY = os.environ.get("ZHIHU_OAUTH_APP_KEY", "")
    # 公网回调地址（部署后配置，格式 https://<域名>/auth/callback）
    REDIRECT_URI = os.environ.get("ZHIHU_OAUTH_REDIRECT_URI", "")

    # AI 能力
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

    # 服务
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "4173"))

    # 固定端点
    ZHIHU_API = "https://developer.zhihu.com"
    ZHIHU_OAUTH = "https://openapi.zhihu.com"
    DEEPSEEK_API = "https://api.deepseek.com"

    # 会话
    SESSION_COOKIE = "kd_session"
    SESSION_TTL_SECONDS = 8 * 3600

    @property
    def oauth_ready(self) -> bool:
        """OAuth 三要素齐备（回调地址部署后才会有）"""
        return bool(self.APP_KEY and self.ACCESS_SECRET and self.REDIRECT_URI)


settings = Settings()
