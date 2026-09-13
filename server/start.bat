@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   知识蒸馏站 - 本地启动
echo ============================================

if not exist ".venv\Scripts\python.exe" (
  echo [初始化] 首次运行：创建虚拟环境并安装依赖...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
  )
  .venv\Scripts\python.exe -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)

if not exist ".env" (
  echo [警告] 未找到 .env 凭证文件，接口调用会失败。
  echo        请从队友处获取 .env（包含 Access Secret / OAuth 密钥）。
)

echo.
echo   启动后请在浏览器打开: http://127.0.0.1:4173
echo   本地预览模式（ALLOW_SELF_MODE=1）直接读取本人收藏夹
echo   按 Ctrl+C 停止服务
echo.
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 4173
pause
