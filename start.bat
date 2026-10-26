@echo off
chcp 65001 >nul 2>&1
title 同花顺AI交易系统
echo ==========================================
echo   同花顺模块化AI交易系统 - Web前端
echo ==========================================
echo.

REM Check 32-bit Python exists
if not exist ".venv32\Scripts\python.exe" (
    echo [ERROR] 未找到32位Python环境 .venv32
    echo 请确保项目目录下有 .venv32 文件夹
    pause
    exit /b 1
)

REM Check xiadan.exe path
set THS_PATH=C:\同花顺软件\同花顺\xiadan.exe
if not exist "%THS_PATH%" (
    set THS_PATH=D:\同花顺软件\同花顺\xiadan.exe
)
if not exist "%THS_PATH%" (
    echo [WARN] 未找到同花顺下单客户端 xiadan.exe
    echo 请在配置页面手动设置路径
    echo.
)

echo 使用32位Python: %~dp0.venv32\Scripts\python.exe
echo.

REM Start Flask server
".venv32\Scripts\python.exe" api.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 服务器启动失败
    pause
)
