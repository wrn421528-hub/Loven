@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python。请先从 https://www.python.org/downloads/windows/ 安装 Python 3.11 或更新版本，并勾选 Add Python to PATH。
  pause
  exit /b 1
)
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo 首次运行已创建 .env，请在打开的记事本中填写模型服务信息并保存。
  notepad .env
)
echo.
echo 浏览器打开地址：http://127.0.0.1:8000
echo 首次使用请先编辑当前文件夹里的 .env，填写 AI_API_KEY、AI_BASE_URL 和 AI_MODEL。
echo 按 Ctrl+C 可停止服务。
echo.
start "" http://127.0.0.1:8000
python -m uvicorn server:app --host 127.0.0.1 --port 8000
pause
