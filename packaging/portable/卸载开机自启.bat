@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0CampusNet.exe" (
    echo Stopping the CampusNet background daemon...
    "%~dp0CampusNet.exe" --stop
)

echo Removing the startup shortcut...
cscript //nologo "%~dp0uninstall_autostart.vbs"

echo.
echo Autostart removed. The saved password is kept.
echo To delete it as well, run: CampusNet.exe --forget
echo.
pause
