"""
REST API服务器
为前端提供股票查询、交易等接口
"""

from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import json
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
import os
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent / "src"))

from strategy import TradingEngine
from broker import THSBroker, AutoTrader
from config import config_manager
from ths_connector import web_connector, get_active_connector
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
from notification import notification_manager
from db_store import db_store
from account_manager import account_manager
from device_manager import init_device_manager

app = Flask(__name__, static_folder='static')
CORS(app)

# ==================== API 认证 ====================
# 通过环境变量 API_TOKEN 或 config/api_token.json 设置访问令牌
# 不设置则不启用认证（开发模式）
API_TOKEN = os.environ.get("API_TOKEN", "")
_token_file = Path(__file__).parent / "config" / "api_token.json"
if not API_TOKEN and _token_file.exists():
    try:
        API_TOKEN = json.loads(_token_file.read_text(encoding="utf-8")).get("token", "")
    except Exception:
        pass

# 免认证路径（静态资源、健康检查、认证本身、SSE流）
_PUBLIC_PATHS = {'/', '/api/health', '/api/auth/login',
                 '/api/mobile/notifications/stream',
                 '/api/mobile/bind/claim',
                 '/api/device/bind/claim'}

def require_auth():
    """检查 API 认证，未启用认证时直接放行"""
    if not API_TOKEN:
        return None  # 未设置令牌，不校验
    if request.path in _PUBLIC_PATHS:
        return None
    if request.path.startswith('/static/') or request.path.endswith('.html') or request.path.endswith('.js') or request.path.endswith('.css'):
        return None

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.args.get("token", "")

    # 新增：检查设备JWT
    from device_manager import device_manager as dm
    if dm and token:
        payload = dm.verify_token(token)
        if payload:
            request.device = payload  # 注入设备信息到request
            return None  # JWT有效，放行

    # 现有API_TOKEN检查
    if token != API_TOKEN:
        return jsonify({"success": False, "message": "未授权，请先登录"}), 401
    return None

@app.before_request
def _check_auth():
    return require_auth()

# 全局实例
engine = TradingEngine()
broker = THSBroker(mode="simulation")
trader = AutoTrader(broker)

# 设备管理器
_device_manager = init_device_manager(db_store)

# 交易模式: simulation / desktop / web
trading_mode = "simulation"

# 交易状态
trading_state = {
    "balance": 1000000,
    "positions": {},
    "trades": []
}

# 尝试导入akshare
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
            # 新格式: dict with English keys (from fast_data)
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
            # 旧格式: pandas Series with Chinese keys (akshare fallback)
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
    """
    计算真实技术指标（MA/RSI/MACD/布林带/ATR/ADX）
    使用缓存的K线数据（fast_data）
    """
    kline = get_kline(symbol, period="daily", days=120)
    if not kline or len(kline) < 20:
        return {}
    return compute_indicators(kline)


