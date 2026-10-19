@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%~dp0CampusNet.exe" (
    echo [ERROR] CampusNet.exe not found in this folder.
    pause
    exit /b 1
)

echo ============================================
echo   CampusNet first-time setup
echo   Detect the login page and save the
echo   password with the OS credential store
echo ============================================
echo.
"%~dp0CampusNet.exe" --setup
if errorlevel 1 (
    echo.
    echo Setup did not complete. Installation cancelled.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Install logon autostart
echo ============================================
cscript //nologo "%~dp0install_autostart.vbs"
if errorlevel 1 (
    echo Failed to create the startup shortcut.
    pause
    exit /b 1
)

echo.
echo Startup shortcut created: CampusNet.lnk
echo Starting the background daemon...
wscript "%~dp0run_watch.vbs"

echo.
echo Done. CampusNet will start after Windows logon and reconnect after drops.
echo To uninstall, run the uninstall batch file.
echo.
pause
