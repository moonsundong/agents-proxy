#!/usr/bin/env bash
# One-click stop for LLM Agent Gateway (ports 8300 / 5188)
for PORT in 8300 5188; do
  PIDS=$(netstat -ano | grep ":$PORT " | grep LISTENING | awk '{print $NF}' | sort -u)
  for p in $PIDS; do
    echo "Killing PID $p on port $PORT"
    taskkill //PID "$p" //F > /dev/null 2>&1
  done
done
echo "Done."
