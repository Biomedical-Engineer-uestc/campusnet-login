@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%~dp0CampusNet.exe" (
    echo [ERROR] CampusNet.exe not found in this folder.
    pause
    exit /b 1
)

"%~dp0CampusNet.exe" %*
set "CODE=%errorlevel%"

if not "%CODE%"=="0" (
    echo.
    pause
) else (
    ping -n 3 127.0.0.1 >nul
)
exit /b %CODE%
