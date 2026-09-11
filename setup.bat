@echo off
setlocal
title Rhythm Bot - Setup
echo ============================================================
echo   Rhythm Bot setup
echo   Installs the Python packages the bot needs, then verifies.
echo ============================================================
echo.

set "PY=py"
where py >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul || (
    echo [ERROR] Python was not found.
    echo Install it from https://www.python.org/downloads/  ^(tick "Add to PATH"^)
    echo then run this setup again.
    pause
    exit /b 1
)

echo [1/2] Installing: mss, pydirectinput, keyboard, pynput ...
%PY% -m pip install --upgrade pip >nul 2>nul
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] Install failed - scroll up to see why.
    pause
    exit /b 1
)

echo.
echo [2/2] Verifying packages ...
%PY% -c "import mss, pydirectinput, keyboard, pynput; print('all packages OK')" || (
    echo [WARNING] Some packages are missing - re-run this setup.
)

echo.
echo ============================================================
echo   Done!  Now start the bot with:   rhythm_bot
echo   (or double-click rhythm_bot.cmd)
echo ============================================================
echo.
pause