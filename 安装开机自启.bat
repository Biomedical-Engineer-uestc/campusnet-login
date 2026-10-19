@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3 and add it to PATH.
    pause
    exit /b 1
)

echo ============================================
echo   CampusNet first-time setup
echo   Detect the campus login page and save
echo   the password with the OS credential store
echo ============================================
echo.
python "%~dp0campus_net.py" --setup
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
