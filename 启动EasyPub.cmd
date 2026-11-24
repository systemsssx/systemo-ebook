@echo off
chcp 65001 >nul
set ELECTRON_RUN_AS_NODE=
cd /d "C:\work\epubui\EasyPub-New"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'C:\work\epubui\EasyPub-New\node_modules\electron\dist\electron.exe' -ArgumentList '.' -WorkingDirectory 'C:\work\epubui\EasyPub-New'"
exit /b 0
