"""
账户、资金与同花顺连接网关路由
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

account_bp = Blueprint('account', __name__)

@account_bp.route('/api/account/positions', methods=['GET'])
def get_positions():
    """获取持仓 — 网页API优先"""
    try:
        active_id = db_store.get_active_account_id()
        if not web_connector.cookie and active_id:
            c = db_store.get_account_cookie(active_id)
            if c:
                web_connector.cookie = c
                web_connector.connected = True

        # 优先级1：通过 account_manager 的专属 trader 实时查询（独立维护 cookie 与 gdzh）
        from account_manager import account_manager
        accounts = db_store.list_accounts()
        if accounts:
            active_acc = next((a for a in accounts if a["id"] == active_id), accounts[0])
            trader = account_manager.get_trader(active_acc["id"])
            if trader and trader.connected:
                result = trader.get_positions()
                if result["success"]:
                    return jsonify({
                        "success": True,
                        "data": result["data"]
                    })

        # 优先级2：全局web_connector兜底
        if web_connector.connected and web_connector.cookie:
            result = web_connector.get_positions()
            if result["success"]:
                return jsonify({
                    "success": True,
                    "data": result["data"]
                })
            logger.warning(f"网页API获取持仓失败: {result.get('message')}")

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


@account_bp.route('/api/account/balance', methods=['GET'])
def get_balance():
    """获取余额 — 统一金融结算引擎"""
    try:
        active_id = db_store.get_active_account_id()
        accounts = db_store.list_accounts()
        active = next((a for a in accounts if a["id"] == active_id), (accounts[0] if accounts else None))
        
        # 初始本金硬约束：默认200000.0，若数据库记录低于15万则自动强制修正为20万
        raw_init = float(active.get("initial_balance", 200000.0) if active else 200000.0) or 200000.0
        initial_balance = 200000.0 if raw_init < 150000 else raw_init

        if not web_connector.cookie and active_id:
            c = db_store.get_account_cookie(active_id)
            if c:
                web_connector.cookie = c
                web_connector.connected = True

        fund = None
        balance = 0.0
        total = 0.0
        market_val = 0.0
        connected = False

        # 优先级1：通过 account_manager 的专属 trader 实时查询
        if accounts and active:
            from account_manager import account_manager
            trader = account_manager.get_trader(active["id"])
            if trader and trader.connected:
                pos_result = trader.get_positions()
                if pos_result.get("success") and pos_result.get("data"):
                    fund = pos_result["data"].get("fund", {})
                    balance = float(fund.get("available", 0))
                    total = float(fund.get("total_assets", 0))
                    market_val = float(fund.get("market_value", 0))
                    if total <= 0:
                        total = balance + market_val
                    if total > 0 or balance > 0:
                        connected = True

        # 优先级2：全局web_connector兜底
        if not connected and web_connector.connected and web_connector.cookie and not web_connector.cookie.startswith("sandbox_"):
            result = web_connector.get_positions()
            if result.get("success") and result.get("data"):
                fund = result["data"].get("fund", {})
                balance = float(fund.get("available", 0))
                total = float(fund.get("total_assets", 0))
                market_val = float(fund.get("market_value", 0))
                if total <= 0:
                    total = balance + market_val
                if total > 0 or balance > 0:
                    connected = True

        # 优先级3：最终回退 DB 缓存数据
        if not connected:
            if not accounts or not active:
                return jsonify({"success": False, "message": "未绑定任何账户"})
            balance = float(active.get("ky_fund", 0) or active.get("balance", 0))
            market_val = float(active.get("market_value", 0))
            total = float(active.get("total_assets", 0) or balance + market_val)

        # 统一标准金融结算引擎
        # 1. 累计总盈亏与累计总收益率 (严格以标准20万本金为基准)
        raw_profit = fund.get("total_profit") if fund else None
        if raw_profit is not None and abs(float(raw_profit)) > 0:
            total_profit = float(raw_profit)
        else:
            total_profit = round(total - initial_balance, 2)
        total_return = round((total_profit / initial_balance) * 100, 2) if initial_balance > 0 else 0.0

        # 2. 真正的今日盈亏与今日收益率 (基于日初/昨收基准)
        today_pnl = db_store.get_today_pnl(active["id"] if active else 1, total)
        today_profit = today_pnl["today_profit"]
        today_profit_rate = today_pnl["today_profit_rate"]

        return jsonify({
            "success": True,
            "data": {
                "balance": round(balance, 2),
                "available": round(balance, 2),
                "market_value": round(market_val, 2),
                "total_assets": round(total, 2),
                "initial_balance": round(initial_balance, 2),
                "baseline_assets": today_pnl["baseline_assets"],
                "today_profit": round(today_profit, 2),
                "today_profit_rate": round(today_profit_rate, 2),
                "total_profit": total_profit,
                "total_return": total_return,
                "connected": connected,
                "raw": fund or {}
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/account/offsets', methods=['GET'])
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


@account_bp.route('/api/account/trades', methods=['GET'])
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


@account_bp.route('/api/account/pending-orders', methods=['GET'])
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


@account_bp.route('/api/account/cancel-order', methods=['POST'])
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


@account_bp.route('/api/account/cancel-all', methods=['POST'])
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


@account_bp.route('/api/connection/status', methods=['GET'])
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


@account_bp.route('/api/connection/desktop', methods=['POST'])
def connect_desktop():
    """[已废弃] 桌面客户端连接已移除，请使用Cookie登录"""
    return jsonify({"success": False, "message": "桌面客户端已废弃，请使用Cookie登录"})


@account_bp.route('/api/connection/auto', methods=['POST'])
def auto_connect():
    """[已废弃] 桌面客户端自动连接已移除，请使用Cookie登录"""
    return jsonify({"success": False, "message": "桌面客户端已废弃，请使用Cookie登录"})


@account_bp.route('/api/connection/credentials', methods=['POST'])
def save_credentials():
    """[已废弃] 桌面客户端凭据已移除"""
    return jsonify({"success": False, "message": "桌面客户端已废弃，请使用Cookie登录"})


@account_bp.route('/api/connection/credentials', methods=['GET'])
def get_credentials_status():
    """[已废弃] 桌面客户端凭据已移除"""
    return jsonify({"success": True, "has_credentials": False})


@account_bp.route('/api/connection/info', methods=['GET'])
def connection_info():
    """获取连接状态详情"""
    try:
        from ths_connector import get_connection_info
        return jsonify(get_connection_info())
    except Exception as e:
        return jsonify({"mode": "none", "connected": False, "message": str(e)})


@account_bp.route('/api/connection/web/save-cookie', methods=['POST'])
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


@account_bp.route('/api/connection/web/auto-get', methods=['POST'])
def auto_get_cookie():
    """自动获取Cookie（在现有浏览器中打开新标签页）"""
    global trading_mode

    try:
        import threading
        import webbrowser
        import time

        def open_ths_page():
            # 在用户的默认浏览器中打开同花顺新版官方交易页面
            webbrowser.open("https://mncg.10jqka.com.cn/cgiwt/index/index")

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


@account_bp.route('/api/connection/web/load', methods=['POST'])
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


@account_bp.route('/api/connection/disconnect', methods=['POST'])
def disconnect():
    """断开连接"""
    global trading_mode

    web_connector.connected = False
    trading_mode = "simulation"

    return jsonify({"success": True, "message": "已断开连接，切换到模拟模式"})


@account_bp.route('/api/connection/web/query-stock/<symbol>', methods=['GET'])
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


@account_bp.route('/api/connection/web/summary', methods=['GET'])
def ths_account_summary():
    """获取同花顺账户完整摘要"""
    try:
        if not web_connector.connected:
            return jsonify({"success": False, "message": "未连接同花顺"})

        result = web_connector.get_account_summary()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/connection/web/validate', methods=['GET'])
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


@account_bp.route('/api/connection/web/refresh', methods=['POST'])
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


@account_bp.route('/api/ths/qrcode/generate', methods=['POST'])
def qr_generate():
    """生成同花顺扫码登录二维码"""
    try:
        from ths_connector import qr_login_manager
        result = qr_login_manager.generate_qrcode()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/ths/qrcode/poll', methods=['GET'])
def qr_poll():
    """轮询扫码登录状态 (多用户并发会话隔离 + 唯一账号防重复绑定)"""
    try:
        session_id = request.args.get('session_id', '')
        qrcode_id = request.args.get('qrcode_id', '')
        from ths_connector import qr_login_manager
        result = qr_login_manager.poll_status(session_id=session_id, qrcode_id=qrcode_id)
        # 如果扫码成功，自动调用 account_manager 唯一识别绑定或原地续签
        if result.get('success') and result.get('status') == 'confirmed':
            cookie = result.get('cookie', '')
            if cookie:
                from account_manager import account_manager
                bind_result = account_manager.add_account("", cookie)
                result['cookie_saved'] = bind_result.get('success', False)
                result['save_message'] = bind_result.get('message', '')
                result['account'] = bind_result.get('data')
                result['renewed'] = bind_result.get('renewed', False)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "status": "error", "message": str(e)})


@account_bp.route('/api/ths/qrcode/cancel', methods=['POST'])
def qr_cancel():
    """取消并释放二维码会话 (多用户并发隔离)"""
    try:
        data = request.json or {}
        session_id = data.get('session_id', '')
        qrcode_id = data.get('qrcode_id', '')
        from ths_connector import qr_login_manager
        qr_login_manager.cancel_qrcode(session_id=session_id, qrcode_id=qrcode_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": True})


@account_bp.route('/api/ths/qrcode/auto-load', methods=['POST'])
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


@account_bp.route('/api/cookie/health', methods=['GET'])
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


@account_bp.route('/api/connection/web/account-detail', methods=['GET'])
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


@account_bp.route('/api/ths/auto_login/start', methods=['POST'])
def start_ths_auto_login():
    """启动同花顺一键扫码/自动登录（唤起Edge浏览器扫码，自动截获并更新Cookie）"""
    try:
        data = request.get_json(silent=True) or {}
        account_id = data.get("account_id")
        timeout_sec = int(data.get("timeout_sec", 180))
        res = auto_login_service.start_auto_login(account_id=account_id, timeout_sec=timeout_sec)
        return jsonify(res)
    except Exception as e:
        logger.error(f"启动一键扫码登录失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/ths/auto_login/status', methods=['GET'])
def get_ths_auto_login_status():
    """查询一键扫码登录的实时状态与进度"""
    try:
        status = auto_login_service.get_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/ths/auto_login/cancel', methods=['POST'])
def cancel_ths_auto_login():
    """取消当前自动登录"""
    try:
        auto_login_service.cancel_login()
        return jsonify({"success": True, "message": "已取消自动登录"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/ths/auto_login/silent_refresh', methods=['POST'])
def silent_refresh_ths_cookie():
    """在后台静默免扫码刷新 Cookie（利用 30 天持久化凭证）"""
    try:
        data = request.get_json(silent=True) or {}
        account_id = data.get("account_id")
        res = auto_login_service.silent_refresh(account_id=account_id)
        return jsonify(res)
    except Exception as e:
        logger.error(f"静默续签失败: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/ths/login/credential', methods=['POST'])
def login_ths_by_credential():
    """
    手机端专用的同花顺登录/绑定接口
    支持手机号/用户名/密码验证及 Cookie 凭据免密导入
    """
    try:
        data = request.get_json(silent=True) or request.form or {}
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        cookie = (data.get('cookie') or '').strip()
        account_name = (data.get('account_name') or '').strip()

        if cookie:
            res = account_manager.add_account(account_name, cookie)
            if res.get("success"):
                from ths_connector import web_connector
                web_connector.cookie = cookie
                web_connector.connected = True
            return jsonify(res)

        if username and password:
            if "escapename=" in username or "ticket=" in username:
                res = account_manager.add_account(account_name, username)
                return jsonify(res)
            return jsonify({
                "success": False,
                "message": "同花顺安全机制提示：同花顺官方要求在手机浏览器完成滑块/短信安全验证。请点击下方【🌐 手机打开同花顺官网】登录后一键同步，或使用【🔑 Cookie/凭据录入】秒级绑定。"
            })

        return jsonify({"success": False, "message": "请输入手机号或同花顺用户名"})
    except Exception as e:
        logger.error(f"凭据登录异常: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/accounts', methods=['GET'])
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


@account_bp.route('/api/Accounts/quick-bind', methods=['GET', 'POST', 'OPTIONS'])
@account_bp.route('/api/accounts/quick-bind', methods=['GET', 'POST', 'OPTIONS'])
def quick_bind_ths_account():
    """
    方案 B: 浏览器书签栏 (Bookmarklet) / 控制台单行命令一键免粘贴同步 Cookie 接口
    支持从 https://mncg.10jqka.com.cn 通过 window.open GET 或 POST 跨站直传 Cookie，
    自动识别新账号（按 1, 2, 3 顺序命名）或已有账号（按 gdzh 原地无缝续签同步）
    """
    if request.method == 'OPTIONS':
        resp = Response("", status=200)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    try:
        cookie = ""
        if request.method == 'GET':
            cookie = (request.args.get("cookie") or "").strip()
        else:
            data = request.get_json(silent=True) or request.form or {}
            cookie = (data.get("cookie") or request.args.get("cookie") or "").strip()

        if not cookie or len(cookie) < 15:
            msg = "未检测到有效同花顺 Cookie，请确认已在 mncg.10jqka.com.cn 登录"
            if request.method == 'GET':
                return Response(_render_quick_bind_html(False, msg), mimetype='text/html')
            resp = jsonify({"success": False, "message": msg})
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

        if "userid=ths_gu_" in cookie:
            msg = "❌ 检测到当前处于未登录访客状态 (ths_gu_)！<br><br>请在同花顺网页先登录您的正式账号，并<b>点击【同花顺交易区】</b>进入交易台，然后再点击此同步按钮。"
            if request.method == 'GET':
                return Response(_render_quick_bind_html(False, msg), mimetype='text/html')
            resp = jsonify({"success": False, "message": msg})
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

        result = account_manager.add_account("", cookie)
        if request.method == 'GET' and request.args.get("format") != "json":
            return Response(
                _render_quick_bind_html(result.get("success", False), result.get("message", ""), result.get("data")),
                mimetype='text/html'
            )

        resp = jsonify(result)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        if request.method == 'GET' and request.args.get("format") != "json":
            return Response(_render_quick_bind_html(False, f"同步异常: {e}"), mimetype='text/html')
        resp = jsonify({"success": False, "message": str(e)})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


def _render_quick_bind_html(success: bool, message: str, acc_data: dict = None) -> str:
    """渲染书签栏一键同步弹出小窗的自动关闭反馈页"""
    icon = "✅" if success else "❌"
    color = "#3fb950" if success else "#f85149"
    title = "同花顺 Cookie 同步成功" if success else "Cookie 同步失败"
    payload_json = json.dumps({"type": "THS_COOKIE_BOUND", "success": success, "message": message, "account": acc_data or {}}, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body {{ background:#0d1117; color:#f0f6fc; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; text-align:center; }}
.card {{ background:#161b22; border:1px solid {color}; border-radius:12px; padding:24px 28px; max-width:360px; box-shadow:0 12px 32px rgba(0,0,0,0.6); }}
.icon {{ font-size:40px; margin-bottom:10px; }}
.title {{ font-size:16px; font-weight:700; color:{color}; margin-bottom:8px; }}
.msg {{ font-size:13px; color:#c9d1d9; line-height:1.6; margin-bottom:16px; word-break:break-all; }}
.sub {{ font-size:11px; color:#8b949e; }}
</style>
</head>
<body>
<div class="card">
  <div class="icon">{icon}</div>
  <div class="title">{title}</div>
  <div class="msg">{message}</div>
  <div class="sub">{'窗口将在 2 秒后自动关闭...' if success else '请登录同花顺模拟炒股页面后重试'}</div>
</div>
<script>
try {{
  if (window.opener) {{
    window.opener.postMessage({payload_json}, '*');
  }}
}} catch(e) {{}}
if ({'true' if success else 'false'}) {{
  setTimeout(function() {{ window.close(); }}, 2000);
}}
</script>
</body>
</html>"""


