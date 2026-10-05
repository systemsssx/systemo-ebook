@echo off
rem ============================================================
rem  EasyPub dev launcher
rem
rem  1. clears ELECTRON_RUN_AS_NODE
rem     (if set, Electron runs in plain-Node mode and main.js dies
rem      with "Cannot read properties of undefined (reading isPackaged)")
rem  2. refreshes index.html ?v= cache busters
rem  3. starts Electron, teeing all output to _launch.log
rem  4. if the app died, prints crash.log (main-process errors)
rem ============================================================
setlocal
rem quotes matter: "set X= && ..." would set X to a single space instead of clearing it
set "ELECTRON_RUN_AS_NODE="
cd /d "%~dp0"

set LOG=%~dp0_launch.log
del "%LOG%" 2>nul
del crash.log 2>nul

echo EasyPub launch log > "%LOG%"
echo time: %DATE% %TIME% >> "%LOG%"
call node --version >> "%LOG%" 2>&1
echo ---- bump ---- >> "%LOG%"
call npm run bump >> "%LOG%" 2>&1

echo.
echo Starting EasyPub ...
echo.
call npx electron . >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

echo.
echo ============================================================
echo  EasyPub exited (code %RC%)
echo ============================================================

if exist crash.log (
    echo.
    echo  ---- crash.log ----
    type crash.log
    echo  -------------------
)

echo.
echo  Full output: %LOG%
echo.
pause
endlocal & exit /b %RC%
