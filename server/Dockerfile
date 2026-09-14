# 知识蒸馏站 —— 容器镜像定义（CloudBase 云托管 / 任意容器平台）
FROM python:3.12-slim

WORKDIR /app

# 内容版本使用系统 Git 的公开对象/引用接口；ca-certificates 保证外部 API TLS 链完整。
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用构建缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（.env 已被 .dockerignore 排除，凭证由平台环境变量注入）
COPY . .

# 云托管默认端口 80；本地/其他平台可用 PORT 环境变量覆盖
ENV PORT=80
EXPOSE 80

# HOST 必须为 0.0.0.0，容器外才能访问
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
