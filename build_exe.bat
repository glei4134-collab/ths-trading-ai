@echo off
cd /d "D:\claude work\ths-trading-ai"
if exist ".venv32\Scripts\activate.bat" call .venv32\Scripts\activate.bat

echo ============================================
echo   Building THS-Trading-AI.exe
echo   (excluding user data for safe sharing)
echo ============================================

pip install pyinstaller -q

python -m PyInstaller --noconsole --onefile --name "THS-Trading-AI" ^
    --paths src ^
    --add-data "static;static" ^
    --add-data "config\ai_providers.json;config" ^
    --add-data "config\competition_rules.json;config" ^
    --add-data "config\settings.json;config" ^
    --hidden-import "flask" ^
    --hidden-import "flask_cors" ^
    --hidden-import "pystray" ^
    --hidden-import "PIL" ^
    --hidden-import "PIL.Image" ^
    --hidden-import "PIL.ImageDraw" ^
    --hidden-import "akshare" ^
    --hidden-import "pandas" ^
    --hidden-import "numpy" ^
    --hidden-import "requests" ^
    --hidden-import "requests.adapters" ^
    --hidden-import "concurrent.futures" ^
    --hidden-import "urllib3" ^
    --hidden-import "bs4" ^
    --hidden-import "lxml" ^
    --hidden-import "paths" ^
    --hidden-import "db_store" ^
    --hidden-import "config" ^
    --hidden-import "ths_connector" ^
    --hidden-import "ai_connector" ^
    --hidden-import "ai_brain" ^
    --hidden-import "auto_trader" ^
    --hidden-import "notification" ^
    --hidden-import "account_manager" ^
    --hidden-import "competition" ^
    --hidden-import "strategy_registry" ^
    --hidden-import "strategy_tracker" ^
    --hidden-import "activity_log" ^
    --hidden-import "easytrader" ^
    --hidden-import "easyutils" ^
    --hidden-import "pywinauto" ^
    --hidden-import "comtypes" ^
    --hidden-import "pyquery" ^
    --hidden-import "fast_data" ^
    --hidden-import "data_cache" ^
    --hidden-import "risk_manager" ^
    --hidden-import "strategy" ^
    --hidden-import "enhanced_strategy" ^
    --hidden-import "broker" ^
    --hidden-import "data_store" ^
    --hidden-import "market_regime" ^
    --hidden-import "backtester" ^
    --hidden-import "stock_screener" ^
    --hidden-import "multi_timeframe" ^
    --hidden-import "server" ^
    --collect-submodules "pystray" ^
    --collect-submodules "PIL" ^
    --collect-data "akshare" ^
    desktop_app.py

echo.
echo ============================================
echo   Done! EXE at: dist\THS-Trading-AI.exe
echo   Safe to share (no user data included)
echo ============================================
pause
