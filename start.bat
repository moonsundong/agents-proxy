@echo off
setlocal
rem One-click startup for LLM Agent Gateway (backend + frontend)
rem 傻瓜式设计: 先强制清场(杀掉占用端口的任何旧进程), 再启动, 最后自检。
rem Double-click this file or run: start.bat

set ROOT=%~dp0

echo ============================================
echo   LLM Agent Gateway - Starting services
echo ============================================

rem ---- Step 0: 清场,杀掉占用 8300/5188 的任何进程(防僵尸) ----
echo [0/3] Cleaning old processes on ports 8300 / 5188 ...
for %%P in (8300 5188) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
        echo   Killing leftover PID %%a on port %%P
        taskkill /PID %%a /T /F > nul 2>&1
    )
)

rem ---- Backend (FastAPI / uvicorn, port 8300) ----
if not exist "%ROOT%api_gateway\.env" (
    copy "%ROOT%api_gateway\.env.example" "%ROOT%api_gateway\.env" > nul
    echo [init] Created api_gateway\.env from .env.example
)
rem 固定使用项目虚拟环境, 不存在则创建(绝不用系统 Python)
if not exist "%ROOT%.venv\Scripts\uvicorn.exe" (
    echo [init] Creating Python virtualenv at .venv ...
    python -m venv "%ROOT%.venv"
    "%ROOT%.venv\Scripts\python.exe" -m pip install -q -r "%ROOT%api_gateway\requirements.txt"
)
echo [1/3] Starting backend  -^> http://localhost:8300 (docs: /docs)
start "gateway-backend" cmd /k "cd /d %ROOT%api_gateway && %ROOT%.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8300"

rem ---- Frontend (Vite dev server, port 5188) ----
if not exist "%ROOT%ai-proxy-frontend\node_modules" (
    echo [init] Installing frontend dependencies...
    pushd "%ROOT%ai-proxy-frontend"
    call npm install --no-audit --no-fund
    popd
)
echo [2/3] Starting frontend -^> http://localhost:5188
start "gateway-frontend" cmd /k "cd /d %ROOT%ai-proxy-frontend && npm run dev"

rem ---- Step 3: 自检,等后端真正可用再报告 ----
echo [3/3] Waiting for backend to become ready ...
for /l %%i in (1,1,30) do (
    curl -s -m 2 http://localhost:8300/health > nul 2>&1
    if not errorlevel 1 goto :backend_ok
    ping -n 2 127.0.0.1 > nul
)
echo.
echo [FAIL] Backend did not respond within 30s.
echo        Open the "gateway-backend" window to see the error.
goto :end

:backend_ok
echo.
echo [OK] Backend is ready:  http://localhost:8300
echo [OK] Frontend:          http://localhost:5188
echo.
echo Two windows were opened (gateway-backend / gateway-frontend).
echo Close them to stop the services, or run stop.bat.

:end
endlocal
