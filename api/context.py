"""
量化系统 API 共享上下文与通用依赖注入中心
"""

import os
import sys
import json
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Any

from flask import jsonify, request, Response

logger = logging.getLogger(__name__)

# 确保 src 目录在 Python 路径中
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# 导入所有量化核心模块
from strategy import TradingEngine
from broker import THSBroker, AutoTrader
from config import config_manager
from ths_connector import web_connector, get_active_connector, decrypt_cookie
from ai_connector import ai_connector
from enhanced_strategy import enhanced_strategy, SignalType
from auto_trader import auto_trader, OrderType
from risk_manager import risk_manager
from backtester import backtester
from data_store import data_store
from competition import competition_manager
from ai_brain import strategy_scanner
from data_cache import (
    cache, get_realtime_quote, get_realtime_quotes_batch,
    get_kline, get_intraday, compute_indicators, get_kline_with_indicators
)
from notification import notification_manager, cockpit_hub
from db_store import db_store
from account_manager import account_manager
from device_manager import init_device_manager
from auto_login_service import auto_login_service

# 全局单例与状态
engine = TradingEngine()
broker = THSBroker(mode="simulation")
trader = AutoTrader(broker)
_device_manager = init_device_manager(db_store)
trading_mode = "simulation"
trading_state = {
    "balance": 1000000,
    "positions": {},
    "trades": []
}

# 认证配置
API_TOKEN = os.environ.get("API_TOKEN", "")
_token_file = Path(__file__).resolve().parent.parent / "config" / "api_token.json"
if not API_TOKEN and _token_file.exists():
    try:
        API_TOKEN = json.loads(_token_file.read_text(encoding="utf-8")).get("token", "")
    except Exception:
        pass

_PUBLIC_PATHS = {
    '/', '/api/health', '/api/auth/login',
    '/api/mobile/notifications/stream',
    '/api/mobile/bind/claim',
    '/api/device/bind/claim',
    '/api/accounts/quick-bind',
    '/api/ths/auth/send-sms',
    '/api/ths/auth/login-sms',
    '/api/ths/auth/login-pwd',
    '/api/stream/cockpit',
    '/api/stream/cockpit/test-broadcast'
}

def require_auth():
    """检查 API 认证，未启用认证时直接放行"""
    if not API_TOKEN:
        return None
    if request.path in _PUBLIC_PATHS:
        return None
    if request.path.startswith('/static/') or request.path.endswith('.html') or request.path.endswith('.js') or request.path.endswith('.css'):
        return None

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.args.get("token", "")

    from device_manager import device_manager as dm
    if dm and token:
        payload = dm.verify_token(token)
        if payload:
            request.device = payload
            return None

    if token != API_TOKEN:
        return jsonify({"success": False, "message": "未授权，请先登录"}), 401
    return None

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except Exception:
    AKSHARE_AVAILABLE = False


def get_stock_data(symbol):
    """获取股票数据（fast_data + 缓存）"""
    row = get_realtime_quote(symbol)
    if row is not None:
        try:
            if isinstance(row, dict) and "code" in row:
                return {
                    "code": row.get("code", symbol),
                    "name": row.get("name", ""),
                    "price": float(row.get("price", 0)),
                    "change": float(row.get("change", 0)),
                    "change_pct": float(row.get("change_pct", 0)),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "pre_close": float(row.get("pre_close", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                    "turnover": float(row.get("turnover", 0)),
                }
            elif hasattr(row, '__getitem__') and '代码' in row:
                return {
                    "code": row['代码'],
                    "name": row['名称'],
                    "price": float(row['最新价']),
                    "change": float(row['涨跌额']),
                    "change_pct": float(row['涨跌幅']),
                    "open": float(row['今开']),
                    "high": float(row['最高']),
                    "low": float(row['最低']),
                    "pre_close": float(row['昨收']),
                    "volume": int(row['成交量']),
                    "amount": float(row['成交额']),
                    "turnover": float(row['换手率'])
                }
        except Exception:
            pass
    return None


def calculate_indicators(symbol: str) -> Dict:
    """计算真实技术指标（MA/RSI/MACD/布林带/ATR/ADX）"""
    kline = get_kline(symbol, period="daily", days=120)
    if not kline or len(kline) < 20:
        return {}
    return compute_indicators(kline)
