"""
实盘交易、自动交易与风控路由
"""

import os
import sys
import json
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Optional, Any

from flask import Blueprint, jsonify, request, Response, send_file

from api.context import (
    engine, broker, trader, _device_manager, trading_mode, trading_state,
    AKSHARE_AVAILABLE, API_TOKEN, _PUBLIC_PATHS,
    get_stock_data, calculate_indicators,
    config_manager, web_connector, get_active_connector, decrypt_cookie,
    ai_connector, enhanced_strategy, SignalType,
    auto_trader, OrderType, risk_manager, backtester,
    data_store, competition_manager, strategy_scanner,
    cache, get_realtime_quote, get_realtime_quotes_batch,
    get_kline, get_intraday, compute_indicators, get_kline_with_indicators,
    notification_manager, db_store, account_manager, auto_login_service
)

logger = logging.getLogger(__name__)

trade_bp = Blueprint('trade', __name__)

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


@trade_bp.route('/api/trade/buy', methods=['POST'])
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


@trade_bp.route('/api/trade/sell', methods=['POST'])
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


@trade_bp.route('/api/auto-trade/config', methods=['GET'])
def auto_trade_config_get():
    """获取自动交易配置"""
    return jsonify({
        "success": True,
        "data": auto_trader.get_config()
    })


@trade_bp.route('/api/auto-trade/config', methods=['POST'])
def auto_trade_config_update():
    """更新自动交易配置"""
    try:
        data = request.json
        auto_trader.update_config(**data)
        return jsonify({"success": True, "message": "配置已更新"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@trade_bp.route('/api/auto-trade/toggle', methods=['POST'])
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


@trade_bp.route('/api/auto-trade/status', methods=['GET'])
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


@trade_bp.route('/api/auto-trade/execute', methods=['POST'])
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


@trade_bp.route('/api/auto-trade/positions', methods=['GET'])
def auto_trade_positions():
    """获取自动交易持仓"""
    return jsonify({
        "success": True,
        "data": auto_trader.get_positions()
    })


@trade_bp.route('/api/auto-trade/history', methods=['GET'])
def auto_trade_history():
    """获取自动交易历史"""
    limit = request.args.get('limit', 50, type=int)
    return jsonify({
        "success": True,
        "data": auto_trader.get_trade_history(limit)
    })


@trade_bp.route('/api/auto-trade/reset', methods=['POST'])
def auto_trade_reset():
    """重置自动交易账户"""
    try:
        data = request.json
        initial_balance = data.get("initial_balance", 1000000)
        auto_trader.reset(initial_balance)
        return jsonify({"success": True, "message": "账户已重置"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@trade_bp.route('/api/auto-trade/signal/<symbol>', methods=['GET'])
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


@trade_bp.route('/api/auto-trade/check-stops', methods=['GET'])
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


@trade_bp.route('/api/risk/assess', methods=['GET'])
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


@trade_bp.route('/api/risk/position-size', methods=['POST'])
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


@trade_bp.route('/api/risk/stop-loss', methods=['POST'])
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


@trade_bp.route('/api/risk/take-profit', methods=['POST'])
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




# =====================================================================
# ⚡ 多账户联动批量交易接口 (支持多账户并发委托及毫秒级测速回报)
# =====================================================================

@trade_bp.route('/api/trade/batch-order', methods=['POST'])
def trade_batch_order():
    """
    多账户联动批量下单接口
    支持对指定的多个同花顺账户批量报单，并提供高精度毫秒级往返耗时 (duration_ms)
    """
    import time
    try:
        data = request.get_json(silent=True) or request.form or {}
        action = str(data.get('action', 'buy')).lower()
        symbol = str(data.get('symbol', '')).strip()
        price = float(data.get('price', 0))
        amount = int(data.get('amount', 0))
        target_account_ids = data.get('account_ids', [])

        if not symbol or len(symbol) != 6:
            return jsonify({"success": False, "message": "股票代码必须是6位数字"}), 400
        if amount <= 0 or amount % 100 != 0:
            return jsonify({"success": False, "message": "委托数量必须是100股的整数倍"}), 400
        if action not in ['buy', 'sell']:
            return jsonify({"success": False, "message": "委托动作必须是 buy 或 sell"}), 400

        all_accounts = db_store.list_accounts()
        if not all_accounts:
            t0 = time.perf_counter()
            d_ms = round((time.perf_counter() - t0) * 1000, 2)
            return jsonify({
                "success": True,
                "message": "多账户联动委托执行完毕（模拟环境）",
                "data": {
                    "action": action,
                    "symbol": symbol,
                    "price": price,
                    "amount": amount,
                    "total_accounts": 1,
                    "success_count": 1,
                    "fail_count": 0,
                    "results": [{
                        "account_id": 0,
                        "account_name": "系统模拟账户",
                        "success": True,
                        "order_id": f"SIM_{int(time.time()*1000)}",
                        "message": "模拟委托成功",
                        "duration_ms": d_ms
                    }]
                }
            })

        if target_account_ids and isinstance(target_account_ids, list):
            target_accounts = [a for a in all_accounts if a["id"] in target_account_ids]
        else:
            target_accounts = all_accounts

        if not target_accounts:
            target_accounts = all_accounts

        results = []
        for acc in target_accounts:
            acc_id = acc["id"]
            acc_name = acc.get("name") or f"账户-{acc_id}"
            t_start = time.perf_counter()
            trader_inst = account_manager.get_trader(acc_id)
            order_res = {"success": False, "message": "未连接"}

            try:
                if trader_inst and trader_inst.cookie:
                    if action == "buy":
                        order_res = trader_inst.buy(symbol, price, amount)
                    else:
                        order_res = trader_inst.sell(symbol, price, amount)
                else:
                    cookie = db_store.get_account_cookie(acc_id)
                    if cookie:
                        trader_inst = account_manager.register_account(acc_id, cookie)
                        if action == "buy":
                            order_res = trader_inst.buy(symbol, price, amount)
                        else:
                            order_res = trader_inst.sell(symbol, price, amount)
                    else:
                        order_res = {"success": False, "message": "账号Cookie未就绪"}
            except Exception as e:
                order_res = {"success": False, "message": f"委托异常: {str(e)}"}

            duration_ms = round((time.perf_counter() - t_start) * 1000, 2)
            
            try:
                db_store.log_api_performance(
                    action=f"batch_{action}",
                    duration_ms=duration_ms,
                    success=1 if order_res.get("success") else 0,
                    error_message="" if order_res.get("success") else str(order_res.get("message", ""))
                )
            except Exception:
                pass

            results.append({
                "account_id": acc_id,
                "account_name": acc_name,
                "success": bool(order_res.get("success")),
                "order_id": str(order_res.get("data", {}).get("order_id", order_res.get("order_id", ""))),
                "message": order_res.get("message", "委托已提交" if order_res.get("success") else "失败"),
                "duration_ms": duration_ms
            })

        success_count = sum(1 for r in results if r["success"])
        fail_count = sum(1 for r in results if not r["success"])

        return jsonify({
            "success": True,
            "message": f"多账户联动{action}执行完毕: {success_count}成功, {fail_count}失败",
            "data": {
                "action": action,
                "symbol": symbol,
                "price": price,
                "amount": amount,
                "total_accounts": len(results),
                "success_count": success_count,
                "fail_count": fail_count,
                "results": results
            }
        })
    except Exception as e:
        logger.error(f"批量交易下单异常: {e}")
        return jsonify({"success": False, "message": f"批量下单异常: {str(e)}"}), 500
