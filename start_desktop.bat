@echo off
cd /d "%~dp0"
title THS Trading AI - Cockpit Workstation

echo =====================================================================
echo   THS Trading AI - Autonomous Operations Cockpit (2026 Team Edition)
echo =====================================================================
echo [INFO] Launching Edge WebView2 Desktop Application...
echo [INFO] Python process starting. This console will hide automatically.
echo =====================================================================

python desktop_app.py
if errorlevel 1 (
    echo.
    echo =============================================================
    echo [ERROR] Desktop app failed to start.
    echo Check the console output above or view trading.log for details.
    echo =============================================================
    pause
)