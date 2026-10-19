@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Stopping the CampusNet background daemon...
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0campus_net.py" --stop
)

echo Removing the startup shortcut...
cscript //nologo "%~dp0uninstall_autostart.vbs"

echo.
echo Autostart removed. The saved password is kept.
echo To delete it as well: python campus_net.py --forget
echo.
pause
