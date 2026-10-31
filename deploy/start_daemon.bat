@echo off
chcp 65001 >nul 2>&1
title 同花顺AI量化系统 - 7x24守护进程
echo ==========================================================
echo    同花顺AI量化实盘系统 - 本地7x24小时守护与自动重启进程
echo ==========================================================
echo.

cd /d "%~dp0.."

REM 1. 查找可用 Python 解释器
set PY_CMD=
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
) else if exist ".venv32\Scripts\python.exe" (
    set "PY_CMD=.venv32\Scripts\python.exe"
) else (
    set "PY_CMD=python"
)

echo [INFO] 使用 Python 解释器: %PY_CMD%
echo.

REM 2. 执行启动前全链路自检与 WAL 维护
echo [INFO] 正在执行启动前量化链路体检与健康自愈...
"%PY_CMD%" tools\auto_debugger.py --check-all --auto-fix
if %errorlevel% neq 0 (
    echo [WARN] 体检探针报告异常，按任意键继续启动，或关闭窗口排查。
)

echo.
echo ==========================================================
echo [INFO] 守护循环已激活：若后端主服务意外异常退出，将在 3 秒后自动拉起自愈！
echo ==========================================================
echo.

:RESTART_LOOP
echo [%DATE% %TIME%] [DAEMON] 正在拉起量化交易后端主程序 (api.py)...
"%PY_CMD%" api.py

echo.
echo [%DATE% %TIME%] [WARN] 检测到后端主服务退出 (ExitCode: %ERRORLEVEL%)
echo [%DATE% %TIME%] [RECOVERY] 执行异常退出收尾体检与 WAL 截断自愈...
"%PY_CMD%" tools\auto_debugger.py --auto-fix

echo [%DATE% %TIME%] [DAEMON] 3秒后重新拉起主进程...
timeout /t 3 /nobreak >nul
goto RESTART_LOOP
