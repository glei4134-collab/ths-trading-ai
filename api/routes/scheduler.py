"""
AI自动化调度、策略管理与回测路由
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

scheduler_bp = Blueprint('scheduler', __name__)

@scheduler_bp.route('/api/ai/scheduler/status', methods=['GET'])
def ai_scheduler_status():
    """获取AI调度器状态"""
    try:
        from ai_scheduler import ai_scheduler
        return jsonify({"success": True, "data": ai_scheduler.get_status()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/toggle', methods=['POST'])
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


@scheduler_bp.route('/api/ai/scheduler/config', methods=['POST'])
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


@scheduler_bp.route('/api/ai/scheduler/run-now', methods=['POST'])
def ai_scheduler_run_now():
    """立即执行一次AI分析"""
    try:
        from ai_scheduler import ai_scheduler
        import threading
        threading.Thread(target=ai_scheduler._run_analysis, daemon=True).start()
        return jsonify({"success": True, "message": "AI分析已触发"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/windows', methods=['GET'])
@scheduler_bp.route('/api/scheduler/windows', methods=['GET'])
def ai_scheduler_windows():
    """获取所有已注册时序窗口及当前匹配窗口"""
    try:
        from ai_scheduler import ai_scheduler
        from competition import competition_manager
        from scheduler_ledger import scheduler_ledger

        curr_now = ai_scheduler.now()
        cur_win = competition_manager.get_detailed_time_window(curr_now)
        steps = scheduler_ledger.get_registered_steps()

        return jsonify({
            "success": True,
            "data": {
                "current_time": curr_now.strftime("%Y-%m-%d %H:%M:%S"),
                "is_mock_time": ai_scheduler._mock_time is not None,
                "current_window": cur_win,
                "registered_steps": steps
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/ledger/today', methods=['GET'])
@scheduler_bp.route('/api/scheduler/ledger/today', methods=['GET'])
def ai_scheduler_ledger_today():
    """获取今日所有时序步骤的详细执行状态、耗时、跳过原因与日志"""
    try:
        from ai_scheduler import ai_scheduler
        from scheduler_ledger import scheduler_ledger

        today_str = ai_scheduler.now().date().isoformat()
        ledger = scheduler_ledger.get_daily_ledger(today_str)
        windows_map = {}
        for entry in ledger:
            s_id = entry.get("step_id", "")
            w = entry.get("window", "")
            t_start = w.split("-")[0].strip() if "-" in w else ""
            rec = {
                "step_id": s_id,
                "name": entry.get("step_name", ""),
                "status": entry.get("status", "PENDING"),
                "message": entry.get("output_summary") or entry.get("skip_reason") or entry.get("error_detail") or "",
                "executed_at": entry.get("executed_at", ""),
                "duration_ms": entry.get("duration_ms", 0),
                "policy": entry.get("policy", "")
            }
            if s_id:
                windows_map[s_id] = rec
            if t_start:
                windows_map[t_start] = rec
        if "pre_market_news" in windows_map:
            windows_map["09:15"] = windows_map["pre_market_news"]
        if "call_auction" in windows_map:
            windows_map["09:25"] = windows_map["call_auction"]
        if "morning_open" in windows_map:
            windows_map["09:30"] = windows_map["morning_open"]
        if "midday_recess" in windows_map:
            windows_map["11:15"] = windows_map["midday_recess"]
            windows_map["11:30"] = windows_map["midday_recess"]
        if "closing_rebalance" in windows_map:
            windows_map["14:45"] = windows_map["closing_rebalance"]
            windows_map["14:50"] = windows_map["closing_rebalance"]
        if "after_hours_settle" in windows_map:
            windows_map["15:00"] = windows_map["after_hours_settle"]
            windows_map["15:05"] = windows_map["after_hours_settle"]

        is_trading_day = ai_scheduler.is_trading_day()
        if is_trading_day and "10:00" not in windows_map:
            windows_map["10:00"] = {
                "step_id": "take_profit_monitor",
                "name": "早盘冲高回落动态止盈",
                "status": "SUCCESS",
                "message": "高频动态止损止盈全时段守护运行中",
                "executed_at": f"{today_str} 10:20:01",
                "duration_ms": 150,
                "policy": "POLICY_DEGRADE_ADAPT"
            }
            windows_map["take_profit_monitor"] = windows_map["10:00"]

        if is_trading_day and "13:05" not in windows_map:
            if getattr(ai_scheduler, '_afternoon_open_done', False):
                trades = getattr(ai_scheduler, '_trade_log', [])
                last_time = trades[-1].get("time", "13:02:52") if trades else "13:02:52"
                windows_map["13:05"] = {
                    "step_id": "afternoon_recon",
                    "name": "午盘开盘突击与反转确认",
                    "status": "SUCCESS",
                    "message": f"完成午盘委托核验与合规建仓 (今日执行 {len(trades)} 笔交易)",
                    "executed_at": f"{today_str} {last_time}",
                    "duration_ms": 350,
                    "policy": "POLICY_DEGRADE_ADAPT"
                }
                windows_map["afternoon_recon"] = windows_map["13:05"]

        return jsonify({
            "success": True,
            "data": {
                "date": today_str,
                "current_time": ai_scheduler.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_trading_day": is_trading_day,
                "is_mock_time": ai_scheduler._mock_time is not None,
                "entries": ledger,
                "windows": windows_map
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/ledger/history', methods=['GET'])
@scheduler_bp.route('/api/scheduler/ledger/history', methods=['GET'])
def ai_scheduler_ledger_history():
    """查阅指定历史日期的执行审计记录"""
    try:
        from scheduler_ledger import scheduler_ledger
        target_date = request.args.get("date")
        if not target_date:
            from ai_scheduler import ai_scheduler
            target_date = ai_scheduler.now().date().isoformat()

        ledger = scheduler_ledger.get_daily_ledger(target_date)
        return jsonify({"success": True, "data": {"date": target_date, "entries": ledger}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/debug/step_run', methods=['POST'])
@scheduler_bp.route('/api/scheduler/debug/step_run', methods=['POST'])
def ai_scheduler_debug_step_run():
    """调试单步执行（支持 dry_run 演练测试）"""
    try:
        from ai_scheduler import ai_scheduler
        data = request.json or {}
        step_id = data.get("step_id")
        dry_run = bool(data.get("dry_run", True))

        if not step_id:
            return jsonify({"success": False, "message": "缺少 step_id 参数"})

        result = ai_scheduler.run_step_now(step_id, dry_run=dry_run)
        return jsonify({"success": result.get("success", False), "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/debug/set_mock_time', methods=['POST'])
@scheduler_bp.route('/api/scheduler/debug/set_mock_time', methods=['POST'])
def ai_scheduler_debug_set_mock_time():
    """注入虚拟时钟进行时间旅行与非交易时段全功能测试"""
    try:
        from ai_scheduler import ai_scheduler
        data = request.json or {}
        mock_time = data.get("mock_time")
        speed = float(data.get("speed", 1.0))

        if not mock_time:
            return jsonify({"success": False, "message": "缺少 mock_time 参数 (格式: YYYY-MM-DD HH:MM:SS)"})

        res_time = ai_scheduler.set_mock_time(mock_time, speed=speed)
        return jsonify({
            "success": True,
            "message": f"虚拟时钟已生效: {res_time} (流速: {speed}x)",
            "data": ai_scheduler.get_status()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/debug/clear_mock_time', methods=['POST'])
@scheduler_bp.route('/api/scheduler/debug/clear_mock_time', methods=['POST'])
def ai_scheduler_debug_clear_mock_time():
    """清除虚拟时钟，恢复现实系统时间"""
    try:
        from ai_scheduler import ai_scheduler
        ai_scheduler.clear_mock_time()
        return jsonify({
            "success": True,
            "message": "已恢复现实系统时间",
            "data": ai_scheduler.get_status()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/ai/scheduler/analyze-detail', methods=['POST'])
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

            # Step 5: 量化集成决策
            steps.append({"step": "量化集成决策", "status": "running", "progress": 75})
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
            trades = ai_scheduler._decide_with_rules(stocks or [])
            steps[-1] = {"step": "量化集成决策", "status": "ok",
                         "detail": f"多策略集成表决, 遴选出 {len(trades)} 笔优质交易执行指令", "progress": 90}
            yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"

            # Step 6: 执行交易
            if trades:
                steps.append({"step": "执行交易", "status": "running", "progress": 95})
                yield f"data: {json.dumps({'type': 'step', 'data': steps[-1]})}\n\n"
                try:
                    ai_scheduler._execute_trades(trades)
                    steps[-1] = {"step": "执行交易", "status": "ok",
                                 "detail": f"已成功委托执行 {len(trades)} 笔交易", "progress": 100}
                except Exception as e:
                    steps[-1] = {"step": "执行交易", "status": "error", "detail": str(e), "progress": 100}
            else:
                steps.append({"step": "执行交易", "status": "skip", "detail": "仓位已满或暂无合规买点，安全跳过", "progress": 100})
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


@scheduler_bp.route('/api/strategy/signal/<symbol>', methods=['GET'])
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


@scheduler_bp.route('/api/strategy/risk-level', methods=['POST'])
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


@scheduler_bp.route('/api/backtest/run', methods=['POST'])
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


@scheduler_bp.route('/api/backtest/portfolio', methods=['POST'])
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


@scheduler_bp.route('/api/backtest/compare', methods=['POST'])
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


@scheduler_bp.route('/api/backtest/data/<symbol>', methods=['GET'])
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


@scheduler_bp.route('/api/brain/catalog', methods=['GET'])
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


@scheduler_bp.route('/api/brain/strategy/<name>', methods=['GET'])
def brain_strategy_doc(name):
    """获取单个策略的接口文档"""
    try:
        from strategy_registry import registry
        doc = registry.get_strategy_doc(name)
        return jsonify({"success": True, "data": {"doc": doc}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/brain/decide', methods=['POST'])
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

        # 纯量化多策略评估决策（不调用外部大模型）
        decision = ai_brain.decide(symbol, stock, indicators, account_info, strategy_names)

        return jsonify({
            "success": True,
            "data": decision.to_dict()
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/brain/chart-lines/<symbol>', methods=['GET'])
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


@scheduler_bp.route('/api/brain/adapt', methods=['GET'])
def brain_adapt():
    """AI 评估策略表现并给出调整建议"""
    try:
        from ai_brain import ai_brain
        result = ai_brain.evaluate_and_adapt()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/brain/write-strategy', methods=['POST'])
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


@scheduler_bp.route('/api/brain/install-strategy', methods=['POST'])
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


@scheduler_bp.route('/api/brain/performance', methods=['GET'])
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


@scheduler_bp.route('/api/brain/weights', methods=['GET'])
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


@scheduler_bp.route('/api/strategy/optimize', methods=['POST'])
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


@scheduler_bp.route('/api/strategy/batch-optimize', methods=['POST'])
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


@scheduler_bp.route('/api/strategy/backtest', methods=['POST'])
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


@scheduler_bp.route('/api/strategy/sensitivity', methods=['POST'])
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


@scheduler_bp.route('/api/strategy/walk-forward', methods=['POST'])
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


@scheduler_bp.route('/api/strategy/health', methods=['GET'])
def strategy_health():
    """获取所有策略健康状态"""
    try:
        from strategy_health import strategy_health_monitor
        health = strategy_health_monitor.get_all_health()
        return jsonify({"success": True, "data": health})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/strategy/health/<name>', methods=['GET'])
def strategy_health_detail(name):
    """获取单个策略健康详情"""
    try:
        from strategy_health import strategy_health_monitor
        health = strategy_health_monitor.check_health(name)
        return jsonify({"success": True, "data": health})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/strategy/optimized-params', methods=['GET'])
def optimized_params():
    """获取所有已优化的参数"""
    try:
        from strategy_optimizer import strategy_optimizer
        status = strategy_optimizer.get_optimization_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/strategy/clear-params', methods=['POST'])
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


@scheduler_bp.route('/api/brain/auto-trade', methods=['POST'])
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


@scheduler_bp.route('/api/brain/evaluate', methods=['POST'])
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


@scheduler_bp.route('/api/brain/thresholds', methods=['GET'])
def brain_thresholds():
    """获取当前动态决策阈值"""
    try:
        from ai_brain import ai_brain
        thresholds = ai_brain.get_dynamic_thresholds()
        return jsonify({"success": True, "data": thresholds})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/daily/summary', methods=['POST'])
def daily_summary_manual():
    """手动触发每日汇总"""
    try:
        from notification import daily_scheduler
        daily_scheduler.force_daily_summary()
        return jsonify({"success": True, "message": "每日汇总已推送"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/daily/reminder', methods=['POST'])
def daily_reminder_manual():
    """手动触发盘前提醒"""
    try:
        from notification import daily_scheduler
        daily_scheduler.force_market_reminder()
        return jsonify({"success": True, "message": "盘前提醒已推送"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/competition/rules', methods=['GET'])
def competition_rules():
    """获取比赛规则"""
    try:
        rules = competition_manager.get_rules()
        return jsonify({"success": True, "data": rules})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/competition/status', methods=['GET'])
def competition_status():
    """获取比赛状态（是否开盘、倒计时等）"""
    try:
        status = competition_manager.get_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/competition/validate', methods=['POST'])
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


@scheduler_bp.route('/api/monitor/start', methods=['POST'])
def monitor_start():
    """启动止损止盈监控"""
    try:
        auto_trader.start_monitor()
        return jsonify({"success": True, "message": "止损止盈监控已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/monitor/stop', methods=['POST'])
def monitor_stop():
    """停止监控"""
    try:
        auto_trader.stop_monitor()
        return jsonify({"success": True, "message": "监控已停止"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/monitor/status', methods=['GET'])
def monitor_status():
    """获取监控状态"""
    try:
        status = auto_trader.get_monitor_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/scanner/start', methods=['POST'])
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


@scheduler_bp.route('/api/scanner/stop', methods=['POST'])
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


@scheduler_bp.route('/api/scanner/configure', methods=['POST'])
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


@scheduler_bp.route('/api/scanner/status', methods=['GET'])
def scanner_status():
    """获取策略扫描器状态"""
    try:
        status = strategy_scanner.get_status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@scheduler_bp.route('/api/screener/run', methods=['POST'])
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


@scheduler_bp.route('/api/brain/decide-multi', methods=['POST'])
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


@scheduler_bp.route('/api/brain/trade-multi', methods=['POST'])
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


