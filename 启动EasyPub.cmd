@echo off
chcp 65001 >nul
set "ELECTRON_RUN_AS_NODE="
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~dp0node_modules\electron\dist\electron.exe' -ArgumentList '.' -WorkingDirectory '%~dp0'"
exit /b 0
