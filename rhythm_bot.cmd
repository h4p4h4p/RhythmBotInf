@echo off
setlocal
cd /d "%~dp0"

REM Request administrator rights (needed for the global hotkeys to be
REM visible when the game itself runs elevated). Skip with --no-admin.
if /I not "%1"=="--no-admin" (
    net session >nul 2>&1
    if errorlevel 1 (
        echo Requesting administrator rights...
        powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
        exit /b
    )
)

set "PY=py"
where py >nul 2>nul || set "PY=python"

if not exist "%~dp0rhythm_bot.py" (
    echo rhythm_bot.py not found in %~dp0
    pause
    exit /b 1
)

REM Stop any old copy first so you never get "already running".
for /f "usebackq delims=" %%i in (`powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match 'rhythm_bot' } | ForEach-Object { $_.ProcessId }"`) do taskkill /f /pid %%i >nul 2>nul

echo ============================================================
echo   RHYTHM BOT
echo   - Enter or F9 to start. Keep this window open.
echo   - First time: C to Calibrate, then CLICK each of the
echo     4 markers on the receptors.
echo   - Global keys work even in-game: [ ] threshold, - = delay,
echo     Z X probe distance, R jitter, O probe side, V overlay.
echo   - q = quit.  Use --no-admin to start without elevation.
echo ============================================================
echo.
%PY% "%~dp0rhythm_bot.py" %*
pause