@app.after_request
def add_no_cache_headers(response):
    """禁止浏览器缓存HTML和JS，确保始终加载最新版本"""
    if request.path == '/' or request.path.endswith('.html') or request.path.endswith('.js'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


@app.route('/api/health', methods=['GET'])
def health_check():
    """轻量健康检查端点（部署状态检测）"""
    return jsonify({"success": True, "status": "ok"})


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """登录获取访问令牌"""
    data = request.json or {}
    token = data.get("token", "")
    if not API_TOKEN:
        return jsonify({"success": True, "message": "认证未启用", "auth_required": False})
    if token == API_TOKEN:
        return jsonify({"success": True, "message": "登录成功", "auth_required": True})
    return jsonify({"success": False, "message": "密码错误"}), 401


@app.route('/api/auth/check', methods=['GET'])
def auth_check():
    """检查是否需要认证"""
    return jsonify({"success": True, "data": {"auth_required": bool(API_TOKEN)}})


@app.route('/api/market/overview', methods=['GET'])
def market_overview():
    """获取市场概况（全市场数据）"""
    try:
        up_count = down_count = flat_count = total = 0
        sh_index = sz_index = 0.0

        # 全市场涨跌统计（用原始数据，不过滤）
        try:
            df = get_realtime_quote()
            if df is not None and not df.empty:
                up_count = int((df['涨跌幅'] > 0).sum())
                down_count = int((df['涨跌幅'] < 0).sum())
                flat_count = int((df['涨跌幅'] == 0).sum())
                total = len(df)
        except Exception:
            pass

        # 上证指数 + 深证成指
        try:
            from fast_data import fetch_tencent_realtime
            idx = fetch_tencent_realtime(["000001", "399001"])
            if "000001" in idx:
                sh_index = idx["000001"]["price"]
            if "399001" in idx:
                sz_index = idx["399001"]["price"]
        except Exception:
            pass

        return jsonify({
            "success": True,
            "data": {
                "sh_index": sh_index,
                "sz_index": sz_index,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "total": total
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/realtime/<symbol>', methods=['GET'])
def stock_realtime(symbol):
    """获取股票实时行情"""
    try:
        stock = get_stock_data(symbol)
        if stock:
            return jsonify({"success": True, "data": stock})
        return jsonify({"success": False, "message": f"未找到股票 {symbol}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/history/<symbol>', methods=['GET'])
def stock_history(symbol):
    """获取历史K线数据（使用 fast_data，带缓存）"""
    try:
        days = request.args.get('days', 30, type=int)

        data = get_kline(symbol, period="daily", days=days)
        if data:
            return jsonify({"success": True, "data": data})

        return jsonify({"success": False, "message": f"未找到 {symbol} K线数据"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/analyze/<symbol>', methods=['GET'])
def stock_analyze(symbol):
    """综合分析股票"""
    try:
        result = engine.analyze_stock(symbol)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/indicators/<symbol>', methods=['GET'])
def stock_indicators(symbol):
    """获取K线+技术指标（全缓存，含均线/MACD/RSI）"""
    try:
        days = request.args.get('days', 60, type=int)

        data = get_kline_with_indicators(symbol, days=days)
        if not data:
            return jsonify({"success": False, "message": f"未找到 {symbol} 数据"})

        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/intraday/<symbol>', methods=['GET'])
def stock_intraday(symbol):
    """获取分时数据（当日分钟级数据，带缓存）"""
    try:
        data = get_intraday(symbol)
        if data:
            return jsonify({"success": True, "data": data})

        return jsonify({"success": False, "message": f"未找到 {symbol} 分时数据"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/search', methods=['GET'])
def stock_search():
    """搜索股票（全市场）"""
    try:
        keyword = request.args.get('keyword', '').strip()
        limit = request.args.get('limit', 20, type=int)

        if not keyword:
            return jsonify({"success": True, "data": []})

        # 从全市场数据搜索
        try:
            df = get_realtime_quote()
            if df is not None and not df.empty:
                mask = df['代码'].str.contains(keyword, na=False) | df['名称'].str.contains(keyword, na=False)
                matched = df[mask].head(limit)
                result = []
                for _, row in matched.iterrows():
                    result.append({
                        "code": row['代码'],
                        "name": row['名称'],
                        "price": float(row['最新价']),
                        "change_pct": float(row['涨跌幅']),
                        "volume": int(row['成交量']),
                        "amount": float(row['成交额']),
                        "turnover": float(row['换手率']),
                    })
                return jsonify({"success": True, "data": result})
        except Exception:
            pass

        # fallback: 从比赛股票池搜索
        pool = competition_manager.get_stock_pool(limit=500)
        result = [s for s in pool if keyword in s['code'] or keyword in s['name']][:limit]
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/hot', methods=['GET'])
def hot_stocks():
    """获取比赛可交易股票池（按成交额排序）"""
    try:
        limit = request.args.get('limit', 50, type=int)
        sort_by = request.args.get('sort', 'amount', type=str)
        stocks = competition_manager.get_stock_pool(limit=limit, sort_by=sort_by)
        return jsonify({"success": True, "data": stocks})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def _validate_order(symbol: str, action: str, amount: int, price: float) -> dict:
    """
    下单前统一校验（防止平台报错）

    Returns: {"valid": True} 或 {"valid": False, "message": "错误原因"}
    """
    # 1. 基础参数检查
    if not symbol or len(symbol) != 6:
        return {"valid": False, "message": "股票代码必须是6位数字"}

    if amount <= 0:
        return {"valid": False, "message": "数量必须大于0"}

    if price is not None and price <= 0:
        return {"valid": False, "message": "价格必须大于0"}

    # 2. 整手检查 (100股整数倍)
    if amount % 100 != 0:
        return {"valid": False, "message": "买入数量必须是100的整数倍"}

    # 3. 连接检查
    connector, conn_type = get_active_connector()
    if not connector or not connector.connected:
        return {"valid": False, "message": "未连接交易系统，请先同步账户"}

    # 4. 获取账户信息
    try:
        fund_result = connector.get_fund()
        balance = fund_result.get("data", 0) if fund_result.get("success") else 0

        pos_result = connector.get_positions()
        positions = []
        total_market_value = 0
        if pos_result.get("success"):
            data = pos_result.get("data", {})
            if isinstance(data, dict):
                positions = data.get("positions", [])
                balance = data.get("balance", balance)
            elif isinstance(data, list):
                positions = data
            for p in positions:
                total_market_value += p.get("market_value", 0)

        total_assets = balance + total_market_value
    except Exception:
        total_assets = 1000000  # 默认值
        balance = 1000000

    # 5. 余额检查 (买入时)
    if action == "buy" and price:
        required = price * amount * 1.001  # 含佣金
        if required > balance:
            max_shares = int(balance / price / 1.001 / 100) * 100
            return {"valid": False, "message": f"余额不足。需要{required:.0f}元，可用{balance:.0f}元，最多买{max_shares}股"}

    # 6. 持仓检查 (卖出时)
    if action == "sell":
        held = 0
        for p in positions:
            if p.get("symbol") == symbol:
                held = p.get("amount", 0)
                break
        if amount > held:
            return {"valid": False, "message": f"持仓不足。持有{held}股，想卖{amount}股"}

    # 7. 比赛仓位限制 (单只不超过30%)
    if action == "buy" and price and total_assets > 0:
        buy_value = price * amount
        # 加上该股票已有持仓
        existing_value = 0
        for p in positions:
            if p.get("symbol") == symbol:
                existing_value = p.get("market_value", 0)
                break
        new_ratio = (existing_value + buy_value) / total_assets
        if new_ratio > 0.30:
            max_buy = int((total_assets * 0.30 - existing_value) / price / 100) * 100
            if max_buy < 100:
                return {"valid": False, "message": f"该股票仓位已达30%上限，无法继续买入"}
            return {"valid": False, "message": f"超过仓位限制30%。当前占比{new_ratio:.1%}，最多可再买{max_buy}股"}

    # 8. 涨跌停检查
    try:
        from fast_data import fetch_realtime_single
        quote = fetch_realtime_single(symbol)
        if quote:
            current_price = float(quote.get("price", 0))
            pre_close = float(quote.get("pre_close", 0))
            if pre_close > 0 and current_price > 0:
                change_pct = (current_price / pre_close - 1) * 100
                if action == "buy" and change_pct >= 9.9:
                    return {"valid": False, "message": f"涨停中(+{change_pct:.1f}%)，无法买入"}
                if action == "sell" and change_pct <= -9.9:
                    return {"valid": False, "message": f"跌停中({change_pct:.1f}%)，无法卖出"}
    except Exception:
        pass

    # 9. 比赛股票池检查
    try:
        if not competition_manager.is_tradable(symbol):
            return {"valid": False, "message": f"{symbol} 不在比赛允许的交易范围内"}
    except Exception:
        pass

    return {"valid": True}


@app.route('/api/trade/buy', methods=['POST'])
def trade_buy():
    """买入股票"""
    try:
        data = request.json
        symbol = data.get('symbol')
        amount = data.get('amount')
        price = data.get('price')

        if not symbol or not amount:
            return jsonify({"success": False, "message": "参数不完整"})

        # 统一校验
        validation = _validate_order(symbol, "buy", amount, price)
        if not validation["valid"]:
            return jsonify({"success": False, "message": validation["message"]})

        # 如果已连接同花顺网页版，使用真实API交易
        connector, conn_type = get_active_connector()
        if conn_type == "web" and connector.connected:
            result = connector.buy(symbol, price, amount)
            if result["success"]:
                # 同时保存到本地记录
                fund_result = connector.get_fund()
                balance = fund_result["data"] if fund_result["success"] else 0
                stock = get_stock_data(symbol)
                name = stock['name'] if stock else symbol
                trade = {
                    'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
                    'symbol': symbol,
                    'name': name,
                    'action': 'buy',
                    'amount': amount,
                    'price': price or 0,
                    'total': (price or 0) * amount,
                    'balance': balance,
                    'mode': 'ths_competition'
                }
                data_store.add_trade(trade)
                # 活动日志: 用户手动买入 (同花顺)
                try:
                    from activity_log import activity_log as _alog
                    _alog.log(action="buy", actor="user", symbol=symbol, name=name,
                              details={"price": price, "amount": amount, "total": (price or 0) * amount})
                except Exception:
                    pass
            return jsonify(result)

        # 本地模拟交易
        if price is None:
            stock = get_stock_data(symbol)
            if not stock:
                return jsonify({"success": False, "message": f"未找到股票 {symbol}"})
            price = stock['price']

        total_cost = price * amount
        balance = data_store.account["balance"]

        if total_cost > balance:
            return jsonify({"success": False, "message": "余额不足"})

        new_balance = balance - total_cost
        data_store.update_balance(new_balance)

        positions = data_store.positions
        if symbol in positions:
            pos = positions[symbol]
            pos['amount'] += amount
            pos['total_cost'] += total_cost
            pos['avg_price'] = pos['total_cost'] / pos['amount']
        else:
            stock = get_stock_data(symbol)
            name = stock['name'] if stock else symbol
            positions[symbol] = {
                'symbol': symbol,
                'name': name,
                'amount': amount,
                'total_cost': total_cost,
                'avg_price': price,
                'current_price': price
            }
        data_store.save_positions()

        trade = {
            'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': symbol,
            'name': positions[symbol].get('name', symbol),
            'action': 'buy',
            'amount': amount,
            'price': price,
            'total': total_cost,
            'balance': new_balance,
            'mode': 'simulation'
        }
        data_store.add_trade(trade)
        # 活动日志: 用户手动买入 (模拟)
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="buy", actor="user", symbol=symbol,
                      name=positions[symbol].get('name', symbol),
                      details={"price": price, "amount": amount, "total": total_cost})
        except Exception:
            pass

        return jsonify({
            "success": True,
            "message": "买入成功",
            "data": {
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "total": total_cost,
                "balance": new_balance
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/trade/sell', methods=['POST'])
def trade_sell():
    """卖出股票"""
    try:
        data = request.json
        symbol = data.get('symbol')
        amount = data.get('amount')
        price = data.get('price')

        if not symbol or not amount:
            return jsonify({"success": False, "message": "参数不完整"})

        # 统一校验
        validation = _validate_order(symbol, "sell", amount, price)
        if not validation["valid"]:
            return jsonify({"success": False, "message": validation["message"]})

        # 如果已连接同花顺网页版，使用真实API交易
        connector, conn_type = get_active_connector()
        if conn_type == "web" and connector.connected:
            # 从THS获取持仓以获取mkcode和gdzh
            pos_result = connector.get_positions()
            mkcode = ""
            gdzh = ""
            if pos_result["success"]:
                for p in pos_result["data"].get("positions", []):
                    if p["symbol"] == symbol:
                        mkcode = p.get("mkcode", "")
                        gdzh = p.get("gdzh", "")
                        break

            result = connector.sell(symbol, price, amount, mkcode=mkcode, gdzh=gdzh)
            if result["success"]:
                fund_result = connector.get_fund()
                balance = fund_result["data"] if fund_result["success"] else 0
                stock = get_stock_data(symbol)
                name = stock['name'] if stock else symbol
                trade = {
                    'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
                    'symbol': symbol,
                    'name': name,
                    'action': 'sell',
                    'amount': amount,
                    'price': price or 0,
                    'total': (price or 0) * amount,
                    'balance': balance,
                    'mode': 'ths_competition'
                }
                data_store.add_trade(trade)
                # 活动日志: 用户手动卖出 (同花顺)
                try:
                    from activity_log import activity_log as _alog
                    _alog.log(action="sell", actor="user", symbol=symbol, name=name,
                              details={"price": price, "amount": amount, "total": (price or 0) * amount})
                except Exception:
                    pass
            return jsonify(result)

        # 本地模拟交易
        positions = data_store.positions
        if symbol not in positions:
            return jsonify({"success": False, "message": f"未持有股票 {symbol}"})

        position = positions[symbol]
        if amount > position['amount']:
            return jsonify({"success": False, "message": "持仓不足"})

        if price is None:
            stock = get_stock_data(symbol)
            if not stock:
                return jsonify({"success": False, "message": f"未找到股票 {symbol}"})
            price = stock['price']

        total_income = price * amount
        profit = (price - position['avg_price']) * amount

        new_balance = data_store.account["balance"] + total_income
        data_store.update_balance(new_balance)

        position['amount'] -= amount
        if position['amount'] == 0:
            del positions[symbol]
        data_store.save_positions()

        trade = {
            'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': symbol,
            'name': position.get('name', symbol),
            'action': 'sell',
            'amount': amount,
            'price': price,
            'total': total_income,
            'profit': profit,
            'balance': new_balance,
            'mode': 'simulation'
        }
        data_store.add_trade(trade)
        # 活动日志: 用户手动卖出 (模拟)
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="sell", actor="user", symbol=symbol,
                      name=position.get('name', symbol),
                      details={"price": price, "amount": amount, "total": total_income, "profit": profit})
        except Exception:
            pass

        return jsonify({
            "success": True,
            "message": "卖出成功",
            "data": {
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "total": total_income,
                "profit": profit,
                "balance": new_balance
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/positions', methods=['GET'])
def get_positions():
    """获取持仓 — 网页API优先"""
    try:
        # 优先级1：全局web_connector
        if web_connector.connected and web_connector.cookie:
            result = web_connector.get_positions()
            if result["success"]:
                return jsonify({
                    "success": True,
                    "data": result["data"]
                })
            logger.warning(f"网页API获取持仓失败: {result.get('message')}")

        # 优先级2：通过 account_manager 的 trader 实时查询
        from account_manager import account_manager
        accounts = db_store.list_accounts()
        if accounts:
            active_acc = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])
            trader = account_manager.get_trader(active_acc["id"])
            if trader and trader.connected:
                result = trader.get_positions()
                if result["success"]:
                    return jsonify({
                        "success": True,
                        "data": result["data"]
                    })

        # 最终回退：本地模拟数据
        if not accounts:
            return jsonify({
                "success": True,
                "data": {"balance": 0, "market_value": 0, "total_assets": 0, "positions": []}
            })

        # 本地模拟持仓
        positions = []
        total_market_value = 0

        saved_positions = data_store.positions
        balance = data_store.account.get("balance", 0)

        for symbol, pos in saved_positions.items():
            stock = get_stock_data(symbol)
            if stock:
                current_price = stock['price']
                name = stock['name']
            else:
                current_price = pos.get('current_price', pos.get('avg_price', 0))
                name = pos.get('name', symbol)

            market_value = current_price * pos['amount']
            profit = (current_price - pos['avg_price']) * pos['amount']
            profit_rate = (current_price / pos['avg_price'] - 1) * 100 if pos['avg_price'] > 0 else 0

            total_market_value += market_value

            positions.append({
                "symbol": symbol,
                "name": name,
                "amount": pos['amount'],
                "avg_price": round(pos['avg_price'], 2),
                "current_price": round(current_price, 2),
                "market_value": round(market_value, 2),
                "profit": round(profit, 2),
                "profit_rate": round(profit_rate, 2)
            })

        total_assets = balance + total_market_value
        return jsonify({
            "success": True,
            "data": {
                "balance": round(balance, 2),
                "market_value": round(total_market_value, 2),
                "total_assets": round(total_assets, 2),
                "initial_balance": 1000000,
                "positions": positions
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/balance', methods=['GET'])
def get_balance():
    """获取余额 — 网页API优先"""
    try:
        # 优先级1：全局web_connector
        if web_connector.connected and web_connector.cookie:
            result = web_connector.get_positions()
            if result["success"]:
                fund = result["data"].get("fund", {})
                balance = fund.get("available", 0)
                total = fund.get("total_assets", 0)
                market_val = fund.get("market_value", 0)
                return jsonify({
                    "success": True,
                    "data": {
                        "balance": round(float(balance), 2),
                        "market_value": round(float(market_val), 2),
                        "total_assets": round(float(total), 2),
                        "raw": fund
                    }
                })
            # 网页API失败，尝试只获取资金
            fund_result = web_connector.get_fund()
            if fund_result["success"]:
                return jsonify({
                    "success": True,
                    "data": {
                        "balance": round(float(fund_result["data"]), 2),
                        "market_value": 0,
                        "total_assets": round(float(fund_result["data"]), 2),
                    }
                })

        # 优先级2：通过 account_manager 的 trader 实时查询
        from account_manager import account_manager
        accounts = db_store.list_accounts()
        if accounts:
            active_acc = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])
            trader = account_manager.get_trader(active_acc["id"])
            if trader and trader.connected:
                pos_result = trader.get_positions()
                if pos_result["success"]:
                    fund = pos_result["data"].get("fund", {})
                    balance = fund.get("available", 0)
                    total = fund.get("total_assets", 0)
                    market_val = fund.get("market_value", 0)
                    return jsonify({
                        "success": True,
                        "data": {
                            "balance": round(float(balance), 2),
                            "market_value": round(float(market_val), 2),
                            "total_assets": round(float(total), 2),
                        }
                    })

        # 最终回退：DB缓存数据（不准确，仅供参考）
        if not accounts:
            return jsonify({"success": False, "message": "未绑定任何账户"})

        active = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])

        # --- 从持仓实时计算市值（与positions接口一致） ---
        # ky_fund = THS实际可用资金（优先），balance = 本地扣减后的余额
        balance = active.get("ky_fund") or active.get("balance", 0)
        saved_positions = data_store.positions
        total_market_value = 0

        for symbol, pos in saved_positions.items():
            stock = get_stock_data(symbol)
            if stock:
                current_price = stock['price']
            else:
                current_price = pos.get('current_price', pos.get('avg_price', 0))
            total_market_value += current_price * pos['amount']

        total_assets = balance + total_market_value

        return jsonify({
            "success": True,
            "data": {
                "balance": round(balance, 2),
                "market_value": round(total_market_value, 2),
                "total_assets": round(total_assets, 2),
                "position_count": len(saved_positions)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/offsets', methods=['GET'])
def get_account_offsets():
    """查看各账户的策略偏移（验证差异化）"""
    try:
        from ai_brain import ai_brain
        accounts = db_store.list_accounts()
        result = {}
        for acc in accounts:
            acc_id = acc["id"]
            offsets = ai_brain._get_offsets(acc_id)
            seed = ai_brain._account_seeds.get(acc_id, None)
            result[acc_id] = {
                "name": acc.get("name", f"user_{acc_id}"),
                "seed": seed,
                "offsets": {k: round(v, 4) for k, v in offsets.items()} if offsets else {},
            }
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/trades', methods=['GET'])
def get_trades():
    """获取交易记录 — 优先走网页API"""
    try:
        limit = request.args.get('limit', 50, type=int)
        from ths_connector import web_connector

        # 优先级1：网页API
        if web_connector.connected and web_connector.cookie:
            result = web_connector.get_orders()
            if result["success"]:
                return jsonify({
                    "success": True,
                    "data": {
                        "trades": result["data"],
                        "total": len(result["data"])
                    }
                })

        # 本地模拟交易记录
        all_trades = data_store.get_trades(limit)

        return jsonify({
            "success": True,
            "data": {
                "trades": all_trades,
                "total": len(data_store.trades)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/pending-orders', methods=['GET'])
def get_pending_orders():
    """获取当日未成交委托"""
    try:
        # 优先级1：全局web_connector
        if web_connector.connected and web_connector.cookie:
            result = web_connector.get_orders()
            if result.get("success"):
                orders = [o for o in result.get("data", []) if o.get("status") in ("未成交", "部分成交", "已报")]
                return jsonify({"success": True, "data": orders})

        # 优先级2：通过 account_manager 的 trader 实时查询
        from account_manager import account_manager
        accounts = db_store.list_accounts()
        if accounts:
            active_acc = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])
            trader = account_manager.get_trader(active_acc["id"])
            if trader and trader.connected:
                result = trader.get_orders()
                if result.get("success"):
                    orders = [o for o in result.get("data", []) if o.get("status") in ("未成交", "部分成交", "已报")]
                    return jsonify({"success": True, "data": orders})

        return jsonify({"success": True, "data": []})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/cancel-order', methods=['POST'])
def cancel_order():
    """撤单"""
    try:
        data = request.json or {}
        order_id = data.get("order_id", "")
        date = data.get("date", "")
        if not order_id:
            return jsonify({"success": False, "message": "缺少 order_id"})
        conn, _ = _get_active_conn()
        if not conn:
            return jsonify({"success": False, "message": "未连接交易系统"})
        if not hasattr(conn, 'cancel_order'):
            return jsonify({"success": False, "message": "当前连接器不支持撤单"})
        result = conn.cancel_order(order_id, date)
        # 推送撤单通知
        if result.get("success"):
            try:
                notification_manager.add({
                    "type": "trade", "level": "warning",
                    "title": "🚫 撤单成功",
                    "message": f"委托 {order_id} 已撤销",
                })
            except Exception:
                pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/account/cancel-all', methods=['POST'])
def cancel_all_orders():
    """撤销所有未成交委托"""
    try:
        conn, _ = _get_active_conn()
        if not conn:
            return jsonify({"success": False, "message": "未连接交易系统"})
        if not hasattr(conn, 'cancel_all_orders'):
            return jsonify({"success": False, "message": "当前连接器不支持撤单"})
        result = conn.cancel_all_orders()
        if result.get("success"):
            try:
                notification_manager.add({
                    "type": "trade", "level": "warning",
                    "title": "🚫 批量撤单",
                    "message": result.get("message", "撤单完成"),
                })
            except Exception:
                pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 账户连接 API ====================

@app.route('/api/connection/status', methods=['GET'])
def connection_status():
    """获取连接状态"""
    connector, conn_type = get_active_connector()
    return jsonify({
        "success": True,
        "data": {
            "connected": connector is not None,
            "type": conn_type,
            "mode": trading_mode
        }
    })


@app.route('/api/connection/desktop', methods=['POST'])
def connect_desktop():
    """[已废弃] 桌面客户端连接已移除，请使用Cookie登录"""
    return jsonify({"success": False, "message": "桌面客户端已废弃，请使用Cookie登录"})


@app.route('/api/connection/auto', methods=['POST'])
def auto_connect():
    """[已废弃] 桌面客户端自动连接已移除，请使用Cookie登录"""
    return jsonify({"success": False, "message": "桌面客户端已废弃，请使用Cookie登录"})


@app.route('/api/connection/credentials', methods=['POST'])
def save_credentials():
    """[已废弃] 桌面客户端凭据已移除"""
    return jsonify({"success": False, "message": "桌面客户端已废弃，请使用Cookie登录"})


@app.route('/api/connection/credentials', methods=['GET'])
def get_credentials_status():
    """[已废弃] 桌面客户端凭据已移除"""
    return jsonify({"success": True, "has_credentials": False})


@app.route('/api/connection/info', methods=['GET'])
def connection_info():
    """获取连接状态详情"""
    try:
        from ths_connector import get_connection_info
        return jsonify(get_connection_info())
    except Exception as e:
        return jsonify({"mode": "none", "connected": False, "message": str(e)})


@app.route('/api/connection/web/save-cookie', methods=['POST'])
def save_web_cookie():
    """保存网页Cookie并连接同花顺模拟比赛"""
    global trading_mode

    try:
        data = request.json
        cookie = data.get('cookie', '')
        name = data.get('name', '')  # 可选：用户指定的账户名

        if not cookie:
            return jsonify({"success": False, "message": "请输入Cookie"})

        result = web_connector.save_cookie(cookie)

        if result['success']:
            trading_mode = "web"

            # 同时将账户写入数据库，确保在账户列表中可见
            account_result = _ensure_account_in_db(cookie, name)

            return jsonify({
                "success": True,
                "message": result['message'],
                "data": {
                    "mode": "ths_competition",
                    "balance": web_connector.ky_fund,
                    "account": account_result
                }
            })
        else:
            return jsonify({"success": False, "message": result['message']})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def _ensure_account_in_db(cookie: str, name: str = "") -> Dict:
    """
    确保Cookie对应的账户已写入数据库
    - 已存在则跳过，返回已有账户信息
    - 不存在则自动从同花顺获取用户名/比赛名，创建新账户
    """
    try:
        from db_store import db_store

        # 检查是否已绑定过相同Cookie
        existing_accounts = db_store.list_accounts()
        for acc in existing_accounts:
            old_cookie = db_store.get_account_cookie(acc["id"])
            if old_cookie and old_cookie == cookie:
                # 已存在，直接返回
                logger.info(f"Cookie已绑定: {acc['name']} (ID={acc['id']})")
                return {"id": acc["id"], "name": acc["name"], "status": "exists"}

        # 未找到，自动从同花顺获取用户信息来命名
        if not name:
            name = _fetch_ths_display_name(cookie, len(existing_accounts))

        result = account_manager.add_account(name, cookie)
        if result.get("success"):
            acc_data = result.get("data", {})
            logger.info(f"Cookie保存后自动创建账户: {name} (ID={acc_data.get('id')})")
            return {"id": acc_data.get("id"), "name": name, "status": "created"}
        else:
            logger.warning(f"Cookie保存后创建账户失败: {result.get('message')}")
            return {"status": "error", "message": result.get("message", "创建失败")}

    except Exception as e:
        logger.error(f"确保账户入库失败: {e}")
        return {"status": "error", "message": str(e)}


def _fetch_ths_display_name(cookie: str, existing_count: int) -> str:
    """
    从同花顺API获取用户名/比赛名，生成显示名称

    优先级: 用户昵称 > 比赛名+昵称 > 比赛名 > "账户N"
    """
    try:
        from ths_connector import THSWebConnector
        tmp = THSWebConnector()
        tmp.cookie = cookie

        info_result = tmp.get_user_info()
        if info_result.get("success"):
            info = info_result.get("data", {})
            username = info.get("username", "").strip()
            comp_name = info.get("competition_name", "").strip()

            if username and comp_name:
                return f"{comp_name}-{username}"
            elif username:
                return username
            elif comp_name:
                return comp_name

        # fallback: 用 gdzh（资金账号）
        if tmp.gdzh:
            return f"账号{tmp.gdzh}"

    except Exception as e:
        logger.debug(f"获取THS用户信息失败: {e}")

    # 最终兜底
    return f"账户{existing_count + 1}"


@app.route('/api/connection/web/auto-get', methods=['POST'])
def auto_get_cookie():
    """自动获取Cookie（在现有浏览器中打开新标签页）"""
    global trading_mode

    try:
        import threading
        import webbrowser
        import time

        def open_ths_page():
            # 在用户的默认浏览器中打开同花顺页面
            webbrowser.open("https://moni.10jqka.com.cn/")

        # 在新线程中打开
        thread = threading.Thread(target=open_ths_page)
        thread.daemon = True
        thread.start()

        return jsonify({
            "success": True,
            "message": "已在浏览器中打开同花顺页面，请登录后复制Cookie"
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/connection/web/load', methods=['POST'])
def load_web_cookie():
    """加载已保存的Cookie"""
    global trading_mode

    try:
        result = web_connector.load_cookie()

        if result['success']:
            trading_mode = "web"
            return jsonify({
                "success": True,
                "message": "Cookie加载成功",
                "save_time": result.get('save_time')
            })
        else:
            return jsonify({"success": False, "message": result['message']})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/connection/disconnect', methods=['POST'])
def disconnect():
    """断开连接"""
    global trading_mode

    web_connector.connected = False
    trading_mode = "simulation"

    return jsonify({"success": True, "message": "已断开连接，切换到模拟模式"})


@app.route('/api/connection/web/query-stock/<symbol>', methods=['GET'])
def query_ths_stock(symbol):
    """通过同花顺API查询股票信息（价格、五档盘口）"""
    try:
        if not web_connector.connected:
            return jsonify({"success": False, "message": "未连接同花顺"})

        action = request.args.get('action', 'buy')
        result = web_connector.qry_stock(symbol, action)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/connection/web/summary', methods=['GET'])
def ths_account_summary():
    """获取同花顺账户完整摘要"""
    try:
        if not web_connector.connected:
            return jsonify({"success": False, "message": "未连接同花顺"})

        result = web_connector.get_account_summary()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/connection/web/validate', methods=['GET'])
def validate_web_cookie():
    """验证Cookie有效性并返回账户信息"""
    try:
        if not web_connector.cookie:
            return jsonify({"success": False, "valid": False, "message": "未设置Cookie"})

        result = web_connector.get_fund()
        if result["success"]:
            return jsonify({
                "success": True,
                "valid": True,
                "message": "Cookie有效",
                "data": {
                    "balance": web_connector.ky_fund,
                    "gdzh": web_connector.gdzh
                }
            })
        else:
            return jsonify({
                "success": True,
                "valid": False,
                "message": f"Cookie已过期: {result['message']}"
            })
    except Exception as e:
        return jsonify({"success": False, "valid": False, "message": str(e)})


@app.route('/api/connection/web/refresh', methods=['POST'])
def refresh_web_cookie():
    """刷新Cookie验证状态，重新获取账户数据"""
    global trading_mode

    try:
        if not web_connector.cookie:
            return jsonify({"success": False, "message": "未设置Cookie，请先登录"})

        # 重新验证Cookie
        result = web_connector.get_fund()
        if result["success"]:
            # 获取完整账户信息
            summary = web_connector.get_account_summary()
            account_data = summary.get("data", {}) if summary["success"] else {}

            return jsonify({
                "success": True,
                "message": "刷新成功",
                "data": {
                    "balance": web_connector.ky_fund,
                    "total_assets": account_data.get("total_assets", web_connector.ky_fund),
                    "market_value": account_data.get("market_value", 0),
                    "total_profit": account_data.get("total_profit", 0),
                    "positions_count": len(account_data.get("positions", [])),
                    "orders_count": len(account_data.get("orders", []))
                }
            })
        else:
            # Cookie已过期，断开连接
            web_connector.connected = False
            trading_mode = "simulation"
            return jsonify({
                "success": False,
                "message": f"Cookie已过期，请重新登录: {result['message']}"
            })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== Cookie 健康监控 ====================

_cookie_health = {
    "last_check": None,       # 上次检查时间
    "last_valid": None,       # 上次验证成功时间
    "last_refresh": None,     # 上次自动刷新时间
    "consecutive_fails": 0,   # 连续失败次数
    "status": "unknown",      # unknown / valid / expired / error
}


# ========== 扫码登录 API (已废弃，保留兼容) ==========

@app.route('/api/ths/qrcode/generate', methods=['POST'])
def qr_generate():
    """生成同花顺扫码登录二维码"""
    try:
        from ths_connector import qr_login_manager
        result = qr_login_manager.generate_qrcode()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ths/qrcode/poll', methods=['GET'])
def qr_poll():
    """轮询扫码登录状态"""
    try:
        qrcode_id = request.args.get('qrcode_id', '')
        from ths_connector import qr_login_manager
        result = qr_login_manager.poll_status(qrcode_id)
        # 如果扫码成功，自动保存Cookie
        if result.get('success') and result.get('status') == 'confirmed':
            cookie = result.get('cookie', '')
            if cookie:
                save_result = qr_login_manager.validate_and_save_cookie(cookie)
                result['cookie_saved'] = save_result.get('success', False)
                result['save_message'] = save_result.get('message', '')
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "status": "error", "message": str(e)})


@app.route('/api/ths/qrcode/cancel', methods=['POST'])
def qr_cancel():
    """取消二维码"""
    try:
        data = request.json or {}
        qrcode_id = data.get('qrcode_id', '')
        from ths_connector import qr_login_manager
        qr_login_manager.cancel_qrcode(qrcode_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": True})


@app.route('/api/ths/qrcode/auto-load', methods=['POST'])
def qr_auto_load():
    """尝试自动加载已保存的扫码登录Cookie"""
    global trading_mode
    try:
        from ths_connector import qr_login_manager
        result = qr_login_manager.load_qr_cookie()
        if result.get('success'):
            trading_mode = "web"
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cookie/health', methods=['GET'])
def cookie_health_check():
    """
    Cookie 健康检查 (主动调用 or 后台定时)
    返回: status, last_valid, last_refresh, consecutive_fails, message
    """
    try:
        from datetime import datetime as _dt
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        _cookie_health["last_check"] = now

        if not web_connector.cookie:
            _cookie_health["status"] = "no_cookie"
            return jsonify({
                "success": True,
                "data": {
                    "status": "no_cookie",
                    "message": "未设置Cookie",
                    "last_valid": _cookie_health["last_valid"],
                    "last_refresh": _cookie_health.get("last_refresh"),
                    "consecutive_fails": 0,
                }
            })

        result = web_connector.get_fund()
        if result["success"]:
            _cookie_health["status"] = "valid"
            _cookie_health["last_valid"] = now
            _cookie_health["last_refresh"] = now
            _cookie_health["consecutive_fails"] = 0
            return jsonify({
                "success": True,
                "data": {
                    "status": "valid",
                    "message": f"Cookie有效 (余额: {web_connector.ky_fund})",
                    "balance": web_connector.ky_fund,
                    "last_valid": now,
                    "last_refresh": now,
                    "consecutive_fails": 0,
                }
            })
        else:
            _cookie_health["consecutive_fails"] += 1
            _cookie_health["status"] = "expired"
            # 断开连接
            web_connector.connected = False
            return jsonify({
                "success": True,
                "data": {
                    "status": "expired",
                    "message": f"Cookie已过期: {result.get('message', '')}",
                    "last_valid": _cookie_health["last_valid"],
                    "last_refresh": _cookie_health.get("last_refresh"),
                    "consecutive_fails": _cookie_health["consecutive_fails"],
                }
            })
    except Exception as e:
        _cookie_health["status"] = "error"
        return jsonify({"success": False, "message": str(e)})


# 后台定时健康检查 + 自动刷新线程
_cookie_monitor_thread = None
_cookie_monitor_running = False

# 自动刷新间隔（秒）：每2小时刷新一次，保持会话不过期
_COOKIE_REFRESH_INTERVAL = 2 * 3600  # 2小时
_COOKIE_CHECK_INTERVAL = 30 * 60     # 30分钟检查一次


def _cookie_refresh():
    """
    主动刷新Cookie：在Cookie有效期内定期访问THS服务器保持会话活跃。
    类似浏览器保持登录状态的方式 — 定期发请求让服务器知道你还在。
    """
    global _cookie_health
    from datetime import datetime as _dt

    if not web_connector.cookie or not web_connector.connected:
        return

    try:
        result = web_connector.get_fund()
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        _cookie_health["last_check"] = now
        _cookie_health["last_refresh"] = now

        if result.get("success"):
            _cookie_health["status"] = "valid"
            _cookie_health["last_valid"] = now
            _cookie_health["consecutive_fails"] = 0
            logger.info(f"✅ Cookie自动刷新成功 (余额: {result.get('data', '?')})")
        else:
            _cookie_health["consecutive_fails"] += 1
            if _cookie_health["consecutive_fails"] >= 2:
                _cookie_health["status"] = "expired"
                web_connector.connected = False
                logger.warning(f"⚠ Cookie刷新失败，已过期! 连续失败: {_cookie_health['consecutive_fails']}次")
            else:
                logger.warning(f"Cookie刷新暂时失败: {result.get('message', '?')} (等待下次重试)")
    except Exception as e:
        logger.error(f"Cookie刷新异常: {e}")


def _cookie_monitor_loop():
    """
    Cookie后台监控：
    - 每30分钟检查一次健康状态
    - 每2小时主动刷新一次（保持会话活跃，防止过期）
    """
    import time as _time
    global _cookie_monitor_running
    _cookie_monitor_running = True
    logger.info(f"Cookie健康监控已启动 (检查: {_COOKIE_CHECK_INTERVAL//60}分钟, 自动刷新: {_COOKIE_REFRESH_INTERVAL//3600}小时)")

    last_refresh_time = 0

    while _cookie_monitor_running:
        _time.sleep(_COOKIE_CHECK_INTERVAL)  # 30分钟
        if not _cookie_monitor_running:
            break

        now = _time.time()

        # 检查是否需要自动刷新（每2小时）
        if now - last_refresh_time >= _COOKIE_REFRESH_INTERVAL:
            if web_connector.cookie and web_connector.connected:
                logger.info("执行Cookie自动刷新...")
                _cookie_refresh()
                last_refresh_time = now
            elif web_connector.cookie and not web_connector.connected:
                # Cookie存在但标记为断开，尝试重新验证
                logger.info("尝试重新验证Cookie...")
                _cookie_refresh()
                last_refresh_time = now

        # 也执行一次健康检查（前端可查看状态）
        try:
            with app.test_request_context():
                with app.test_client() as client:
                    resp = client.get('/api/cookie/health')
                    data = resp.get_json()
                    if data and data.get("data", {}).get("status") == "expired":
                        logger.warning(f"⚠ Cookie已过期! 连续失败: {_cookie_health['consecutive_fails']}次")
        except Exception as e:
            logger.error(f"Cookie健康检查异常: {e}")


def start_cookie_monitor():
    """启动Cookie健康监控 + 自动刷新"""
    global _cookie_monitor_thread
    if _cookie_monitor_thread and _cookie_monitor_thread.is_alive():
        return
    _cookie_monitor_thread = threading.Thread(target=_cookie_monitor_loop, daemon=True)
    _cookie_monitor_thread.start()


# 模块加载时启动监控
try:
    start_cookie_monitor()
except Exception:
    pass


@app.route('/api/connection/web/account-detail', methods=['GET'])
def web_account_detail():
    """获取网页连接的详细账户信息（用户名、余额、持仓、委托）"""
    try:
        if not web_connector.connected:
            return jsonify({"success": False, "message": "未连接同花顺"})

        summary = web_connector.get_account_summary()
        if not summary["success"]:
            return jsonify(summary)

        data = summary["data"]

        # 从Cookie中解析用户名
        username = ""
        cookie = web_connector.cookie or ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("u_name="):
                try:
                    import urllib.parse
                    raw = part.split("=", 1)[1]
                    # 同花顺 u_name 是 GBK 编码的 percent-encoded 字符串
                    decoded_latin = urllib.parse.unquote(raw, encoding='latin-1')
                    username = decoded_latin.encode('latin-1').decode('gbk', errors='replace')
                except Exception:
                    username = part.split("=", 1)[1]
                break

        return jsonify({
            "success": True,
            "data": {
                "username": username,
                "balance": data.get("balance", 0),
                "total_assets": data.get("total_assets", 0),
                "market_value": data.get("market_value", 0),
                "total_profit": data.get("total_profit", 0),
                "positions": data.get("positions", []),
                "orders": data.get("orders", []),
                "positions_count": len(data.get("positions", [])),
                "orders_count": len(data.get("orders", []))
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== AI 分析 API ====================

@app.route('/api/ai/check', methods=['GET'])
def ai_check():
    """检查AI是否可用"""
    return jsonify({
        "success": True,
        "data": {"connected": ai_connector.connected}
    })


@app.route('/api/ai/config', methods=['GET'])
def ai_config_get():
    """获取AI配置"""
    return jsonify(ai_connector.get_config())


@app.route('/api/ai/config', methods=['POST'])
def ai_config_update():
    """更新AI配置"""
    try:
        data = request.json
        result = ai_connector.update_config(**data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/providers', methods=['GET'])
def ai_providers_get():
    """获取所有providers（含模型列表）"""
    return jsonify({"success": True, "data": ai_connector.get_providers()})


@app.route('/api/ai/providers', methods=['POST'])
def ai_providers_update():
    """整体替换providers"""
    try:
        data = request.json
        result = ai_connector.update_providers(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/providers/<key>', methods=['PUT'])
def ai_provider_save(key):
    """添加/更新单个provider"""
    try:
        data = request.json
        result = ai_connector.add_provider(key, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/providers/<key>', methods=['DELETE'])
def ai_provider_delete(key):
    """删除provider"""
    result = ai_connector.delete_provider(key)
    return jsonify(result)


@app.route('/api/ai/token-stats', methods=['GET'])
def ai_token_stats():
    """AI token 使用统计"""
    return jsonify({"success": True, "data": ai_connector.get_token_stats()})


# ==================== AI 自动交易调度器 ====================

@app.route('/api/ai/scheduler/status', methods=['GET'])
def ai_scheduler_status():
    """获取AI调度器状态"""
    try:
        from ai_scheduler import ai_scheduler
        return jsonify({"success": True, "data": ai_scheduler.get_status()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/scheduler/toggle', methods=['POST'])
def ai_scheduler_toggle():
    """启用/禁用AI自动交易调度器"""
    try:
        from ai_scheduler import ai_scheduler
        data = request.json or {}
        enabled = data.get("enabled", not ai_scheduler.enabled)
        ai_scheduler.enabled = enabled
        ai_scheduler.save_config()
        if enabled and not ai_scheduler._running:
            ai_scheduler.start()
        logger.info(f"AI调度器 {'启用' if enabled else '禁用'}")
        return jsonify({"success": True, "data": ai_scheduler.get_status()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/scheduler/config', methods=['POST'])
def ai_scheduler_config():
    """更新AI调度器配置"""
    try:
        from ai_scheduler import ai_scheduler
        data = request.json or {}
        if "interval_minutes" in data:
            ai_scheduler.interval_minutes = max(5, int(data["interval_minutes"]))
        if "max_trades_per_day" in data:
            ai_scheduler.max_trades_per_day = max(1, int(data["max_trades_per_day"]))
        if "position_limit_pct" in data:
            ai_scheduler.position_limit_pct = max(0.05, min(1.0, float(data["position_limit_pct"])))
        if "scan_top_n" in data:
            ai_scheduler.scan_top_n = max(1, int(data["scan_top_n"]))
        ai_scheduler.save_config()
        return jsonify({"success": True, "data": ai_scheduler.get_status()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/scheduler/run-now', methods=['POST'])
def ai_scheduler_run_now():
    """立即执行一次AI分析"""
    try:
        from ai_scheduler import ai_scheduler
        import threading
        threading.Thread(target=ai_scheduler._run_analysis, daemon=True).start()
        return jsonify({"success": True, "message": "AI分析已触发"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/scheduler/analyze-detail', methods=['POST'])
def ai_scheduler_analyze_detail():
    """立即分析（SSE流式推送进度）"""
    import traceback

    def generate():
        steps = []
        try:
            # Step 1: 检查连接
            steps.append({"step": "检查THS连接", "status": "running", "progress": 10})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            from ths_connector import get_active_connector
            conn, conn_type = get_active_connector()
            connected = bool(conn and getattr(conn, 'connected', False))
            steps[-1] = {"step": "检查THS连接", "status": "ok" if connected else "error",
                         "detail": f"连接类型: {conn_type}", "progress": 15}
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            if not connected:
                yield f"data: {json.dumps({'type': 'done', 'success': False, 'message': 'THS未连接', 'steps': steps})}\n\n"
                return

            # Step 2: 处理未成交委托
            steps.append({"step": "处理未成交委托", "status": "running", "progress": 20})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            from ai_scheduler import ai_scheduler
            try:
                ai_scheduler._handle_pending_orders()
                pending = len(getattr(ai_scheduler, '_cached_pending_orders', []))
                steps[-1] = {"step": "处理未成交委托", "status": "ok",
                             "detail": f"{pending}笔未成交委托", "progress": 25}
            except Exception as e:
                steps[-1] = {"step": "处理未成交委托", "status": "warn", "detail": str(e), "progress": 25}
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"

            # Step 3: 获取股票池
            steps.append({"step": "获取股票池", "status": "running", "progress": 30})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            from competition import competition_manager
            stocks = competition_manager.get_stock_pool(sort_by="change_pct", limit=100)
            steps[-1] = {"step": "获取股票池", "status": "ok" if stocks else "warn",
                         "detail": f"获取到 {len(stocks or [])} 只股票", "progress": 40}
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"

            # Step 4: 策略信号
            steps.append({"step": "计算策略信号", "status": "running", "progress": 45})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            from ai_brain import ai_brain
            from data_cache import get_realtime_quote, compute_indicators as calculate_indicators
            from auto_trader import auto_trader
            strategy_signals = []
            stock_list = (stocks or [])[:50]
            for i, stock in enumerate(stock_list):
                symbol = stock.get("code", "")
                if not symbol or symbol in auto_trader.positions:
                    continue
                try:
                    stock_data = get_realtime_quote(symbol)
                    if not stock_data:
                        continue
                    indicators = calculate_indicators(symbol) or {"close": stock_data.get("price", 0)}
                    decision = ai_brain.decide(
                        symbol=symbol, stock_data=stock_data,
                        indicators=indicators,
                        account_info=ai_scheduler._get_account_info()
                    )
                    if decision.action != "hold":
                        strategy_signals.append({
                            "symbol": symbol,
                            "name": stock_data.get("name", ""),
                            "action": decision.action,
                            "confidence": round(decision.confidence, 2),
                        })
                except Exception:
                    pass
                # 每分析10只股票推送一次进度
                if (i + 1) % 10 == 0:
                    pct = 45 + int((i + 1) / len(stock_list) * 25)
                    yield f"data: {json.dumps({'type': 'progress', 'progress': min(pct, 70), 'detail': f'已分析{i+1}/{len(stock_list)}只'})}\n\n"
            steps[-1] = {"step": "计算策略信号", "status": "ok",
                         "detail": f"分析{len(stock_list)}只, 产生{len(strategy_signals)}个信号", "progress": 70}
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"

            # Step 5: AI深度分析
            steps.append({"step": "AI深度分析", "status": "running", "progress": 75})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            from ai_connector import ai_connector
            use_ai = ai_connector.connected
            if use_ai:
                trades = ai_scheduler._decide_with_ai(stocks or [])
                steps[-1] = {"step": "AI深度分析", "status": "ok",
                             "detail": f"AI模式, 产生{len(trades)}笔交易信号", "progress": 90}
            else:
                trades = ai_scheduler._decide_with_rules(stocks or [])
                steps[-1] = {"step": "规则引擎决策", "status": "ok",
                             "detail": f"规则引擎模式, 产生{len(trades)}笔交易信号", "progress": 90}
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"

            # Step 6: 执行交易
            if trades:
                steps.append({"step": "执行交易", "status": "running", "progress": 95})
                yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
                try:
                    ai_scheduler._execute_trades(trades)
                    steps[-1] = {"step": "执行交易", "status": "ok",
                                 "detail": f"执行了{len(trades)}笔交易", "progress": 100}
                except Exception as e:
                    steps[-1] = {"step": "执行交易", "status": "error", "detail": str(e), "progress": 100}
            else:
                steps.append({"step": "执行交易", "status": "skip", "detail": "无交易信号", "progress": 100})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"

            pass
            logger.info(f"=== 分析完成: {len(steps)}步, {len(trades if trades else [])}笔交易 ===")
            yield f"data: {json.dumps({'type': 'done', 'success': True, 'steps': steps, 'trades': trades if trades else []})}\n\n"
        except TimeoutError:
            pass
            logger.error("分析超时")
            yield f"data: {json.dumps({'type': 'done', 'success': False, 'message': '分析超时(120秒)', 'steps': steps})}\n\n"
        except Exception as e:
            pass
            logger.error(f"分析异常: {traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'done', 'success': False, 'message': str(e), 'steps': steps})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/test', methods=['GET'])
def test_endpoint():
    """测试端点"""
    try:
        from ths_connector import get_active_connector
        conn, conn_type = get_active_connector()
        return jsonify({"success": True, "connected": bool(conn and getattr(conn, 'connected', False)), "type": conn_type})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 活动日志 ====================

@app.route('/api/activity/log', methods=['POST'])
def activity_log_add():
    """记录一条活动日志"""
    try:
        from activity_log import activity_log
        data = request.json or {}
        result = activity_log.log(
            action=data.get("action", "config_change"),
            actor=data.get("actor", "user"),
            symbol=data.get("symbol", ""),
            name=data.get("name", ""),
            details=data.get("details"),
            result=data.get("result", "success"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/activity/logs', methods=['GET'])
def activity_logs_get():
    """查询活动日志"""
    try:
        from activity_log import activity_log
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)
        action = request.args.get("action")
        actor = request.args.get("actor")
        symbol = request.args.get("symbol")

        # 获取当前活跃账户
        active_account = request.args.get("account_id", None, type=int)
        if active_account is None:
            try:
                from db_store import db_store
                active_account = db_store.get_active_account_id()
            except Exception:
                active_account = 1

        logs = activity_log.get_logs(
            account_id=active_account,
            action=action,
            actor=actor,
            symbol=symbol,
            limit=limit,
            offset=offset,
        )
        return jsonify({"success": True, "data": logs, "total": len(logs)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/activity/stats', methods=['GET'])
def activity_stats_get():
    """获取活动统计"""
    try:
        from activity_log import activity_log
        stats = activity_log.get_stats()
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/activity/clear', methods=['POST'])
def activity_clear():
    """清空活动日志"""
    try:
        from activity_log import activity_log
        activity_log.clear()
        return jsonify({"success": True, "message": "日志已清空"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


import re as _re


def _get_active_conn():
    """获取活跃交易连接器 — 网页API"""
    try:
        if web_connector.connected and web_connector.cookie:
            return web_connector, "web"
        # 通过 account_manager 查找
        from account_manager import account_manager
        accounts = db_store.list_accounts()
        if accounts:
            active_acc = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])
            trader = account_manager.get_trader(active_acc["id"])
            if trader and trader.connected:
                return trader, "web"
    except Exception:
        pass
    return None, None


def _detect_and_execute_trade(message: str):
    """
    检测用户消息中的交易意图，如有则直接执行模拟交易。
    返回: AI回复文本 或 None（无交易意图）
    """
    msg = message.strip()

    # --- 买入检测 ---
    buy_patterns = [
        r'买入?\s*(\d{6})\s*[，,]?\s*(\d+)\s*[股手]?',
        r'帮我买\s*(\d{6})\s*[，,]?\s*(\d+)',
        r'买\s*(\d{6})\s*[，,]?\s*(\d+)',
    ]
    for pat in buy_patterns:
        m = _re.search(pat, msg)
        if m:
            return _execute_ai_trade("buy", m.group(1), int(m.group(2)))

    # --- 卖出检测 ---
    sell_patterns = [
        r'卖出?\s*(\d{6})\s*[，,]?\s*(\d+)\s*[股手]?',
        r'帮我卖\s*(\d{6})\s*[，,]?\s*(\d+)',
        r'卖\s*(\d{6})\s*[，,]?\s*(\d+)',
    ]
    for pat in sell_patterns:
        m = _re.search(pat, msg)
        if m:
            return _execute_ai_trade("sell", m.group(1), int(m.group(2)))

    # --- 查询持仓 ---
    if _re.search(r'(查看?|看[一下]?)?(持仓|仓位|手[里中]|我有)', msg):
        return _get_ai_positions()

    # --- 查询资金 ---
    if _re.search(r'(查看?|看[一下]?)?(资金|余额|账户|钱|可用)', msg):
        return _get_ai_balance()

    # --- 撤单检测 ---
    if _re.search(r'撤销?全部|撤所有|全部撤单', msg):
        try:
            conn, _ = _get_active_conn()
            if conn and hasattr(conn, 'cancel_all_orders'):
                result = conn.cancel_all_orders()
                return f"🚫 {result.get('message', '撤单完成')}"
        except Exception as e:
            return f"❌ 撤单失败: {e}"

    cancel_m = _re.search(r'撤销?订单?\s*(\d+)', msg)
    if cancel_m:
        try:
            conn, _ = _get_active_conn()
            if conn and hasattr(conn, 'cancel_order'):
                result = conn.cancel_order(cancel_m.group(1), datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d"))
                return f"🚫 撤单 {cancel_m.group(1)} → {result.get('message', '完成')}"
        except Exception as e:
            return f"❌ 撤单失败: {e}"

    return None  # 无交易意图，交给AI聊天处理


def _execute_ai_trade(action: str, symbol: str, amount: int, price: float = 0):
    """执行AI交易指令 — OPT-3: 通过AutoTrader执行（含风控+持久化+策略记录）"""
    try:
        # OPT-4: 整手验证（A股买入必须100股整数倍）
        if action == "buy":
            if amount < 100:
                return f"❌ 买入数量{amount}股不足1手(100股)，已取消。"
            amount = (amount // 100) * 100

        # 获取当前价格（改单时使用指定价格）
        if not price:
            stock = get_stock_data(symbol)
            price = stock.get('price', 0) if stock else 0
            if not price:
                return f"❌ 无法获取 {symbol} 的当前价格，请确认股票代码正确。"

        # 通过AutoTrader执行（含风控+持久化+手续费+策略记录）
        from auto_trader import auto_trader
        if not auto_trader.enabled:
            auto_trader.enabled = True

        order = auto_trader.create_order(symbol, action, amount, price)
        result = auto_trader.execute_order(order, stock or {})
        success = result.get("success", False)
        label = "买入" if action == "buy" else "卖出"

        if success:
            exec_price = price
            # 活动日志
            try:
                from activity_log import activity_log as _alog
                _alog.log(
                    action=f"trade_{action}", actor="ai",
                    symbol=symbol, details={
                        "price": exec_price, "amount": amount,
                        "source": "ai_chat"
                    }
                )
            except Exception:
                pass
            # 推送通知
            try:
                notification_manager.add({
                    "type": "trade",
                    "level": "success",
                    "title": f"{'📈 买入' if action == 'buy' else '📉 卖出'}委托成功",
                    "message": f"{symbol} {amount}股 @ ¥{exec_price}\n来源: AI聊天",
                })
            except Exception:
                pass
            # OPT-16: 记录策略信号
            try:
                from strategy_tracker import strategy_tracker
                strategy_tracker.record_signal(
                    strategy_name="ai_chat_manual",
                    symbol=symbol,
                    signal="BUY" if action == "buy" else "SELL",
                    score=0,
                    confidence=0.5,
                    price=price
                )
            except Exception:
                pass
            return (
                f"✅ {label}委托已提交！\n\n"
                f"• 股票: {symbol}\n"
                f"• 数量: {amount}股\n"
                f"• 价格: ¥{exec_price}\n"
                f"• 状态: 委托成功"
            )
        else:
            return (
                f"❌ {label}失败\n\n"
                f"• 股票: {symbol}\n"
                f"• 原因: {result.get('message', '未知错误')}\n\n"
                f"请检查账户状态或股票代码是否正确。"
            )
    except Exception as e:
        return f"❌ 交易执行异常: {str(e)}"


def _get_ai_positions():
    """查询持仓 — 通过活跃连接器"""
    try:
        conn, _ = _get_active_conn()
        if not conn:
            return "❌ 未连接交易系统，无法查询持仓。"

        positions = conn.get_positions() if hasattr(conn, 'get_positions') else []
        if isinstance(positions, dict):
            positions = positions.get('data', [])

        if not positions:
            return "📊 当前没有持仓。\n\n空仓状态，告诉我你想买什么股票，我帮你买入。"

        lines = ["📊 当前持仓\n"]
        total_value = 0
        for pos in positions if isinstance(positions, list) else []:
            name = pos.get("stock_name", pos.get("name", ""))
            code = pos.get("stock_code", pos.get("code", ""))
            qty = pos.get("available_amount", pos.get("amount", 0))
            cost = pos.get("cost_price", pos.get("price", 0))
            current = pos.get("market_price", pos.get("current_price", 0))
            profit = pos.get("profit", 0)
            profit_pct = pos.get("profit_rate", 0)
            emoji = "📈" if float(profit or 0) >= 0 else "📉"
            lines.append(f"{emoji} {name}({code})")
            lines.append(f"   持仓: {qty}股 | 成本: {cost} | 现价: {current}")
            lines.append(f"   盈亏: {profit} ({profit_pct}%)")
            lines.append("")
            total_value += float(current or 0) * int(qty or 0)

        lines.append(f"💰 持仓总市值: {total_value:.2f}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查询持仓失败: {str(e)}"


def _get_ai_balance():
    """查询资金 — 通过活跃连接器"""
    try:
        conn, _ = _get_active_conn()
        if not conn:
            return "❌ 未连接交易系统，无法查询资金。"

        result = conn.get_fund() if hasattr(conn, 'get_fund') else None
        if result and result.get("success"):
            fund = result.get("data", 0)
            return (
                f"💰 账户资金\n\n"
                f"• 可用资金: {fund}\n\n"
                f"告诉我你想买什么股票，我帮你下单。"
            )

        # 备用：从数据库读取（优先用 ky_fund，它是 THS 实时资金）
        try:
            from db_store import db_store
            acc = db_store.get_account()
            if acc:
                # ky_fund = THS实际可用资金，balance = 本地扣减后的余额
                available = acc.get('ky_fund') or acc.get('balance', 0)
                total = acc.get('total_assets', available)
                return (
                    f"💰 账户资金\n\n"
                    f"• 可用资金: {available:,.2f}\n"
                    f"• 总资产: {total:,.2f}\n\n"
                    f"告诉我你想买什么股票，我帮你下单。"
                )
        except Exception:
            pass

        return "💰 无法获取资金信息，请检查账户连接状态。"
    except Exception as e:
        return f"❌ 查询资金失败: {str(e)}"


_AI_TRADING_SYSTEM_PROMPT = """你是同花顺AI交易系统的**全权操盘手**。你拥有完全的自主交易权限。

## 核心能力
1. **自主交易**：根据分析结果主动买入/卖出，无需人工确认
2. **股票分析**：分析个股走势、技术指标、基本面
3. **持仓管理**：查看和管理当前持仓
4. **策略执行**：制定并执行交易策略

## 交易规则
- 这是同花顺模拟比赛账户，交易会通过真实API提交到交易所
- 你有完全的交易自主权，大胆操作
- 追踪活跃股、强势股，把握短线机会
- 盈利时果断止盈，亏损时及时止损

## 如何执行交易
当你决定买入或卖出时，在回复中包含交易指令标签（可多个）：
[TRADE:BUY:股票代码:数量]
[TRADE:SELL:股票代码:数量]

示例：
- 买入100股贵州茅台：[TRADE:BUY:600519:100]
- 卖出50股比亚迪：[TRADE:SELL:002594:50]

## 委托管理
你还可以管理未成交的委托（改价、撤单）：
[TRADE:CANCEL:订单ID]          撤销指定委托
[TRADE:CANCEL:ALL]             撤销全部未成交委托
[TRADE:MODIFY:订单ID:新价格:新数量]  改单（先撤旧再下新）

示例：
- 撤销订单12345：[TRADE:CANCEL:12345]
- 撤销全部：[TRADE:CANCEL:ALL]
- 把订单12345从10.50元改为10.80元：[TRADE:MODIFY:12345:10.80:100]

## 重要
- 回答简洁专业，给出明确的买卖理由
- 使用中文回答"""

_AI_TRADE_TAG_RE = _re.compile(
    r'\[TRADE:(BUY|SELL):(\d{6}):(\d+)\]'
    r'|\[TRADE:CANCEL:(ALL|\d+)\]'
    r'|\[TRADE:MODIFY:(\d+):([\d.]+):(\d+)\]',
    _re.IGNORECASE
)


def _get_trading_context() -> str:
    """获取当前交易上下文（持仓、资金、挂单、策略、市场环境）注入AI对话"""
    parts = []

    # 1. 持仓
    try:
        positions = _get_ai_positions_data()
        if positions:
            parts.append("当前持仓：" + "；".join(
                f"{p.get('symbol','')} {p.get('name','')} {p.get('amount',0)}股 成本{p.get('cost_price',0)}"
                for p in positions[:10]
            ))
        else:
            parts.append("当前持仓：空仓")
    except Exception:
        parts.append("当前持仓：未知")

    # 2. 资金（含冻结金额）
    try:
        balance = _get_ai_balance_data()
        if balance:
            frozen = balance.get('frozen', 0)
            available = balance.get('available', 0)
            total = balance.get('total_assets', 0) or balance.get('total', 0) or balance.get('balance', 0)
            msg = f"可用资金：{available}，总资产：{total}"
            if frozen > 0:
                msg += f"，冻结金额：{frozen}（挂单中）"
            parts.append(msg)
    except Exception:
        pass

    # 3. 市场情绪
    try:
        from data_cache import get_market_basket_quote
        basket = get_market_basket_quote()
        if basket:
            wc = basket.get('weighted_change', 0)
            up = basket.get('up_count', 0)
            down = basket.get('down_count', 0)
            parts.append(f"大盘情绪：加权涨跌{wc:+.2f}%（上涨{up}/下跌{down}）")
    except Exception:
        pass

    # 4. 市场环境（牛/熊/震荡/波动）
    try:
        from market_regime import market_regime_detector
        regime = market_regime_detector.detect()
        if regime and regime.get("regime") != "unknown":
            name = {"bull": "牛市", "bear": "熊市", "sideways": "震荡", "volatile": "波动"}.get(regime["regime"], "未知")
            parts.append(
                f"市场环境：{name}（置信度{regime['confidence']:.0%}） "
                f"MA20={regime.get('ma20',0):.2f} MA60={regime.get('ma60',0):.2f} "
                f"20日涨跌={regime.get('change_20d',0):+.1f}%"
            )
            adj = market_regime_detector.get_strategy_adjustments(regime["regime"])
            if adj:
                prefer = ", ".join(adj.get("prefer_categories", []))
                avoid = ", ".join(adj.get("avoid_categories", []))
                parts.append(f"策略建议：推荐[{prefer}] 避免[{avoid}] 仓位系数{adj['position_factor']:.1f}x")
    except Exception:
        pass

    # 5. 当日挂单委托
    try:
        conn, _ = _get_active_conn()
        if conn and hasattr(conn, 'get_orders'):
            result = conn.get_orders()
            if result.get("success"):
                orders = result.get("data", [])
                if orders:
                    order_lines = []
                    for o in orders[:10]:
                        label = "买入" if "mairu" in o.get("type", "") else "卖出"
                        status = o.get("status", "")
                        order_lines.append(
                            f"{label} {o.get('name','')}({o.get('symbol','')}) "
                            f"{o.get('amount',0)}股@{o.get('price',0)} [{status}]"
                        )
                    parts.append("当日挂单：" + "；".join(order_lines))
    except Exception:
        pass

    # 6. 策略权重排行（AI Chat也能看到策略建议）
    try:
        from strategy_tracker import strategy_tracker
        weights = strategy_tracker.get_strategy_weights(days=30)
        if weights:
            sorted_w = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:5]
            lines = []
            for name, w in sorted_w:
                perf = strategy_tracker.get_performance(name, days=30)
                wr = perf.get("win_rate", 0)
                sigs = perf.get("total_signals", 0)
                lines.append(f"{name}(权重{w:.3f} 胜率{wr:.0f}% 信号{sigs}次)")
            parts.append("策略权重TOP5：" + "；".join(lines))
    except Exception:
        pass

    return "\n".join(parts) if parts else ""


def _get_ai_positions_data():
    """获取持仓数据（返回list）— 兼容 web/desktop 两种连接器格式"""
    conn, _ = _get_active_conn()
    if not conn:
        return []
    try:
        result = conn.get_positions()
        if result.get("success"):
            data = result.get("data", {})
            # web connector 返回 {"balance": ..., "positions": [...]}
            if isinstance(data, dict):
                return data.get("positions", [])
            # desktop connector 直接返回 list
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _get_ai_balance_data():
    """获取资金数据（返回dict，含冻结金额等完整信息）"""
    conn, conn_type = _get_active_conn()
    if not conn:
        return None
    try:
        result = conn.get_positions()
        if result.get("success"):
            data = result.get("data", {})
            if isinstance(data, dict):
                fund = data.get("fund", {})
                if fund:
                    return {
                        "available": fund.get("available", 0),
                        "balance": fund.get("balance", 0),
                        "frozen": fund.get("frozen", 0),
                        "total_assets": fund.get("total_assets", 0),
                        "market_value": fund.get("market_value", 0),
                    }
                return {"available": data.get("balance", 0), "total": data.get("balance", 0)}
    except Exception:
        pass
    return None


def _parse_and_execute_ai_trades(ai_response: str) -> str:
    """从AI回复中解析交易标签并执行，返回追加的执行结果"""
    # 用findall会把所有group混在一起，改用finditer
    results = []
    for m in _AI_TRADE_TAG_RE.finditer(ai_response):
        groups = m.groups()
        # BUY/SELL: (action, symbol, amount, None, None, None, None)
        # CANCEL: (None, None, None, cancel_target, None, None, None)
        # MODIFY: (None, None, None, None, order_id, new_price, new_amount)

        if groups[0]:  # BUY or SELL
            action, symbol, amount = groups[0], groups[1], groups[2]
            action_lower = action.lower()
            label = "买入" if action_lower == "buy" else "卖出"

            # 检查交易时间
            try:
                from competition import competition_manager
                if not competition_manager.is_trading_hours():
                    results.append(f"⏸️ {label} {symbol} {amount}股 → 非交易时段，暂缓")
                    continue
            except Exception:
                pass

            try:
                trade_result = _execute_ai_trade(action_lower, symbol, int(amount))
                results.append(f"🤖 {label} {symbol} {amount}股 → {trade_result}")
            except Exception as e:
                results.append(f"❌ {label} {symbol} 失败: {e}")

        elif groups[3] is not None:  # CANCEL
            target = groups[3]
            try:
                conn, _ = _get_active_conn()
                if target.upper() == "ALL":
                    result = conn.cancel_all_orders() if conn and hasattr(conn, 'cancel_all_orders') else {"success": False, "message": "不支持"}
                else:
                    result = conn.cancel_order(target, datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")) if conn and hasattr(conn, 'cancel_order') else {"success": False, "message": "不支持"}
                msg = result.get("message", "未知") if isinstance(result, dict) else str(result)
                results.append(f"🚫 撤单 {target} → {msg}")
            except Exception as e:
                results.append(f"❌ 撤单 {target} 失败: {e}")

        elif groups[4] is not None:  # MODIFY (cancel + new order)
            order_id, new_price, new_amount = groups[4], groups[5], groups[6]
            try:
                conn, _ = _get_active_conn()
                # 1. 先撤旧单
                if conn and hasattr(conn, 'cancel_order'):
                    cancel_result = conn.cancel_order(order_id, datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d"))
                    if not cancel_result.get("success"):
                        results.append(f"❌ 改单失败（撤单）: {cancel_result.get('message','')}")
                        continue
                # 2. 下新单（需要知道股票代码和方向，从挂单列表查）
                orders_result = conn.get_orders() if conn and hasattr(conn, 'get_orders') else {"success": False}
                old_order = None
                if orders_result.get("success"):
                    for o in orders_result.get("data", []):
                        if str(o.get("order_id", "")) == str(order_id):
                            old_order = o
                            break
                if old_order:
                    symbol = old_order.get("symbol", "")
                    direction = "buy" if "mairu" in old_order.get("type", "") else "sell"
                    trade_result = _execute_ai_trade(direction, symbol, int(new_amount), price=float(new_price))
                    results.append(f"🔄 改单 {symbol} → 撤旧成功，新单: {trade_result}")
                else:
                    results.append(f"⚠️ 改单: 撤旧成功但找不到原委托信息，无法下新单")
            except Exception as e:
                results.append(f"❌ 改单 {order_id} 失败: {e}")

    return "\n\n---\n**交易执行结果：**\n" + "\n".join(results) if results else ""


_CHAT_HISTORY_FILE = Path(__file__).parent / "data" / "chat_history.json"

@app.route('/api/ai/chat/history', methods=['GET'])
def get_chat_history():
    """获取AI对话历史"""
    try:
        if _CHAT_HISTORY_FILE.exists():
            with open(_CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            return jsonify({"success": True, "data": history[-50:]})  # 最近50条
        return jsonify({"success": True, "data": []})
    except Exception:
        return jsonify({"success": True, "data": []})

@app.route('/api/ai/chat/history', methods=['POST'])
def save_chat_history():
    """保存AI对话历史"""
    try:
        data = request.json or {}
        messages = data.get("messages", [])
        _CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(messages[-100:], f, ensure_ascii=False, indent=1)  # 保留最近100条
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    """AI对话（带交易能力）"""
    try:
        data = request.json
        message = data.get('message', '')
        history = data.get('history', [])

        if not message:
            return jsonify({"success": False, "message": "请输入消息"})

        # 检测交易意图：直接执行
        try:
            trade_result = _detect_and_execute_trade(message)
        except Exception:
            trade_result = None
        if trade_result:
            return jsonify({"success": True, "data": {"content": trade_result, "model": "system", "traded": True}})

        # ★ 注入当前时间 + 交易时段 + 账户上下文
        try:
            from competition import competition_manager
            market_status = competition_manager.get_market_status()
            time_info = (
                f"当前时间: {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n"
                f"市场状态: {'交易中' if market_status.get('is_trading_hours') else '休市'}\n"
                f"交易时段: 集合竞价9:15-9:25, 上午9:30-11:30, 下午13:00-15:00\n"
                f"注意: A股交易时间为周一至周五(节假日除外), 仅上述时段可下单"
            )
        except Exception:
            time_info = f"当前时间: {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')} (北京时间)"

        context = _get_trading_context()
        parts = [f"[系统信息]\n{time_info}"]
        if context:
            parts.append(f"[当前账户状态]\n{context}")

        # 注入实时行情数据
        try:
            from data_cache import get_realtime_quote as _grq
            hot_stocks = []
            for sym in ["sh000001", "sz399001", "sz399006"]:  # 上证指数、深证成指、创业板指
                q = _grq(sym)
                if q:
                    hot_stocks.append(f"{q.get('name','')}: {q.get('price',0)} ({q.get('change_pct',0):+.2f}%)")
            if hot_stocks:
                parts.append(f"[大盘指数]\n" + "\n".join(hot_stocks))
        except:
            pass

        parts.append(f"[用户问题]\n{message}")
        message = "\n\n".join(parts)

        result = ai_connector.chat(message, history=history, system_prompt=_AI_TRADING_SYSTEM_PROMPT)

        # 解析AI回复中的交易标签并执行
        if result.get("success") and result.get("data", {}).get("content"):
            ai_content = result["data"]["content"]
            trade_append = _parse_and_execute_ai_trades(ai_content)
            if trade_append:
                # 移除回复中的交易标签，追加执行结果
                clean_content = _AI_TRADE_TAG_RE.sub('', ai_content).strip()
                result["data"]["content"] = clean_content + trade_append
                result["data"]["traded"] = True
                # 把执行结果反馈给AI，让AI知道交易是否成功
                try:
                    followup = ai_connector.chat(
                        f"[系统通知] 交易执行结果:\n{trade_append}\n\n请根据以上结果简短回复用户。",
                        history=[
                            {"role": "user", "content": message},
                            {"role": "assistant", "content": ai_content},
                        ],
                        system_prompt="你是交易助手。用户发起了一笔交易，系统已执行。请用1-2句话简要告知用户结果。不要再次发起交易。"
                    )
                    if followup.get("success") and followup.get("data", {}).get("content"):
                        result["data"]["content"] = clean_content + "\n\n" + followup["data"]["content"]
                except Exception:
                    pass

        # 自动保存聊天记录（后端侧）
        try:
            if result.get("success") and result.get("data", {}).get("content"):
                existing = []
                if _CHAT_HISTORY_FILE.exists():
                    with open(_CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                # 保存原始用户消息（不含注入的上下文）
                raw_message = data.get('message', '')
                existing.append({"role": "user", "content": raw_message})
                existing.append({"role": "assistant", "content": result["data"]["content"]})
                _CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(_CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(existing[-100:], f, ensure_ascii=False, indent=1)
        except Exception:
            pass

        # 活动日志
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="ai_chat", actor="user",
                      details={"message": message[:100], "success": result.get("success", False)})
        except Exception:
            pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/analyze/<symbol>', methods=['GET'])
def ai_analyze_stock(symbol):
    """AI分析股票"""
    try:
        stock = get_stock_data(symbol)
        if not stock:
            return jsonify({"success": False, "message": f"未找到股票 {symbol}"})

        # 获取技术指标（真实数据）
        indicators = calculate_indicators(symbol)
        if not indicators:
            indicators = {"close": stock["price"]}

        # AI分析
        ai_result = ai_connector.analyze_stock(symbol, stock, indicators)

        # 增强策略分析
        signal = enhanced_strategy.generate_signal(symbol, stock, indicators, ai_result.get('data', {}).get('content', ''))

        # 活动日志
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="ai_analyze", actor="user", symbol=symbol,
                      name=stock.get("name", symbol),
                      details={"success": ai_result.get("success", False),
                               "signal": signal.signal_type if signal else None})
        except Exception:
            pass

        return jsonify({
            "success": True,
            "data": {
                "stock": stock,
                "indicators": indicators,
                "ai_analysis": ai_result.get('data', {}).get('content', ''),
                "signal": signal.to_dict(),
                "report": enhanced_strategy.format_signal_report(signal, stock)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/strategy', methods=['POST'])
def ai_generate_strategy():
    """AI生成交易策略"""
    try:
        data = request.json
        risk_level = data.get('risk_level', 'medium')

        # 获取真实市场数据
        hot = competition_manager.get_stock_pool(limit=5, sort_by="amount")
        market_data = {
            "market_overview": {"sh_index": 0, "sz_index": 0, "up_count": 0, "down_count": 0},
            "hot_stocks": hot,
            "risk_level": risk_level
        }

        result = ai_connector.generate_strategy(market_data, risk_level)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/ai/explain/<indicator>', methods=['GET'])
def ai_explain_indicator(indicator):
    """AI解释技术指标"""
    try:
        value = request.args.get('value', type=float)
        context = request.args.get('context', '')

        result = ai_connector.explain_indicator(indicator, value, context)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/signal/<symbol>', methods=['GET'])
def get_strategy_signal(symbol):
    """获取策略信号"""
    try:
        stock = get_stock_data(symbol)
        if not stock:
            return jsonify({"success": False, "message": f"未找到股票 {symbol}"})

        # 获取真实技术指标
        indicators = calculate_indicators(symbol)
        if not indicators:
            indicators = {"close": stock["price"]}

        # 获取信号
        signal = enhanced_strategy.generate_signal(symbol, stock, indicators)

        # 计算建议仓位（从持久化存储获取余额）
        balance = data_store.account["balance"]
        position_size = enhanced_strategy.calculate_position_size(signal, balance, stock['price'])

        return jsonify({
            "success": True,
            "data": {
                "signal": signal.to_dict(),
                "report": enhanced_strategy.format_signal_report(signal, stock),
                "position_size": position_size,
                "estimated_cost": round(position_size * stock['price'], 2)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/risk-level', methods=['POST'])
def set_risk_level():
    """设置风险等级"""
    try:
        data = request.json
        level = data.get('level', 'medium')
        enhanced_strategy.set_risk_level(level)
        return jsonify({
            "success": True,
            "message": f"风险等级已设置为: {level}",
            "data": {
                "risk_level": level,
                "max_position_ratio": enhanced_strategy.max_position_ratio,
                "stop_loss_ratio": enhanced_strategy.stop_loss_ratio,
                "take_profit_ratio": enhanced_strategy.take_profit_ratio
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 自动交易 API ====================

@app.route('/api/auto-trade/config', methods=['GET'])
def auto_trade_config_get():
    """获取自动交易配置"""
    return jsonify({
        "success": True,
        "data": auto_trader.get_config()
    })


@app.route('/api/auto-trade/config', methods=['POST'])
def auto_trade_config_update():
    """更新自动交易配置"""
    try:
        data = request.json
        auto_trader.update_config(**data)
        return jsonify({"success": True, "message": "配置已更新"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/auto-trade/toggle', methods=['POST'])
def auto_trade_toggle():
    """开启/关闭自动交易"""
    try:
        data = request.json
        auto_trader.enabled = data.get("enabled", False)
        # 活动日志
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="config_change", actor="user",
                      name="auto_trade",
                      details={"enabled": auto_trader.enabled})
        except Exception:
            pass
        return jsonify({
            "success": True,
            "message": f"自动交易已{'开启' if auto_trader.enabled else '关闭'}",
            "data": {"enabled": auto_trader.enabled}
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/auto-trade/status', methods=['GET'])
def auto_trade_status():
    """获取自动交易状态"""
    return jsonify({
        "success": True,
        "data": {
            "enabled": auto_trader.enabled,
            "account": auto_trader.get_account_summary(),
            "positions": auto_trader.get_positions(),
            "config": auto_trader.get_config()
        }
    })


@app.route('/api/auto-trade/execute', methods=['POST'])
def auto_trade_execute():
    """手动执行交易"""
    try:
        data = request.json
        symbol = data.get("symbol")
        action = data.get("action")  # buy / sell
        amount = data.get("amount")
        price = data.get("price")

        if not all([symbol, action, amount, price]):
            return jsonify({"success": False, "message": "参数不完整"})

        # 获取股票数据
        stock = get_stock_data(symbol)

        # 创建并执行订单
        order = auto_trader.create_order(
            symbol=symbol,
            action=action,
            amount=amount,
            price=price
        )

        result = auto_trader.execute_order(order, stock)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/auto-trade/positions', methods=['GET'])
def auto_trade_positions():
    """获取自动交易持仓"""
    return jsonify({
        "success": True,
        "data": auto_trader.get_positions()
    })


@app.route('/api/auto-trade/history', methods=['GET'])
def auto_trade_history():
    """获取自动交易历史"""
    limit = request.args.get('limit', 50, type=int)
    return jsonify({
        "success": True,
        "data": auto_trader.get_trade_history(limit)
    })


@app.route('/api/auto-trade/reset', methods=['POST'])
def auto_trade_reset():
    """重置自动交易账户"""
    try:
        data = request.json
        initial_balance = data.get("initial_balance", 1000000)
        auto_trader.reset(initial_balance)
        return jsonify({"success": True, "message": "账户已重置"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/auto-trade/signal/<symbol>', methods=['GET'])
def auto_trade_signal(symbol):
    """获取自动交易信号"""
    try:
        stock = get_stock_data(symbol)
        if not stock:
            return jsonify({"success": False, "message": f"未找到股票 {symbol}"})

        # 生成信号
        signal = enhanced_strategy.generate_signal(symbol, stock)

        # 计算建议仓位
        position_size = auto_trader.calculate_position_size(stock['price'], signal.confidence)

        return jsonify({
            "success": True,
            "data": {
                "signal": signal.to_dict(),
                "position_size": position_size,
                "estimated_cost": round(position_size * stock['price'], 2)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/auto-trade/check-stops', methods=['GET'])
def auto_trade_check_stops():
    """检查止损止盈"""
    try:
        # 获取所有持仓的当前价格
        stock_prices = {}
        for pos in auto_trader.positions.values():
            stock = get_stock_data(pos.symbol)
            if stock:
                stock_prices[pos.symbol] = stock['price']

        # 检查止损止盈
        signals = auto_trader.check_stop_loss_take_profit(stock_prices)

        return jsonify({
            "success": True,
            "data": signals
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 风控 API ====================

@app.route('/api/risk/assess', methods=['GET'])
def risk_assess():
    """风险评估"""
    try:
        positions = auto_trader.get_positions()
        total_assets = auto_trader.get_account_summary()["total_assets"]

        # 获取股票数据
        stock_data = {}
        for pos in positions:
            stock = get_stock_data(pos["symbol"])
            if stock:
                stock_data[pos["symbol"]] = stock

        # 评估风险
        assessment = risk_manager.assess_portfolio_risk(
            {p["symbol"]: p for p in positions},
            stock_data
        )

        return jsonify({
            "success": True,
            "data": assessment
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/risk/position-size', methods=['POST'])
def risk_position_size():
    """计算建议仓位"""
    try:
        data = request.json
        signal_strength = data.get("signal_strength", 0.5)
        volatility = data.get("volatility", 0.02)
        account_value = data.get("account_value", 1000000)
        current_positions = data.get("current_positions", 0)

        result = risk_manager.calculate_optimal_position(
            signal_strength, volatility, account_value, current_positions
        )

        return jsonify({
            "success": True,
            "data": result
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/risk/stop-loss', methods=['POST'])
def risk_stop_loss():
    """计算止损价"""
    try:
        data = request.json
        entry_price = data.get("entry_price")
        volatility = data.get("volatility", 0.02)
        support_level = data.get("support_level")

        result = risk_manager.calculate_stop_loss(entry_price, volatility, support_level)

        return jsonify({
            "success": True,
            "data": result
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/risk/take-profit', methods=['POST'])
def risk_take_profit():
    """计算止盈价"""
    try:
        data = request.json
        entry_price = data.get("entry_price")
        volatility = data.get("volatility", 0.02)
        resistance_level = data.get("resistance_level")

        result = risk_manager.calculate_take_profit(entry_price, volatility, resistance_level)

        return jsonify({
            "success": True,
            "data": result
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 回测 API ====================

@app.route('/api/backtest/run', methods=['POST'])
def backtest_run():
    """运行回测（使用真实数据 + 策略组合）"""
    try:
        data = request.json
        symbol = data.get("symbol", "000001")
        strategies = data.get("strategies", ["multi_factor"])
        initial_capital = data.get("initial_capital", 1000000)
        days = data.get("days", 60)
        position_size = data.get("position_size", 0.3)
        stock_name = data.get("stock_name", "")

        result = backtester.run_strategy_backtest(
            symbol=symbol,
            strategy_names=strategies,
            initial_capital=initial_capital,
            days=days,
            position_size=position_size,
            stock_name=stock_name
        )

        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/backtest/portfolio', methods=['POST'])
def backtest_portfolio():
    """组合回测（多股票）"""
    try:
        data = request.json
        symbols = data.get("symbols", [])
        strategies = data.get("strategies", ["multi_factor"])
        initial_capital = data.get("initial_capital", 1000000)
        days = data.get("days", 60)
        position_per_stock = data.get("position_per_stock", 0.2)

        result = backtester.run_portfolio_backtest(
            symbols=symbols,
            strategy_names=strategies,
            initial_capital=initial_capital,
            days=days,
            position_per_stock=position_per_stock
        )

        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/backtest/compare', methods=['POST'])
def backtest_compare():
    """比较不同策略组合"""
    try:
        data = request.json
        symbol = data.get("symbol", "000001")
        strategy_groups = data.get("strategy_groups", [["multi_factor"], ["ma_cross", "rsi_extreme"]])
        days = data.get("days", 60)
        initial_capital = data.get("initial_capital", 1000000)

        result = backtester.compare_strategies(
            symbol=symbol,
            strategy_groups=strategy_groups,
            days=days,
            initial_capital=initial_capital
        )

        return jsonify({
            "success": True,
            "data": result
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/backtest/data/<symbol>', methods=['GET'])
def backtest_data(symbol):
    """获取回测数据（真实数据）"""
    try:
        days = request.args.get('days', 60, type=int)
        data = backtester.fetch_real_data(symbol, days)
        data = backtester.calculate_indicators_for_data(data)

        return jsonify({
            "success": True,
            "data": data
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== AI 大脑 API ====================

@app.route('/api/brain/catalog', methods=['GET'])
def brain_catalog():
    """获取策略目录"""
    try:
        from strategy_registry import registry
        catalog_text = registry.get_catalog()
        catalog_json = registry.get_catalog_json()
        return jsonify({
            "success": True,
            "data": {
                "catalog_text": catalog_text,
                "strategies": catalog_json,
                "total": len(catalog_json)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/strategy/<name>', methods=['GET'])
def brain_strategy_doc(name):
    """获取单个策略的接口文档"""
    try:
        from strategy_registry import registry
        doc = registry.get_strategy_doc(name)
        return jsonify({"success": True, "data": {"doc": doc}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/decide', methods=['POST'])
def brain_decide():
    """AI 大脑决策"""
    try:
        from ai_brain import ai_brain

        data = request.json
        symbol = data.get("symbol")
        strategy_names = data.get("strategies")  # 可选，指定策略列表

        if not symbol:
            return jsonify({"success": False, "message": "请输入股票代码"})

        # 获取股票数据
        stock = get_stock_data(symbol)
        if not stock:
            return jsonify({"success": False, "message": f"未找到股票 {symbol}"})

        # 获取真实技术指标
        indicators = calculate_indicators(symbol)
        if not indicators:
            indicators = {"close": stock["price"]}

        # 账户信息
        account_info = {"balance": data_store.account.get("balance", 1000000)}

        # AI决策
        if ai_connector.connected:
            decision = ai_brain.decide_with_ai(symbol, stock, indicators, account_info)
        else:
            decision = ai_brain.decide(symbol, stock, indicators, account_info, strategy_names)

        return jsonify({
            "success": True,
            "data": decision.to_dict()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/chart-lines/<symbol>', methods=['GET'])
def brain_chart_lines(symbol):
    """轻量级图表参考线（目标价/止损），不调用AI，纯计算"""
    try:
        indicators = calculate_indicators(symbol)
        if not indicators:
            return jsonify({"success": True, "data": {}})

        close = indicators.get("close", 0)
        atr = indicators.get("atr", close * 0.02)
        rsi = indicators.get("rsi", 50)

        # 简单策略：目标价 = 当前价 + 2*ATR，止损 = 当前价 - 1.5*ATR
        target = round(close + 2 * atr, 2) if atr else None
        stop = round(close - 1.5 * atr, 2) if atr else None

        return jsonify({
            "success": True,
            "data": {
                "target_price": target,
                "stop_loss": stop,
                "atr": atr,
                "rsi": rsi,
                "ma5": indicators.get("ma5"),
                "ma10": indicators.get("ma10"),
                "ma20": indicators.get("ma20"),
            }
        })
    except Exception as e:
        return jsonify({"success": True, "data": {}})


@app.route('/api/brain/adapt', methods=['GET'])
def brain_adapt():
    """AI 评估策略表现并给出调整建议"""
    try:
        from ai_brain import ai_brain
        result = ai_brain.evaluate_and_adapt()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/write-strategy', methods=['POST'])
def brain_write_strategy():
    """AI 编写新策略"""
    try:
        from ai_brain import ai_brain

        data = request.json
        spec = data.get("spec", "")

        if not spec:
            return jsonify({"success": False, "message": "请输入策略描述"})

        result = ai_brain.write_strategy(spec)
        # 活动日志: AI编写策略
        if result.get("success"):
            try:
                from activity_log import activity_log as _alog
                _alog.log(action="strategy_write", actor="ai",
                          name=result.get("data", {}).get("name", ""),
                          details={"spec": spec[:200]})
            except Exception:
                pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/install-strategy', methods=['POST'])
def brain_install_strategy():
    """安装自定义策略"""
    try:
        from ai_brain import ai_brain

        data = request.json
        name = data.get("name")
        code = data.get("code")

        if not name or not code:
            return jsonify({"success": False, "message": "需要策略名称和代码"})

        result = ai_brain.install_strategy(name, code)
        # 活动日志: 安装策略
        if result.get("success"):
            try:
                from activity_log import activity_log as _alog
                _alog.log(action="strategy_install", actor="user",
                          name=name, details={"code_length": len(code)})
            except Exception:
                pass
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/performance', methods=['GET'])
def brain_performance():
    """策略表现排行"""
    try:
        from strategy_tracker import strategy_tracker
        days = request.args.get('days', 30, type=int)
        rankings = strategy_tracker.rank_strategies(days)
        summary = strategy_tracker.get_strategy_summary()
        recent = strategy_tracker.get_recent_signals(20)
        return jsonify({
            "success": True,
            "data": {
                "rankings": rankings,
                "summary": summary,
                "recent_signals": recent
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/weights', methods=['GET'])
def brain_weights():
    """获取策略动态权重"""
    try:
        from strategy_tracker import strategy_tracker
        days = request.args.get('days', 30, type=int)
        weights = strategy_tracker.get_strategy_weights(days)
        weighted_perf = strategy_tracker.get_weighted_performance(days)
        return jsonify({
            "success": True,
            "data": {
                "weights": weights,
                "weighted_performance": weighted_perf
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== Strategy Optimization API ====================

@app.route('/api/strategy/optimize', methods=['POST'])
def optimize_strategy():
    """优化单个策略参数（Optuna 贝叶斯优化）"""
    try:
        data = request.json
        strategy_name = data.get("strategy_name")
        n_trials = data.get("n_trials", 50)
        days = data.get("days", 120)
        objective = data.get("objective", "sharpe")  # sharpe/return/win_rate

        if not strategy_name:
            return jsonify({"success": False, "message": "请输入策略名称"})

        symbol = data.get("symbol", "000001")
        stock = get_stock_data(symbol)
        if not stock:
            stock = {"price": 0, "change_pct": 0, "volume": 0}
        indicators = calculate_indicators(symbol) or {"close": stock.get("price", 0)}

        from strategy_optimizer import strategy_optimizer
        result = strategy_optimizer.optimize(
            strategy_name, stock, indicators,
            n_trials=n_trials, days=days, objective=objective
        )

        # 自动保存最优参数
        if result.get("success") and result.get("best_params"):
            strategy_optimizer.save_optimized_params(
                strategy_name, result["best_params"],
                {"value": result.get("best_value", 0), "objective": objective}
            )

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/batch-optimize', methods=['POST'])
def batch_optimize():
    """批量优化所有策略参数（比赛前一键执行）"""
    try:
        data = request.json or {}
        n_trials = data.get("n_trials", 30)
        days = data.get("days", 120)
        strategy_names = data.get("strategy_names")  # None = 全部

        symbol = data.get("symbol", "000001")
        stock = get_stock_data(symbol)
        if not stock:
            stock = {"price": 0, "change_pct": 0, "volume": 0}
        indicators = calculate_indicators(symbol) or {"close": stock.get("price", 0)}

        from strategy_optimizer import strategy_optimizer
        result = strategy_optimizer.batch_optimize(
            stock, indicators, strategy_names, n_trials, days
        )

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/backtest', methods=['POST'])
def backtest_strategy():
    """运行策略回测"""
    try:
        data = request.json
        strategy_name = data.get("strategy_name")
        params = data.get("params", {})
        days = data.get("days", 120)

        if not strategy_name:
            return jsonify({"success": False, "message": "请输入策略名称"})

        symbol = data.get("symbol", "000001")
        stock = get_stock_data(symbol)
        if not stock:
            stock = {"price": 0, "change_pct": 0, "volume": 0}
        indicators = calculate_indicators(symbol) or {"close": stock.get("price", 0)}

        from strategy_optimizer import strategy_optimizer
        result = strategy_optimizer._run_backtest(strategy_name, stock, indicators, params, days)

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/sensitivity', methods=['POST'])
def strategy_sensitivity():
    """参数敏感性分析"""
    try:
        data = request.json
        strategy_name = data.get("strategy_name")
        param_name = data.get("param_name")
        range_pct = data.get("range_pct", 20)

        if not strategy_name or not param_name:
            return jsonify({"success": False, "message": "请输入策略名称和参数名称"})

        symbol = data.get("symbol", "000001")
        stock = get_stock_data(symbol)
        if not stock:
            stock = {"price": 0, "change_pct": 0, "volume": 0}
        indicators = calculate_indicators(symbol) or {"close": stock.get("price", 0)}

        from strategy_optimizer import strategy_optimizer
        result = strategy_optimizer.parameter_sensitivity(
            strategy_name, stock, indicators, param_name, range_pct
        )

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/walk-forward', methods=['POST'])
def walk_forward():
    """滚动窗口回测"""
    try:
        data = request.json
        strategy_name = data.get("strategy_name")
        train_days = data.get("train_days", 60)
        test_days = data.get("test_days", 20)
        n_windows = data.get("n_windows", 5)

        if not strategy_name:
            return jsonify({"success": False, "message": "请输入策略名称"})

        symbol = data.get("symbol", "000001")
        stock = get_stock_data(symbol)
        if not stock:
            stock = {"price": 0, "change_pct": 0, "volume": 0}
        indicators = calculate_indicators(symbol) or {"close": stock.get("price", 0)}

        from strategy_optimizer import strategy_optimizer
        result = strategy_optimizer.walk_forward(
            strategy_name, stock, indicators, train_days, test_days, n_windows
        )

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/health', methods=['GET'])
def strategy_health():
    """获取所有策略健康状态"""
    try:
        from strategy_health import strategy_health_monitor
        health = strategy_health_monitor.get_all_health()
        return jsonify({"success": True, "data": health})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/health/<name>', methods=['GET'])
def strategy_health_detail(name):
    """获取单个策略健康详情"""
    try:
        from strategy_health import strategy_health_monitor
        health = strategy_health_monitor.check_health(name)
        return jsonify({"success": True, "data": health})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/optimized-params', methods=['GET'])
def optimized_params():
    """获取所有已优化的参数"""
    try:
        from strategy_optimizer import strategy_optimizer
        status = strategy_optimizer.get_optimization_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/strategy/clear-params', methods=['POST'])
def clear_optimized_params():
    """清除优化后的参数"""
    try:
        data = request.json or {}
        strategy_name = data.get("strategy_name")
        from strategy_optimizer import strategy_optimizer
        strategy_optimizer.clear_optimized_params(strategy_name)
        return jsonify({"success": True, "message": "已清除"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/auto-trade', methods=['POST'])
def brain_auto_trade():
    """AI 大脑决策并自动执行交易"""
    try:
        data = request.json
        symbol = data.get("symbol")
        if not symbol:
            return jsonify({"success": False, "message": "请输入股票代码"})

        stock = get_stock_data(symbol)
        if not stock:
            return jsonify({"success": False, "message": f"未找到股票 {symbol}"})

        indicators = calculate_indicators(symbol)
        if not indicators:
            indicators = {"close": stock["price"]}

        result = trader.decide_and_trade(symbol, stock, indicators)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/evaluate', methods=['POST'])
def brain_evaluate_signals():
    """评估历史信号准确性"""
    try:
        # 获取所有持仓股票的当前价格
        stock_prices = {}
        for pos in trader.positions.values():
            stock = get_stock_data(pos.symbol)
            if stock:
                stock_prices[pos.symbol] = stock["price"]

        trader.evaluate_signals(stock_prices)
        return jsonify({"success": True, "message": "信号评估完成"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/thresholds', methods=['GET'])
def brain_thresholds():
    """获取当前动态决策阈值"""
    try:
        from ai_brain import ai_brain
        thresholds = ai_brain.get_dynamic_thresholds()
        return jsonify({"success": True, "data": thresholds})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 每日汇总 API ============

@app.route('/api/daily/summary', methods=['POST'])
def daily_summary_manual():
    """手动触发每日汇总"""
    try:
        from notification import daily_scheduler
        daily_scheduler.force_daily_summary()
        return jsonify({"success": True, "message": "每日汇总已推送"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/daily/reminder', methods=['POST'])
def daily_reminder_manual():
    """手动触发盘前提醒"""
    try:
        from notification import daily_scheduler
        daily_scheduler.force_market_reminder()
        return jsonify({"success": True, "message": "盘前提醒已推送"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 比赛规则 API ============

@app.route('/api/competition/rules', methods=['GET'])
def competition_rules():
    """获取比赛规则"""
    try:
        rules = competition_manager.get_rules()
        return jsonify({"success": True, "data": rules})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/competition/status', methods=['GET'])
def competition_status():
    """获取比赛状态（是否开盘、倒计时等）"""
    try:
        status = competition_manager.get_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/competition/validate', methods=['POST'])
def competition_validate():
    """验证订单是否合规"""
    try:
        data = request.json
        is_valid, message = competition_manager.validate_order(data)

        # 始终计算费用（前端需要展示），不再受 is_valid 限制
        fee_info = {"commission": 0, "stamp_tax": 0, "transfer_fee": 0, "total_fee": 0}
        if data.get("price") and data.get("quantity"):
            fee_info = competition_manager.get_commission(
                data.get("action", "buy"),
                data["price"],
                data["quantity"]
            )

        return jsonify({
            "success": True,
            "data": {
                "is_valid": is_valid,
                "message": message,
                "fee_info": fee_info
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/pool', methods=['GET'])
def stock_pool():
    """获取比赛可交易股票池"""
    try:
        limit = request.args.get('limit', 100, type=int)
        sort_by = request.args.get('sort', 'amount', type=str)
        stocks = competition_manager.get_stock_pool(limit=limit, sort_by=sort_by)
        return jsonify({"success": True, "data": stocks})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/pool/count', methods=['GET'])
def stock_pool_count():
    """获取股票池统计"""
    try:
        count = competition_manager.get_stock_pool_count()
        return jsonify({"success": True, "data": count})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/monitor/start', methods=['POST'])
def monitor_start():
    """启动止损止盈监控"""
    try:
        auto_trader.start_monitor()
        return jsonify({"success": True, "message": "止损止盈监控已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/monitor/stop', methods=['POST'])
def monitor_stop():
    """停止监控"""
    try:
        auto_trader.stop_monitor()
        return jsonify({"success": True, "message": "监控已停止"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/monitor/status', methods=['GET'])
def monitor_status():
    """获取监控状态"""
    try:
        status = auto_trader.get_monitor_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 策略扫描器 API ============

@app.route('/api/scanner/start', methods=['POST'])
def scanner_start():
    """启动策略扫描器"""
    try:
        # 如果没有配置股票列表，用多因子选股器获取默认列表
        if not strategy_scanner._stocks:
            from stock_screener import stock_screener
            from market_regime import market_regime_detector

            # 获取市场环境
            regime = market_regime_detector.detect().get("regime", "sideways")

            # 用选股器筛选
            from data_cache import get_realtime_quote
            market_data = get_realtime_quote()
            if market_data is not None and not market_data.empty:
                screened = stock_screener.screen(market_data, regime=regime, max_count=50)
                stock_codes = [s["code"] for s in screened]
            else:
                # 降级到原有逻辑
                stocks = competition_manager.get_stock_pool(limit=30, sort_by="amount")
                stock_codes = [s["code"] for s in stocks]

            strategy_scanner.configure(stocks=stock_codes)

        strategy_scanner.start()
        # 活动日志
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="scanner_start", actor="user",
                      details={"stocks": len(strategy_scanner._stocks), "auto_execute": strategy_scanner._auto_execute})
        except Exception:
            pass
        return jsonify({"success": True, "message": "策略扫描器已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/scanner/stop', methods=['POST'])
def scanner_stop():
    """停止策略扫描器"""
    try:
        strategy_scanner.stop()
        # 活动日志
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="scanner_stop", actor="user")
        except Exception:
            pass
        return jsonify({"success": True, "message": "策略扫描器已停止"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/scanner/configure', methods=['POST'])
def scanner_configure():
    """配置策略扫描器"""
    try:
        data = request.json
        stocks = data.get("stocks")
        strategy_names = data.get("strategy_names")
        interval = data.get("interval")
        max_per_round = data.get("max_per_round")
        buy_threshold = data.get("buy_threshold")
        auto_execute = data.get("auto_execute")

        strategy_scanner.configure(
            stocks=stocks,
            strategy_names=strategy_names,
            interval=interval,
            max_per_round=max_per_round,
            buy_threshold=buy_threshold,
            auto_execute=auto_execute,
        )
        # 活动日志
        try:
            from activity_log import activity_log as _alog
            _alog.log(action="scanner_configure", actor="user",
                      details={"stocks": len(stocks) if stocks else 0, "auto_execute": auto_execute,
                               "interval": interval, "max_per_round": max_per_round})
        except Exception:
            pass
        return jsonify({"success": True, "message": "配置已更新"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/scanner/status', methods=['GET'])
def scanner_status():
    """获取策略扫描器状态"""
    try:
        status = strategy_scanner.get_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/screener/run', methods=['POST'])
def screener_run():
    """手动运行多因子选股器"""
    try:
        from stock_screener import stock_screener
        from market_regime import market_regime_detector
        from data_cache import get_realtime_quote

        data = request.json or {}
        regime = data.get("regime")
        max_count = data.get("max_count", 50)

        # 自动检测市场环境（如果未指定）
        if not regime:
            regime = market_regime_detector.detect().get("regime", "sideways")

        market_data = get_realtime_quote()
        if market_data is None or market_data.empty:
            return jsonify({"success": False, "message": "无法获取市场数据"})

        result = stock_screener.screen(market_data, regime=regime, max_count=max_count)
        return jsonify({
            "success": True,
            "data": {
                "regime": regime,
                "count": len(result),
                "stocks": result
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 通知 API ============

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """获取通知列表"""
    try:
        unread_only = request.args.get('unread', 'false') == 'true'
        limit = request.args.get('limit', 50, type=int)
        if unread_only:
            notifications = notification_manager.get_unread()
        else:
            notifications = notification_manager.get_all(limit)
        return jsonify({
            "success": True,
            "data": {
                "notifications": notifications,
                "unread_count": notification_manager.get_unread_count()
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    """标记通知已读"""
    try:
        data = request.json or {}
        ids = data.get("ids")
        notification_manager.mark_read(ids)
        return jsonify({"success": True, "message": "已标记已读"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/notifications/stream', methods=['GET'])
def notifications_stream():
    """SSE 实时推送通知"""
    from flask import Response
    import queue as _queue

    q = notification_manager.register_sse()

    def generate():
        try:
            while True:
                try:
                    notification = q.get(timeout=30)
                    yield f"data: {json.dumps(notification, ensure_ascii=False)}\n\n"
                except _queue.Empty:
                    yield f": keepalive\n\n"
        except GeneratorExit:
            notification_manager.unregister_sse(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ============ Server酱微信推送 API ============

@app.route('/api/server-chan/config', methods=['GET'])
def get_server_chan_config():
    """获取Server酱配置状态"""
    from server_chan import server_chan
    return jsonify({"success": True, "data": server_chan.status})


@app.route('/api/server-chan/config', methods=['POST'])
def save_server_chan_config():
    """保存Server酱配置"""
    from server_chan import server_chan
    data = request.json or {}
    send_key = data.get("send_key", "").strip()
    enabled = data.get("enabled", True)

    if not send_key:
        return jsonify({"success": False, "message": "请输入SendKey"})

    server_chan.save_config(send_key, enabled)
    return jsonify({"success": True, "message": "配置已保存"})


@app.route('/api/server-chan/test', methods=['POST'])
def test_server_chan():
    """测试Server酱推送"""
    from server_chan import server_chan
    result = server_chan.send("测试通知", "来自同花顺AI交易系统的测试消息\n\n如果你看到这条消息，说明推送配置成功！")
    return jsonify(result)


# ============ 多账户管理 API ============

@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """列出所有同花顺比赛账号"""
    try:
        accounts = account_manager.list_all()
        global_summary = account_manager.get_global_summary()
        active_id = db_store.get_active_account_id()
        return jsonify({
            "success": True,
            "data": {
                "accounts": accounts,
                "global_summary": global_summary,
                "active_account_id": active_id
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts', methods=['POST'])
def add_ths_account():
    """添加同花顺比赛账号（绑定Cookie）"""
    try:
        data = request.json
        name = data.get("name", "")
        cookie = data.get("cookie", "")
        if not name or not cookie:
            return jsonify({"success": False, "message": "请输入账号名称和Cookie"})
        result = account_manager.add_account(name, cookie)
        # 登录成功后，同步全局 web_connector
        if result.get("success"):
            from ths_connector import web_connector
            web_connector.cookie = cookie
            web_connector.connected = True
            logger.info(f"全局web_connector已连接 (Cookie长度: {len(cookie)})")
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/<int:account_id>', methods=['GET'])
def get_account_detail(account_id):
    """获取指定账户详情"""
    try:
        summary = db_store.get_account_summary(account_id)
        # 获取THS持仓
        trader = account_manager.get_trader(account_id)
        if trader:
            pos_result = trader.get_positions()
            if pos_result["success"]:
                summary["ths_positions"] = pos_result["data"].get("positions", [])
        return jsonify({"success": True, "data": summary})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """删除账户"""
    try:
        ok = account_manager.remove_account(account_id)
        if ok:
            return jsonify({"success": True, "message": "账户已删除"})
        return jsonify({"success": False, "message": "删除失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
def rename_account(account_id):
    """重命名账户"""
    try:
        data = request.json
        name = data.get("name", "")
        if not name:
            return jsonify({"success": False, "message": "请输入新名称"})
        account_manager.rename(account_id, name)
        return jsonify({"success": True, "message": "重命名成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/switch', methods=['POST'])
def switch_account():
    """切换当前活跃账户（影响所有交易/持仓/决策模块）"""
    try:
        data = request.json
        account_id = data.get("account_id")
        if not account_id:
            return jsonify({"success": False, "message": "请指定 account_id"})

        # 验证账户存在
        account = db_store.get_account(account_id)
        if not account:
            return jsonify({"success": False, "message": f"账户 {account_id} 不存在"})

        # 切换全局 data_store
        data_store.switch_account(account_id)

        # 同步 auto_trader 的数据存储
        from auto_trader import auto_trader
        auto_trader._ds = data_store
        auto_trader._load_from_data_store()

        logger.info(f"账户切换到: {account.get('name', '')} (id={account_id})")
        return jsonify({
            "success": True,
            "message": f"已切换到账户: {account.get('name', '')}",
            "data": {
                "account_id": account_id,
                "name": account.get("name", ""),
                "balance": account.get("balance", 0),
                "total_assets": account.get("total_assets", 0)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/sync', methods=['POST'])
def sync_all_accounts():
    """同步所有账户数据从同花顺"""
    try:
        results = account_manager.sync_all()
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/sync/<int:account_id>', methods=['POST'])
def sync_account(account_id):
    """同步指定账户（从同花顺拉取最新余额/持仓）"""
    try:
        trader = account_manager.get_trader(account_id)
        if not trader:
            return jsonify({"success": False, "message": "账户未找到或Cookie缺失"})
        if not trader.cookie:
            return jsonify({"success": False, "message": "Cookie为空，请重新绑定账号"})
        result = trader.sync_from_ths()
        if result.get("success"):
            return jsonify({"success": True, "message": "同步成功",
                            "data": {"ky_fund": result.get("ky_fund", 0),
                                     "market_value": result.get("market_value", 0),
                                     "position_count": result.get("position_count", 0)}})
        else:
            msg = result.get("message", "同步失败")
            # 给出更友好的提示
            if "资金账号不存在" in msg or "cookie" in msg.lower() or "401" in str(msg):
                return jsonify({"success": False, "message": f"Cookie已过期，请重新绑定账号 (原错误: {msg})"})
            return jsonify({"success": False, "message": f"同步失败: {msg}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/accounts/summary', methods=['GET'])
def accounts_summary():
    """获取所有账户汇总"""
    try:
        summary = account_manager.get_global_summary()
        return jsonify({"success": True, "data": summary})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/data/backup', methods=['GET'])
def data_backup():
    """下载数据库备份（SQLite文件）"""
    import io, zipfile, time
    try:
        db_path = Path(__file__).parent / "data" / "trading.db"
        if not db_path.exists():
            return jsonify({"success": False, "message": "数据库文件不存在"}), 404

        # 先做一次 checkpoint 确保 WAL 数据写入主库
        from src.db_store import db_store
        try:
            db_store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

        # 打包为 zip 下载
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, "trading.db")
            # 也打包配置文件
            for cfg in ["competition_rules.json", "strategy_params.json"]:
                cfg_path = Path(__file__).parent / "config" / cfg
                if cfg_path.exists():
                    zf.write(cfg_path, f"config/{cfg}")
        buf.seek(0)

        ts = time.strftime("%Y%m%d_%H%M%S")
        from flask import send_file
        return send_file(buf, mimetype="application/zip",
                         as_attachment=True,
                         download_name=f"trading_backup_{ts}.zip")
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/data/restore', methods=['POST'])
def data_restore():
    """从上传的备份恢复数据库"""
    import io, zipfile, shutil
    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "message": "请上传备份文件(.zip)"}), 400

        f = request.files['file']
        buf = io.BytesIO(f.read())
        with zipfile.ZipFile(buf, 'r') as zf:
            names = zf.namelist()
            if "trading.db" not in names:
                return jsonify({"success": False, "message": "ZIP中未找到trading.db"}), 400

            data_dir = Path(__file__).parent / "data"
            db_path = data_dir / "trading.db"
            backup_path = data_dir / "trading_backup_before_restore.db"

            # 备份当前数据库
            if db_path.exists():
                shutil.copy2(db_path, backup_path)

            # 解压新的数据库
            zf.extract("trading.db", str(data_dir))

            # 恢复配置文件
            for name in names:
                if name.startswith("config/"):
                    cfg_path = Path(__file__).parent / name
                    cfg_path.parent.mkdir(parents=True, exist_ok=True)
                    zf.extract(name, str(Path(__file__).parent))

        return jsonify({"success": True, "message": "恢复成功，建议刷新页面或重启服务"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/brain/decide-multi', methods=['POST'])
def brain_decide_multi():
    """AI 为所有账户做决策"""
    try:
        data = request.json
        symbol = data.get("symbol")
        if not symbol:
            return jsonify({"success": False, "message": "请输入股票代码"})

        stock = get_stock_data(symbol)
        if not stock:
            return jsonify({"success": False, "message": f"未找到股票 {symbol}"})

        indicators = calculate_indicators(symbol)

        results = account_manager.decide_for_all(symbol, stock, indicators)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/brain/trade-multi', methods=['POST'])
def brain_trade_multi():
    """AI 为所有账户执行交易"""
    try:
        data = request.json
        symbol = data.get("symbol")
        action = data.get("action")
        price = data.get("price", 0)
        amount = data.get("amount", 100)

        if not symbol or not action:
            return jsonify({"success": False, "message": "参数不完整"})

        results = account_manager.execute_for_all(symbol, action, price, amount)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 缓存管理 API ============

@app.route('/api/cache/status', methods=['GET'])
def cache_status():
    """获取缓存状态"""
    try:
        status = cache.status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cache/clear', methods=['POST'])
def cache_clear():
    """清除缓存"""
    try:
        data = request.json or {}
        pattern = data.get("pattern", "")
        if pattern:
            cache.invalidate(pattern)
        else:
            cache.clear()
        return jsonify({"success": True, "message": "缓存已清除"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 市场情绪 API ============

@app.route('/api/market/sentiment', methods=['GET'])
def market_sentiment():
    """获取市场情绪指标（使用缓存数据）"""
    try:
        result = {"up_count": 0, "down_count": 0, "flat_count": 0, "total": 0}

        try:
            df = get_realtime_quote()
            if df is not None and not df.empty:
                result["up_count"] = int(len(df[df['涨跌幅'] > 0]))
                result["down_count"] = int(len(df[df['涨跌幅'] < 0]))
                result["flat_count"] = int(len(df[df['涨跌幅'] == 0]))
                result["total"] = len(df)

                # 涨停/跌停统计
                result["limit_up"] = int(len(df[df['涨跌幅'] >= 9.9]))
                result["limit_down"] = int(len(df[df['涨跌幅'] <= -9.9]))

                # 成交额统计（亿）
                result["total_amount"] = round(float(df['成交额'].sum()) / 1e8, 2)

                # 市场情绪评分 (-100 ~ 100)
                if result["total"] > 0:
                    ratio = (result["up_count"] - result["down_count"]) / result["total"]
                    result["sentiment_score"] = round(ratio * 100, 1)
                else:
                    result["sentiment_score"] = 0
        except Exception as e:
            logger.error(f"获取市场情绪失败: {e}")

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/sentiment/current', methods=['GET'])
def sentiment_current():
    """获取当前新闻情绪（market_sentiment.json）"""
    try:
        import json as _json
        from pathlib import Path
        f = Path(__file__).parent / "data" / "market_sentiment.json"
        if f.exists():
            data = _json.loads(f.read_text(encoding="utf-8"))
            return jsonify({"success": True, "data": data})
        return jsonify({"success": True, "data": None})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/sentiment/history', methods=['GET'])
def sentiment_history():
    """获取情绪历史数据（sentiment_daily表 + 情绪快照表）"""
    try:
        from db_store import db_store
        days = int(request.args.get('days', 7))
        symbol = request.args.get('symbol', None)

        # 每日情绪跟踪
        daily = []
        try:
            if symbol:
                rows = db_store.conn.execute(
                    "SELECT * FROM sentiment_daily WHERE symbol=? ORDER BY record_date DESC LIMIT ?",
                    (symbol, days * 10)
                ).fetchall()
            else:
                rows = db_store.conn.execute(
                    "SELECT * FROM sentiment_daily ORDER BY record_date DESC LIMIT ?",
                    (days * 50)
                ).fetchall()
            for r in rows:
                daily.append(dict(r))
        except Exception:
            pass

        # 情绪快照（交易时记录）
        snapshots = []
        try:
            snap_rows = db_store.conn.execute(
                "SELECT * FROM sentiment_history ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            for r in snap_rows:
                snapshots.append(dict(r))
        except Exception:
            pass

        # 后悔数据
        regrets = []
        try:
            regret_rows = db_store.conn.execute(
                "SELECT * FROM sentiment_regret ORDER BY record_date DESC LIMIT 20"
            ).fetchall()
            for r in regret_rows:
                regrets.append(dict(r))
        except Exception:
            pass

        return jsonify({
            "success": True,
            "data": {
                "daily": daily,
                "snapshots": snapshots,
                "regrets": regrets,
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/sentiment/scheduler-status', methods=['GET'])
def sentiment_scheduler_status():
    """获取情绪采集调度状态（盘前/午间/收盘后）"""
    try:
        from ai_scheduler import ai_scheduler
        import json as _json
        from pathlib import Path
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        daily_flags_path = Path(__file__).parent / "data" / f"daily_flags_{today_str}.json"
        timestamps = {}
        if daily_flags_path.exists():
            try:
                timestamps = _json.loads(daily_flags_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        data = {
            "pre_market_done": ai_scheduler._pre_market_done,
            "lunch_done": ai_scheduler._lunch_done,
            "after_hours_done": ai_scheduler._after_hours_done,
            "pre_market_result": ai_scheduler._pre_market_result,
            "lunch_sentiment": ai_scheduler._lunch_sentiment,
            "enabled": ai_scheduler.enabled,
            "running": ai_scheduler._running,
            "pre_market_time": timestamps.get("pre_market_time", ""),
            "lunch_time": timestamps.get("lunch_time", ""),
            "after_hours_time": timestamps.get("after_hours_time", ""),
        }
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/market/regime', methods=['GET'])
def market_regime():
    """获取当前市场环境判断"""
    try:
        from market_regime import market_regime_detector
        result = market_regime_detector.detect()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/stock/multi-tf/<symbol>', methods=['GET'])
def stock_multi_tf(symbol):
    """获取多周期指标对比"""
    try:
        from multi_timeframe import multi_timeframe_analyzer
        daily = calculate_indicators(symbol)
        weekly = multi_timeframe_analyzer.get_weekly_indicators(symbol)
        hourly = multi_timeframe_analyzer.get_hourly_indicators(symbol)
        alignment = multi_timeframe_analyzer.analyze_alignment(daily, weekly, hourly)
        return jsonify({
            "success": True,
            "data": {
                "daily": daily,
                "weekly": weekly,
                "hourly": hourly,
                "alignment": alignment
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ============ 数据导出 API ============

@app.route('/api/data/export', methods=['GET'])
def data_export():
    """导出所有交易数据"""
    try:
        data = data_store.export_data()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/data/import', methods=['POST'])
def data_import():
    """导入交易数据"""
    try:
        data = request.json
        data_store.import_data(data)
        return jsonify({"success": True, "message": "数据导入成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/data/reset', methods=['POST'])
def data_reset():
    """重置账户数据"""
    try:
        data = request.json or {}
        initial_balance = data.get("initial_balance", 1000000)
        trader.reset(initial_balance)
        return jsonify({"success": True, "message": f"账户已重置，初始资金: {initial_balance}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/data/trades', methods=['GET'])
def data_trades_query():
    """查询交易记录（支持筛选）"""
    try:
        limit = request.args.get('limit', 50, type=int)
        symbol = request.args.get('symbol', None)
        start_date = request.args.get('start_date', None)
        end_date = request.args.get('end_date', None)
        trades = db_store.get_trades(limit=limit, symbol=symbol,
                                     start_date=start_date, end_date=end_date)
        stats = db_store.get_trade_stats()
        return jsonify({"success": True, "data": {"trades": trades, "stats": stats}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/data/equity-curve', methods=['GET'])
def data_equity_curve():
    """获取收益曲线数据"""
    try:
        days = request.args.get('days', 30, type=int)
        curve = db_store.get_equity_curve(days=days)
        return jsonify({"success": True, "data": curve})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 启动时加载Cookie ====================
# 无论以何种方式启动（直接运行 api.py 或被 desktop_app.py 导入），都加载活跃账户的Cookie
try:
    from ths_connector import web_connector as _wc
    _active_id = db_store.get_active_account_id()
    _active_acc = db_store.get_account(_active_id) if _active_id else None
    if _active_id:
        _raw_cookie = db_store.get_account_cookie(_active_id)
        if _raw_cookie:
            _wc.cookie = _raw_cookie
            # 验证Cookie有效性并设置connected标志
            _fund_result = _wc.get_fund()
            _acc_name = _active_acc.get('name', str(_active_id)) if _active_acc else str(_active_id)
            if _fund_result.get("success"):
                _wc.connected = True
                logging.getLogger(__name__).info(
                    f"已加载账户 '{_acc_name}' 的Cookie (余额: {_fund_result.get('data', '?')})")
            else:
                logging.getLogger(__name__).warning(
                    f"Cookie加载但验证失败: {_fund_result.get('message', '?')}")
except Exception as _e:
    logging.getLogger(__name__).warning(f"启动加载Cookie: {_e}")


# ==================== 启动时设置Server酱通知桥接 ====================
try:
    from server_chan import setup_notification_bridge
    setup_notification_bridge()
except Exception as _e:
    logging.getLogger(__name__).debug(f"Server酱通知桥接: {_e}")


# ==================== 启动时自动连接桌面客户端 ====================
# [已禁用] 不再自动连接桌面客户端，改用Cookie登录
# try:
#     from ths_connector import desktop_connector as _dc
#     if not _dc.connected and _dc.exe_path:
#         import os as _os
#         if _os.path.exists(_dc.exe_path):
#             logging.getLogger(__name__).info(f"尝试自动连接桌面客户端: {_dc.exe_path}")
#             _result = _dc.connect(_dc.exe_path)
#             if _result.get("success"):
#                 logging.getLogger(__name__).info("桌面客户端连接成功！")
#                 trading_mode = "desktop"
#             else:
#                 logging.getLogger(__name__).warning(f"桌面客户端连接失败: {_result.get('message', '')}")
#         else:
#             logging.getLogger(__name__).warning(f"THS客户端路径不存在: {_dc.exe_path}")
# except Exception as _e:
#     logging.getLogger(__name__).debug(f"桌面客户端自动连接: {_e}")


# ==================== 迁移: 文件Cookie → 数据库 ====================
# [已禁用] 旧版文件Cookie迁移逻辑，不再需要
# try:
#     from ths_connector import web_connector as _wc2
#     if not _wc2.cookie:
#         _file_result = _wc2.load_cookie()
#         if _file_result.get("success") and _wc2.cookie:
#             logging.getLogger(__name__).info("发现文件Cookie，正在迁移到数据库...")
#             _ensure_account_in_db(_wc2.cookie, "网页账户")
# except Exception as _e:
#     logging.getLogger(__name__).debug(f"Cookie迁移检查: {_e}")


# ==================== Device Binding API ====================

@app.route('/api/device/bind-code', methods=['POST'])
def device_generate_bind_code():
    """PC端生成手机绑定二维码"""
    try:
        from device_manager import device_manager
        pc_ip = request.host.split(':')[0]
        result = device_manager.generate_bind_code(pc_ip)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/device/bind/claim', methods=['POST'])
def device_claim_bind_code():
    """手机端提交绑定码（免认证）"""
    try:
        from device_manager import device_manager
        data = request.json or {}
        code = data.get("bind_code", "").strip()
        device_name = data.get("device_name", "")
        if not code or len(code) != 6:
            return jsonify({"success": False, "message": "请输入6位数字绑定码"})
        result = device_manager.claim_bind_code(code, device_name)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/device/bind/status', methods=['GET'])
def device_bind_status():
    """PC端轮询扫码状态"""
    try:
        from device_manager import device_manager
        bind_code = request.args.get("bind_code", "")
        if not bind_code:
            return jsonify({"success": False, "message": "缺少bind_code参数"})
        result = device_manager.poll_bind_status(bind_code, timeout=25)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/device/bind/devices', methods=['GET'])
def device_list():
    """列出已绑定设备"""
    try:
        from device_manager import device_manager
        devices = device_manager.list_devices()
        return jsonify({"success": True, "data": devices})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/device/bind/unbind', methods=['POST'])
def device_unbind():
    """解绑设备"""
    try:
        from device_manager import device_manager
        data = request.json or {}
        device_id = data.get("device_id", "")
        if not device_id:
            return jsonify({"success": False, "message": "缺少device_id"})
        result = device_manager.remove_device(device_id)
        return jsonify({"success": result, "message": "解绑成功" if result else "解绑失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/bind/claim', methods=['POST'])
def mobile_bind_claim():
    """手机端绑定入口（免认证）"""
    try:
        from device_manager import device_manager
        data = request.json or {}
        code = data.get("bind_code", "").strip()
        device_name = data.get("device_name", "")
        if not code or len(code) != 6:
            return jsonify({"success": False, "message": "请输入6位数字绑定码"})
        result = device_manager.claim_bind_code(code, device_name)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== Mobile API ====================

@app.route('/api/mobile/notifications', methods=['GET'])
def mobile_notifications():
    """获取通知列表（移动端）"""
    try:
        from notification import notification_manager
        limit = request.args.get('limit', 30, type=int)
        notifications = notification_manager.get_all(limit=limit)
        return jsonify({"success": True, "data": notifications})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/notifications/stream', methods=['GET'])
def mobile_notifications_stream():
    """SSE实时推送通知（移动端）"""
    import queue
    from notification import notification_manager

    def generate():
        q = queue.Queue(maxsize=50)
        notification_manager._sse_queues.append(q)
        try:
            while True:
                try:
                    notification = q.get(timeout=30)
                    yield f"data: {json.dumps(notification, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            if q in notification_manager._sse_queues:
                notification_manager._sse_queues.remove(q)

    return app.response_class(generate(), mimetype='text/event-stream',
                              headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/mobile/account', methods=['GET'])
def mobile_account():
    """获取账户信息（移动端）"""
    try:
        connector, conn_type = get_active_connector()
        if not connector or not connector.connected:
            return jsonify({"success": False, "message": "未连接交易系统"})

        fund_result = connector.get_fund()
        balance = fund_result.get("data", 0) if fund_result.get("success") else 0

        pos_result = connector.get_positions()
        positions = []
        if pos_result.get("success"):
            data = pos_result.get("data", {})
            if isinstance(data, dict):
                positions = data.get("positions", [])

        total_market_value = sum(p.get("market_value", 0) for p in positions)

        return jsonify({
            "success": True,
            "data": {
                "balance": round(balance, 2),
                "market_value": round(total_market_value, 2),
                "total_assets": round(balance + total_market_value, 2),
                "position_count": len(positions),
                "positions": positions[:10]
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/trades', methods=['GET'])
def mobile_trades():
    """获取交易记录（移动端）"""
    try:
        limit = request.args.get('limit', 20, type=int)
        trades = data_store.get_trades()
        if trades:
            trades = trades[-limit:]
        return jsonify({"success": True, "data": trades or []})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/status', methods=['GET'])
def mobile_status():
    """获取系统状态（移动端）"""
    try:
        from ai_scheduler import ai_scheduler
        from auto_trader import auto_trader

        connector, conn_type = get_active_connector()
        connected = connector.connected if connector else False

        return jsonify({
            "success": True,
            "data": {
                "connected": connected,
                "auto_trade": auto_trader.enabled,
                "scheduler_enabled": ai_scheduler.enabled,
                "position_count": len(auto_trader.positions),
                "balance": round(auto_trader.balance, 2),
                "total_assets": round(auto_trader.balance + sum(
                    p.market_value for p in auto_trader.positions.values()
                ), 2)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/ai/chat', methods=['POST'])
def mobile_ai_chat():
    """手机端AI对话（复用现有逻辑）"""
    try:
        data = request.json or {}
        message = data.get("message", "")
        history = data.get("history", [])
        if not message:
            return jsonify({"success": False, "message": "请输入消息"})
        # 直接复用主ai_chat逻辑
        from flask import json as _json
        with app.test_request_context('/api/ai/chat', method='POST',
                                      data=_json.dumps({"message": message, "history": history}),
                                      content_type='application/json'):
            # 注入设备信息
            if hasattr(request, 'device'):
                pass
            return ai_chat()
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/ai/config', methods=['GET'])
def mobile_ai_config_get():
    """手机端获取AI配置"""
    try:
        return jsonify(ai_connector.get_config())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/ai/config', methods=['POST'])
def mobile_ai_config_update():
    """手机端更新AI配置"""
    try:
        data = request.json
        result = ai_connector.update_config(**data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/ai/providers', methods=['GET'])
def mobile_ai_providers():
    """手机端获取AI providers"""
    try:
        return jsonify({"success": True, "data": ai_connector.get_providers()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/ai/providers', methods=['POST'])
def mobile_ai_providers_update():
    """手机端更新AI providers"""
    try:
        data = request.json
        result = ai_connector.update_providers(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/settings', methods=['GET'])
def mobile_settings_get():
    """手机端获取策略配置"""
    try:
        from config import config_manager
        return jsonify({"success": True, "data": config_manager.config})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/settings', methods=['POST'])
def mobile_settings_update():
    """手机端更新策略配置"""
    try:
        from config import config_manager
        data = request.json
        for key, value in data.items():
            if key in config_manager.config:
                if isinstance(config_manager.config[key], dict):
                    config_manager.config[key].update(value)
                else:
                    config_manager.config[key] = value
        config_manager.save()
        return jsonify({"success": True, "message": "配置已更新"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/scheduler/toggle', methods=['POST'])
def mobile_scheduler_toggle():
    """手机端开关AI调度器"""
    try:
        from ai_scheduler import ai_scheduler
        data = request.json or {}
        enable = data.get("enable", not ai_scheduler.enabled)
        if enable:
            ai_scheduler.start()
        else:
            ai_scheduler.stop()
        return jsonify({"success": True, "data": {"enabled": ai_scheduler.enabled}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/mobile/auto-trade/toggle', methods=['POST'])
def mobile_auto_trade_toggle():
    """手机端开关自动交易"""
    try:
        from auto_trader import auto_trader
        data = request.json or {}
        enable = data.get("enable", not auto_trader.enabled)
        auto_trader.enabled = enable
        return jsonify({"success": True, "data": {"enabled": auto_trader.enabled}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== Mobile App Static Files ====================

@app.route('/mobile')
def mobile_app():
    """移动端应用入口"""
    return send_from_directory('static/mobile', 'index.html')


@app.route('/mobile/<path:filename>')
def mobile_static(filename):
    """移动端静态文件"""
    return send_from_directory('static/mobile', filename)


@app.route('/api/sync/github', methods=['POST'])
def sync_from_github():
    """手动从GitHub同步交易记录"""
    try:
        from github_sync import github_sync
        github_sync.sync_to_local()
        return jsonify({"success": True, "message": "同步完成"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ================================================================
#  Cookie 管理 API（多用户支持）
# ================================================================

# ================================================================
#  Cookie 管理 API（多用户支持 - GitHub API 直连）
# ================================================================

import requests as _requests

REPO = "glei4134-collab/ths-trading-ai"
GITHUB_API = "https://api.github.com"


@app.route('/api/debug/github-token', methods=['GET'])
def debug_github_token():
    """调试：检查配置状态"""
    cookie_file = Path(__file__).parent / "config" / "cookies.json"
    return jsonify({
        "cookie_file_exists": cookie_file.exists(),
        "cookie_file_path": str(cookie_file),
        "cwd": os.getcwd(),
        "RAILWAY_ENV": os.environ.get("RAILWAY_ENVIRONMENT", "not set"),
    })


def _gh_api(method, path, data=None):
    """调用 GitHub API（不依赖 gh CLI）"""
    token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_PAT", "") or os.environ.get("COOKIE_TOKEN", "")
    if not token:
        logger.warning("GitHub Token 未设置")
        return None
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    url = f"{GITHUB_API}/repos/{REPO}/{path}"
    if method == "GET":
        r = _requests.get(url, headers=headers, timeout=10)
    elif method == "PATCH":
        r = _requests.patch(url, headers=headers, json=data, timeout=10)
    elif method == "POST":
        r = _requests.post(url, headers=headers, json=data, timeout=10)
    else:
        return None
    if r.status_code in (200, 201):
        return r.json()
    else:
        logger.error(f"GitHub API {method} {path} 失败: {r.status_code} {r.text[:200]}")
        return None


def _get_cookies():
    """从数据库读取 Cookie（主来源）"""
    try:
        accounts = db_store.list_accounts()
        cookies = {}
        for acc in accounts:
            try:
                raw = db_store.get_account_cookie(acc['id'])
                if raw:
                    name = acc.get("name", f"user_{acc['id']}")
                    cookies[name] = raw
            except:
                pass
        if cookies:
            return cookies
    except Exception as e:
        logger.debug(f"数据库Cookie读取: {e}")

    # 备用：从文件读取
    try:
        cookie_file = Path(__file__).parent / "config" / "cookies.json"
        if cookie_file.exists():
            return json.loads(cookie_file.read_text(encoding="utf-8"))
    except:
        pass
    return {}


def _set_cookies(cookies):
    """写入 config/cookies.json"""
    try:
        cookie_file = Path(__file__).parent / "config" / "cookies.json"
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"写入Cookie文件失败: {e}")
        return False


@app.route('/api/cookies', methods=['GET'])
def get_cookies():
    """获取所有用户的Cookie列表（脱敏）"""
    try:
        cookies = _get_cookies()
        cookie_list = []
        for user_id, cookie in cookies.items():
            preview = cookie[:20] + "..." if len(cookie) > 20 else cookie
            cookie_list.append({
                "user_id": user_id,
                "preview": preview,
                "length": len(cookie)
            })
        return jsonify({"success": True, "data": cookie_list})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cookies', methods=['POST'])
def add_cookie():
    """添加或更新用户Cookie"""
    try:
        data = request.json
        user_id = data.get('user_id', '').strip()
        cookie = data.get('cookie', '').strip()

        if not user_id or not cookie:
            return jsonify({"success": False, "message": "缺少user_id或cookie"})

        cookies = _get_cookies()
        cookies[user_id] = cookie

        if _set_cookies(cookies):
            return jsonify({"success": True, "message": f"用户 {user_id} 的Cookie已保存"})
        else:
            return jsonify({"success": False, "message": "保存失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cookies/<user_id>', methods=['DELETE'])
def delete_cookie(user_id):
    """删除用户Cookie"""
    try:
        cookies = _get_cookies()
        if user_id not in cookies:
            return jsonify({"success": False, "message": f"用户 {user_id} 不存在"})

        del cookies[user_id]

        if _set_cookies(cookies):
            return jsonify({"success": True, "message": f"用户 {user_id} 的Cookie已删除"})
        else:
            return jsonify({"success": False, "message": "删除失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cookies/check', methods=['POST'])
def check_cookies():
    """检测所有Cookie有效性（批量）"""
    try:
        results = []

        # 从cookies.json读取
        cookies = _get_cookies()
        if not cookies:
            # 从数据库读取
            try:
                accounts = db_store.list_accounts()
                for acc in accounts:
                    if acc.get("cookie"):
                        from ths_connector import decrypt_cookie
                        raw = decrypt_cookie(acc["cookie"])
                        if raw:
                            cookies[acc.get("name", f"user_{acc['id']}")] = raw
            except:
                pass

        if not cookies:
            # 用默认测试
            cookies = {"owner": ""}

        from ths_connector import THSWebConnector
        from datetime import datetime as _dt
        check_time = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

        for user_id, cookie in cookies.items():
            valid = False
            balance = 0
            error_msg = ""
            try:
                connector = THSWebConnector()
                connector.cookie = cookie
                fund_result = connector.get_fund()
                if fund_result.get("success"):
                    valid = True
                    balance = fund_result.get("data", 0)
                else:
                    error_msg = fund_result.get("message", "查询失败")
            except Exception as e:
                error_msg = str(e)

            results.append({
                "user_id": user_id,
                "valid": valid,
                "balance": round(float(balance), 2) if valid else 0,
                "error": error_msg if not valid else "",
                "check_time": check_time,
                "cookie_preview": cookie[:30] + "..." if len(cookie) > 30 else cookie,
                "cookie_length": len(cookie),
            })

        # 保存检查结果
        try:
            check_file = Path(__file__).parent / "data" / "cookie_check_result.json"
            check_file.parent.mkdir(parents=True, exist_ok=True)
            check_file.write_text(json.dumps({
                "check_time": check_time,
                "results": results,
                "total": len(results),
                "valid_count": sum(1 for r in results if r["valid"]),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except:
            pass

        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cookies/check-and-clean', methods=['POST'])
def check_and_clean_cookies():
    """检查所有Cookie有效性，删除失效的Cookie"""
    try:
        results = []
        deleted = []

        # 获取所有cookie
        cookies = _get_cookies()
        if not cookies:
            return jsonify({"success": True, "data": {"results": [], "deleted": [], "message": "没有Cookie需要检查"}})

        from ths_connector import THSWebConnector
        from datetime import datetime as _dt
        check_time = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

        for user_id, cookie in cookies.items():
            valid = False
            balance = 0
            error_msg = ""
            try:
                connector = THSWebConnector()
                connector.cookie = cookie
                fund_result = connector.get_fund()
                if fund_result.get("success"):
                    valid = True
                    balance = fund_result.get("data", 0)
                else:
                    error_msg = fund_result.get("message", "查询失败")
            except Exception as e:
                error_msg = str(e)

            results.append({
                "user_id": user_id,
                "valid": valid,
                "balance": round(float(balance), 2) if valid else 0,
                "error": error_msg if not valid else "",
                "check_time": check_time,
            })

            # 如果失效，标记删除
            if not valid:
                deleted.append(user_id)

        # 删除失效的Cookie
        if deleted:
            # 从数据库删除
            try:
                accounts = db_store.list_accounts()
                for acc in accounts:
                    name = acc.get("name", f"user_{acc['id']}")
                    if name in deleted:
                        db_store.delete_account(acc['id'])
                        logger.info(f"已从数据库删除失效Cookie: {name}")
            except Exception as e:
                logger.error(f"数据库删除Cookie失败: {e}")

            # 从cookies.json删除
            cookies = _get_cookies()
            for user_id in deleted:
                if user_id in cookies:
                    del cookies[user_id]
            _set_cookies(cookies)

        # 保存检查结果
        try:
            check_file = Path(__file__).parent / "data" / "cookie_check_result.json"
            check_file.parent.mkdir(parents=True, exist_ok=True)
            check_file.write_text(json.dumps({
                "check_time": check_time,
                "results": results,
                "deleted": deleted,
                "total": len(results),
                "valid_count": sum(1 for r in results if r["valid"]),
                "deleted_count": len(deleted),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except:
            pass

        return jsonify({
            "success": True,
            "data": {
                "results": results,
                "deleted": deleted,
                "message": f"检查完成: {len(results)}个Cookie, {len(deleted)}个已删除"
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/cookie/renewal-history', methods=['GET'])
def cookie_renewal_history():
    """获取Cookie续签历史记录"""
    try:
        history_file = Path(__file__).parent / "data" / "cookie_renewal_history.json"
        if history_file.exists():
            history = json.loads(history_file.read_text(encoding="utf-8"))
        else:
            history = []

        # 获取当前cookie列表
        cookies = _get_cookies()
        cookie_list = []
        for user_id, cookie in cookies.items():
            preview = cookie[:30] + "..." if len(cookie) > 30 else cookie
            cookie_list.append({
                "user_id": user_id,
                "preview": preview,
                "length": len(cookie),
            })

        return jsonify({
            "success": True,
            "data": {
                "history": history[-50:],  # 最近50条
                "cookies": cookie_list,
                "total_renewals": len(history),
                "last_renewal": history[-1] if history else None,
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ================================================================
#  Railway 定时任务（APScheduler）
# ================================================================

def _run_cookie_keepalive():
    """定时任务：Cookie续签"""
    try:
        import subprocess
        result = subprocess.run(
            ['python', 'tools/cookie_keeper.py'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            logger.info("Cookie续签成功")
        else:
            logger.warning(f"Cookie续签失败: {result.stderr[:100]}")
    except Exception as e:
        logger.error(f"Cookie续签异常: {e}")


def _run_news_sentiment():
    """定时任务：新闻情绪采集"""
    try:
        import subprocess
        result = subprocess.run(
            ['python', 'tools/news_sentiment.py'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            logger.info("新闻情绪采集完成")
        else:
            logger.warning(f"新闻情绪采集失败: {result.stderr[:100]}")
    except Exception as e:
        logger.error(f"新闻情绪采集异常: {e}")


def _run_auto_trade():
    """定时任务：自动交易"""
    try:
        import subprocess
        result = subprocess.run(
            ['python', 'tools/auto_trade.py'],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info("自动交易完成")
        else:
            logger.warning(f"自动交易失败: {result.stderr[:100]}")
    except Exception as e:
        logger.error(f"自动交易异常: {e}")


def _setup_scheduler():
    """设置定时任务"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler()

        # Cookie续签：每2小时
        scheduler.add_job(
            _run_cookie_keepalive,
            CronTrigger(hour='*/2'),
            id='cookie_keepalive',
            name='Cookie续签'
        )

        # 新闻情绪：工作日每小时
        scheduler.add_job(
            _run_news_sentiment,
            CronTrigger(day_of_week='mon-fri', hour='*/1'),
            id='news_sentiment',
            name='新闻情绪采集'
        )

        # 自动交易：工作日交易时段（9:35, 10:35, 13:05, 14:05）
        scheduler.add_job(
            _run_auto_trade,
            CronTrigger(day_of_week='mon-fri', hour='9,10,13,14', minute=35),
            id='auto_trade',
            name='自动交易'
        )

        scheduler.start()
        logger.info("定时任务已启动: Cookie续签(8h), 新闻情绪(1h), 自动交易(交易时段)")
        return scheduler
    except Exception as e:
        logger.error(f"定时任务启动失败: {e}")
        return None


if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "true").lower() == "true"
    print("=" * 50)
    print("同花顺模拟比赛AI交易系统 - Web前端")
    print("=" * 50)
    print(f"访问地址: http://localhost:{port}")
    print("=" * 50)

    # 后台加载Cookie到web_connector（不阻塞Flask启动）
    def _load_cookie_bg():
        try:
            from ths_connector import web_connector, decrypt_cookie
            active_id = db_store.get_active_account_id()
            active_acc = db_store.get_account(active_id)
            if active_acc and active_acc.get("cookie"):
                raw_cookie = decrypt_cookie(active_acc["cookie"])
                if raw_cookie:
                    web_connector.cookie = raw_cookie
                    test_result = web_connector.get_fund()
                    if test_result["success"]:
                        web_connector.connected = True
                        logger.info(f"✅ 网页API已连接 (余额: {web_connector.ky_fund})")
        except Exception as e:
            logger.debug(f"Cookie加载: {e}")
    threading.Thread(target=_load_cookie_bg, daemon=True).start()

    # 启动每日汇总调度器
    try:
        from notification import daily_scheduler
        daily_scheduler.start()
    except Exception:
        pass

    # 启动AI自动交易调度器（如已启用）
    try:
        from ai_scheduler import ai_scheduler
        if ai_scheduler.enabled:
            ai_scheduler.start()
            logger.info(f"AI自动交易调度器已启动 (间隔={ai_scheduler.interval_minutes}分钟)")
        else:
            logger.info("AI自动交易调度器未启用，跳过启动")
    except Exception as e:
        logger.error(f"AI调度器启动失败: {e}")

    # Cookie云端续签：公开仓库 → GitHub cron 每8小时自动触发
    # 无需本地守护进程

    # ★ 启动时从GitHub同步云端交易记录
    try:
        from github_sync import github_sync
        import threading
        def _sync_on_start():
            try:
                github_sync.sync_to_local()
            except Exception as e:
                logger.debug(f"GitHub同步: {e}")
        threading.Thread(target=_sync_on_start, daemon=True, name="github-sync").start()
    except Exception:
        pass

    # Railway: 启动定时任务
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        _setup_scheduler()

        # 自动从GitHub同步所有配置
        def _sync_config_from_github():
            try:
                import requests
                token = os.environ.get("GITHUB_TOKEN", "")
                if not token:
                    return
                headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
                repo = "glei4134-collab/ths-trading-ai"

                # 要同步的变量列表
                vars_to_sync = ["THS_COOKIE", "AI_API_KEY", "AI_API_URL", "AI_MODEL"]
                for var_name in vars_to_sync:
                    r = requests.get(
                        f"https://api.github.com/repos/{repo}/actions/variables/{var_name}",
                        headers=headers, timeout=10
                    )
                    if r.status_code == 200:
                        value = r.json().get("value", "")
                        if value and not os.environ.get(var_name):
                            os.environ[var_name] = value
                            logger.info(f"✅ 已从GitHub同步 {var_name}")

            except Exception as e:
                logger.debug(f"GitHub配置同步: {e}")
        threading.Thread(target=_sync_config_from_github, daemon=True).start()

    app.run(debug=debug, host="0.0.0.0", port=port)
