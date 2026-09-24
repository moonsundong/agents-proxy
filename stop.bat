@echo off
rem One-click stop for LLM Agent Gateway (ports 8300 / 5188)
echo Stopping gateway services...
for %%P in (8300 5188) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
        echo Killing PID %%a on port %%P
        taskkill /PID %%a /F > nul 2>&1
    )
)
echo Done.
pause
