#!/usr/bin/env bash
# 知识蒸馏站 - 本地启动（macOS / Linux）
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  知识蒸馏站 - 本地启动"
echo "============================================"

if [ ! -x ".venv/bin/python" ]; then
  echo "[初始化] 首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f ".env" ]; then
  echo "[警告] 未找到 .env 凭证文件，接口调用会失败。"
  echo "       请从队友处获取 .env（包含 Access Secret / OAuth 密钥）。"
fi

echo ""
echo "  启动后请在浏览器打开: http://127.0.0.1:4173"
echo "  本地预览模式（ALLOW_SELF_MODE=1）直接读取本人收藏夹"
echo "  按 Ctrl+C 停止服务"
echo ""
exec .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 4173
