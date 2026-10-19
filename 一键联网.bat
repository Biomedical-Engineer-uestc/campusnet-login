@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3 and add it to PATH.
    echo.
    pause
    exit /b 1
)

python "%~dp0campus_net.py" %*
set "CODE=%errorlevel%"

if not "%CODE%"=="0" (
    echo.
    pause
) else (
    ping -n 3 127.0.0.1 >nul
)
exit /b %CODE%