@account_bp.route('/api/accounts', methods=['POST'])
def add_ths_account():
    """添加同花顺比赛账号（绑定Cookie，按存入顺序自动命名为 1, 2, 3...）"""
    try:
        data = request.json or {}
        cookie = data.get("cookie", "").strip()
        name = data.get("name", "").strip() or str(len(db_store.list_accounts()) + 1)
        if not cookie:
            return jsonify({"success": False, "message": "请输入Cookie"})
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


@account_bp.route('/api/accounts/<int:account_id>', methods=['GET'])
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


@account_bp.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """删除账户"""
    try:
        ok = account_manager.remove_account(account_id)
        if ok:
            return jsonify({"success": True, "message": "账户已删除"})
        return jsonify({"success": False, "message": "删除失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/accounts/<int:account_id>', methods=['PUT'])
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


@account_bp.route('/api/accounts/switch', methods=['POST'])
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

        # 同步切换全局 web_connector 的 cookie
        from ths_connector import web_connector
        cookie = db_store.get_account_cookie(account_id)
        if cookie:
            web_connector.cookie = cookie
            web_connector.connected = True
        else:
            web_connector.connected = False

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


@account_bp.route('/api/accounts/sync', methods=['POST'])
def sync_all_accounts():
    """同步所有账户数据从同花顺"""
    try:
        results = account_manager.sync_all()
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/accounts/sync/<int:account_id>', methods=['POST'])
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


@account_bp.route('/api/accounts/summary', methods=['GET'])
def accounts_summary():
    """获取所有账户汇总"""
    try:
        summary = account_manager.get_global_summary()
        return jsonify({"success": True, "data": summary})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@account_bp.route('/api/cookies', methods=['GET'])
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


@account_bp.route('/api/cookies', methods=['POST'])
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


@account_bp.route('/api/cookies/<user_id>', methods=['DELETE'])
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


@account_bp.route('/api/cookies/check', methods=['POST'])
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


@account_bp.route('/api/cookies/check-and-clean', methods=['POST'])
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


@account_bp.route('/api/cookie/renewal-history', methods=['GET'])
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




# =====================================================================
# 📱 同花顺官方标准认证对接端点 (验证码登录 / 账号密码登录)
# =====================================================================

@account_bp.route('/api/ths/auth/send-sms', methods=['POST'])
def ths_auth_send_sms():
    """下发同花顺手机短信验证码"""
    try:
        data = request.get_json(silent=True) or request.form or {}
        phone = str(data.get('phone', '')).strip()
        import re
        if not re.match(r'^1[3-9]\d{9}$', phone):
            return jsonify({"success": False, "message": "请输入有效的11位中国大陆手机号"}), 400

        # 调用底层交易执行引擎的短信下发接口
        send_func = getattr(auto_login_service, 'send_phone_verify_code', None)
        if not callable(send_func):
            send_func = getattr(web_connector, 'send_phone_verify_code', None)
        if not callable(send_func):
            try:
                from ths_connector import send_phone_verify_code
                send_func = send_phone_verify_code
            except Exception:
                pass

        if callable(send_func):
            result = send_func(phone)
            if isinstance(result, dict):
                return jsonify(result)
            return jsonify({"success": True, "message": "验证码已下发至手机，请查收"})
        else:
            return jsonify({
                "success": False,
                "message": "底层交易网关短信服务正在准备中，请联系管理员或稍后重试"
            }), 503
    except Exception as e:
        logger.error(f"下发短信验证码异常: {e}")
        return jsonify({"success": False, "message": f"下发短信失败: {str(e)}"}), 500


@account_bp.route('/api/ths/auth/login-sms', methods=['POST'])
def ths_auth_login_sms():
    """同花顺手机验证码快捷登录与自动绑定"""
    try:
        data = request.get_json(silent=True) or request.form or {}
        phone = str(data.get('phone', '')).strip()
        code = str(data.get('code', '')).strip()

        import re
        if not re.match(r'^1[3-9]\d{9}$', phone):
            return jsonify({"success": False, "message": "手机号格式不正确"}), 400
        if not code or len(code) < 4:
            return jsonify({"success": False, "message": "请输入4~6位短信验证码"}), 400

        # 调用底层交易执行引擎手机验证码登录
        login_func = getattr(auto_login_service, 'login_by_phone_code', None)
        if not callable(login_func):
            login_func = getattr(web_connector, 'login_by_phone_code', None)
        if not callable(login_func):
            try:
                from ths_connector import login_by_phone_code
                login_func = login_by_phone_code
            except Exception:
                pass

        if callable(login_func):
            res = login_func(phone, code)
            if not isinstance(res, dict) or not res.get("success"):
                return jsonify(res if isinstance(res, dict) else {"success": False, "message": "验证码登录失败"}), 400

            # 登录成功，提取 cookie 并持久化账户
            cookie = res.get("cookie") or res.get("data", {}).get("cookie")
            account_name = f"手机账户-{phone[-4:]}"
            if cookie:
                acc_info = _ensure_account_in_db(cookie, account_name)
                return jsonify({
                    "success": True,
                    "message": "同花顺登录成功，已自动绑定入库！",
                    "data": {
                        "account_id": acc_info.get("id"),
                        "name": acc_info.get("name", account_name),
                        "phone": phone
                    }
                })
            return jsonify({"success": True, "message": "登录成功", "data": res.get("data", {})})
        else:
            return jsonify({
                "success": False,
                "message": "底层交易网关短信登录服务正在对接中"
            }), 503
    except Exception as e:
        logger.error(f"手机验证码登录异常: {e}")
        return jsonify({"success": False, "message": f"登录失败: {str(e)}"}), 500


@account_bp.route('/api/ths/auth/login-pwd', methods=['POST'])
def ths_auth_login_pwd():
    """同花顺账号密码标准登录与自动绑定"""
    try:
        data = request.get_json(silent=True) or request.form or {}
        account = str(data.get('account', '')).strip()
        password = str(data.get('password', '')).strip()

        if not account or not password:
            return jsonify({"success": False, "message": "请输入同花顺账号/手机号及密码"}), 400

        # 调用底层交易执行引擎账号密码登录
        login_func = getattr(auto_login_service, 'login_by_password', None)
        if not callable(login_func):
            login_func = getattr(web_connector, 'login_by_password', None)
        if not callable(login_func):
            try:
                from ths_connector import login_by_password
                login_func = login_by_password
            except Exception:
                pass

        if callable(login_func):
            res = login_func(account, password)
            if not isinstance(res, dict) or not res.get("success"):
                return jsonify(res if isinstance(res, dict) else {"success": False, "message": "账号密码登录失败"}), 400

            cookie = res.get("cookie") or res.get("data", {}).get("cookie")
            account_name = f"账号-{account[-4:]}" if len(account) >= 4 else account
            if cookie:
                acc_info = _ensure_account_in_db(cookie, account_name)
                return jsonify({
                    "success": True,
                    "message": "同花顺账号登录成功，已自动绑定入库！",
                    "data": {
                        "account_id": acc_info.get("id"),
                        "name": acc_info.get("name", account_name)
                    }
                })
            return jsonify({"success": True, "message": "登录成功", "data": res.get("data", {})})
        else:
            return jsonify({
                "success": False,
                "message": "底层交易网关账密登录服务正在对接中"
            }), 503
    except Exception as e:
        logger.error(f"账号密码登录异常: {e}")
        return jsonify({"success": False, "message": f"登录失败: {str(e)}"}), 500
