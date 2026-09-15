# -*- coding: utf-8 -*-
"""配置加载：.env 文件 + 环境变量（环境变量优先）"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # server/


def load_env_file() -> None:
    """加载 server/.env（该文件被 .gitignore 忽略，绝不入库）。

    编码容错：优先 UTF-8，失败回退 GBK（Windows 记事本/CMD 编辑可能存成 GBK），
    避免一份配置文件让整个服务起不来。
    """
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    raw = env_file.read_bytes()
    text = None
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
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
    DSH_MODEL = os.environ.get("DSH_MODEL", "deepseek-flash")

    # 内容版本、积分计费与会员演示购买。
    CONTENT_GIT_DIR = os.environ.get("CONTENT_GIT_DIR", "")
    BILLING_ENABLED = os.environ.get("BILLING_ENABLED") == "1"
    RECHARGE_ENABLED = os.environ.get("RECHARGE_ENABLED") == "1"
    MODEL_TOKENIZER_DIR = os.environ.get("MODEL_TOKENIZER_DIR", "")
    CLOUDBASE_PAY_FUNCTION = os.environ.get("CLOUDBASE_PAY_FUNCTION", "")

    # 服务
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "4173"))

    # 固定端点
    ZHIHU_API = "https://developer.zhihu.com"
    ZHIHU_OAUTH = "https://openapi.zhihu.com"
    DEEPSEEK_API = "https://api.deepseek.com"

    # 本地开发调试开关：允许未登录请求以 Access Secret 本人身份读取数据。
    # ⚠️ 默认关闭。公网部署绝不能开启——否则任何访客都能读到项目账号的私密收藏。
    ALLOW_SELF_MODE = os.environ.get("ALLOW_SELF_MODE") == "1"

    # 会话
    SESSION_COOKIE = "kd_session"
    SESSION_TTL_SECONDS = 8 * 3600

    @property
    def oauth_ready(self) -> bool:
        """OAuth 三要素齐备（回调地址部署后才会有）"""
        return bool(self.APP_KEY and self.ACCESS_SECRET and self.REDIRECT_URI)


settings = Settings()
