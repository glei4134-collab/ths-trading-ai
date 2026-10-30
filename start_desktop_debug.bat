@echo off
cd /d "%~dp0"
title THS Trading AI - Debug Console

echo =====================================================================
echo   THS Trading AI - Autonomous Operations Cockpit (Debug Mode)
echo =====================================================================

python desktop_app.py --debug
pause