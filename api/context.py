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
# ALLOW_INSECURE=1 时跳过鉴权（仅本地调试）。否则自动生成并持久化 API_TOKEN。
import secrets as _secrets

_token_file = Path(__file__).resolve().parent.parent / "config" / "api_token.json"
_host = os.environ.get("HOST", "127.0.0.1")
_is_local = _host in ("127.0.0.1", "localhost", "::1")
_ALLOW_INSECURE = _is_local and os.environ.get("ALLOW_INSECURE", "").lower() in ("1", "true", "yes")


def _load_or_create_api_token() -> str:
    if _ALLOW_INSECURE:
        logger.warning("ALLOW_INSECURE=1：API 鉴权已关闭，禁止用于生产")
        return ""
    token = os.environ.get("API_TOKEN", "").strip()
    if not token and _token_file.exists():
        try:
            token = json.loads(_token_file.read_text(encoding="utf-8")).get("token", "") or ""
        except Exception:
            token = ""
    if not token:
        token = _secrets.token_urlsafe(24)
        try:
            _token_file.parent.mkdir(parents=True, exist_ok=True)
            _token_file.write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")
            logger.warning("已生成 API 访问令牌并写入 config/api_token.json，座舱登录后才能调用交易接口")
        except Exception as e:
            logger.error(f"写入 API_TOKEN 失败: {e}")
    return token


API_TOKEN = _load_or_create_api_token()

_PUBLIC_PATHS = {
    '/', '/cockpit', '/cockpit.html', '/api/health', '/api/auth/login', '/api/auth/check',
    '/api/mobile/bind/claim',
    '/api/device/bind/claim',
    '/api/ths/auth/send-sms',
    '/api/ths/auth/login-sms',
    '/api/ths/auth/login-pwd',
    # 座舱态势展示只读端点 (确保大屏永不白屏)
    '/api/connection/info',
    '/api/connection/status',
    '/api/ai/scheduler/status',
    '/api/ai/scheduler/ledger/today',
    '/api/ai/scheduler/windows',
    '/api/stream/cockpit',
    '/api/competition/rules',
    '/api/competition/status',
    '/api/system/health/silent',
    '/api/system/performance',
}

# 双模脱敏端点：未登录时返回脱敏数据（资金***、持仓仅代码/名称），登录后返回完整数据
_MASKED_PATHS = {
    '/api/ai/candidates/today',
    '/api/ai/timing/overview',
}

_PUBLIC_PREFIXES = (
    '/static/', '/mobile', '/api/stock/', '/api/market/', '/api/sentiment/',
    '/api/macro/', '/api/risk/', '/api/audit/'
)

_QUICK_BIND_PATHS = {
    '/api/accounts/quick-bind',
    '/api/Accounts/quick-bind',
}


def _is_request_authenticated() -> bool:
    """检查当前请求是否携带有效 Token（仅 Authorization 头，不再接受 URL ?token=）。"""
    if not API_TOKEN:
        return True  # 鉴权未启用，视为已认证
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return False
    from device_manager import device_manager as dm
    if dm:
        payload = dm.verify_token(token)
        if payload:
            request.device = payload
            return True
    return token == API_TOKEN


def require_auth():
    """API 鉴权。quick-bind 必须携带一次性 ticket。"""
    if request.method == "OPTIONS":
        return None
    path = request.path
    if path.startswith('/static/') or path.endswith('.html') or path.endswith('.js') or path.endswith('.css'):
        return None
    if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
        return None
    if path in _QUICK_BIND_PATHS:
        ticket = request.args.get("ticket") or ""
        if request.is_json:
            ticket = ticket or (request.get_json(silent=True) or {}).get("ticket") or ""
        if not ticket:
            ticket = (request.form or {}).get("ticket") or ""
        from bind_ticket import consume_bind_ticket
        if consume_bind_ticket(ticket):
            return None
        return jsonify({"success": False, "message": "绑定需要一次性票据，请先登录后调用 /api/accounts/bind-ticket"}), 401
    if not API_TOKEN:
        return None
    if path in _PUBLIC_PATHS:
        return None

    # 双模脱敏端点：不拦截请求，但标记认证状态，由 handler 决定返回完整/脱敏数据
    if path in _MASKED_PATHS:
        request.authenticated = _is_request_authenticated()
        return None

    # 仅接受 Authorization 头传递 Token（废除 URL ?token= 传参，防止被日志/历史捕获）
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return jsonify({"success": False, "message": "未授权，请先登录"}), 401

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
