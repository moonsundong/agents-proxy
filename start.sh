#!/usr/bin/env bash
# One-click startup for LLM Agent Gateway (backend + frontend)
# 傻瓜式设计: 先强制清场(杀掉占用端口的任何旧进程), 再启动, 最后自检。
# Usage: ./start.sh   (Ctrl+C stops both services)
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ---- Step 0: 清场,杀掉占用 8300/5188 的任何进程(防僵尸) ----
echo "[0/3] Cleaning old processes on ports 8300 / 5188 ..."
for PORT in 8300 5188; do
  PIDS=$(netstat -ano | grep ":$PORT " | grep LISTENING | awk '{print $NF}' | sort -u)
  for p in $PIDS; do
    echo "  Killing leftover PID $p on port $PORT"
    taskkill //PID "$p" //T //F > /dev/null 2>&1
  done
done

# ---- Backend ----
if [ ! -f "$ROOT/api_gateway/.env" ]; then
  cp "$ROOT/api_gateway/.env.example" "$ROOT/api_gateway/.env"
  echo "[init] Created api_gateway/.env from .env.example"
fi

# 固定使用项目虚拟环境, 不存在则创建(绝不用系统 Python)
VENV_UVICORN="$ROOT/.venv/Scripts/uvicorn.exe"
if [ ! -f "$VENV_UVICORN" ]; then
  echo "[init] Creating Python virtualenv at .venv ..."
  python -m venv "$ROOT/.venv"
  "$ROOT/.venv/Scripts/python.exe" -m pip install -q -r "$ROOT/api_gateway/requirements.txt"
fi

echo "[1/3] Starting backend  -> http://localhost:8300 (docs: /docs)"
(cd "$ROOT/api_gateway" && "$VENV_UVICORN" app.main:app --reload --host 0.0.0.0 --port 8300) &
BACK_PID=$!

# ---- Frontend ----
if [ ! -d "$ROOT/ai-proxy-frontend/node_modules" ]; then
  echo "[init] Installing frontend dependencies..."
  (cd "$ROOT/ai-proxy-frontend" && npm install --no-audit --no-fund)
fi

echo "[2/3] Starting frontend -> http://localhost:5188"
(cd "$ROOT/ai-proxy-frontend" && npm run dev) &
FRONT_PID=$!

# ---- Step 3: 自检,等后端真正可用再报告 ----
echo "[3/3] Waiting for backend to become ready ..."
READY=0
for _ in $(seq 1 30); do
  if curl -s -m 2 http://localhost:8300/health > /dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" = "1" ]; then
  echo ""
  echo "[OK] Backend is ready:  http://localhost:8300"
  echo "[OK] Frontend:          http://localhost:5188"
  echo ""
  echo "Both services started. Ctrl+C to stop."
else
  echo ""
  echo "[FAIL] Backend did not respond within 30s - check the log above."
fi

trap 'kill $BACK_PID $FRONT_PID 2>/dev/null' EXIT
wait
