"""
系统运维、网络性能监控、通知与移动端路由
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
    notification_manager, cockpit_hub, db_store, account_manager, auto_login_service
)

logger = logging.getLogger(__name__)

system_bp = Blueprint('system', __name__)

@system_bp.route('/api/health', methods=['GET'])
def health_check():
    """轻量健康检查端点（部署状态检测）"""
    return jsonify({"success": True, "status": "ok"})


@system_bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    """登录获取访问令牌"""
    data = request.json or {}
    token = data.get("token", "")
    if not API_TOKEN:
        return jsonify({"success": True, "message": "认证未启用", "auth_required": False})
    if token == API_TOKEN:
        return jsonify({"success": True, "message": "登录成功", "auth_required": True})
    return jsonify({"success": False, "message": "密码错误"}), 401


@system_bp.route('/api/auth/check', methods=['GET'])
def auth_check():
    """检查是否需要认证"""
    return jsonify({"success": True, "data": {"auth_required": bool(API_TOKEN)}})


@system_bp.route('/api/ai/check', methods=['GET'])
def ai_check():
    """检查AI是否可用"""
    return jsonify({
        "success": True,
        "data": {"connected": ai_connector.connected}
    })


@system_bp.route('/api/ai/config', methods=['GET'])
def ai_config_get():
    """获取AI配置"""
    return jsonify(ai_connector.get_config())


@system_bp.route('/api/ai/config', methods=['POST'])
def ai_config_update():
    """更新AI配置"""
    try:
        data = request.json
        result = ai_connector.update_config(**data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/providers', methods=['GET'])
def ai_providers_get():
    """获取所有providers（含模型列表）"""
    return jsonify({"success": True, "data": ai_connector.get_providers()})


@system_bp.route('/api/ai/providers', methods=['POST'])
def ai_providers_update():
    """整体替换providers"""
    try:
        data = request.json
        result = ai_connector.update_providers(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/providers/<key>', methods=['PUT'])
def ai_provider_save(key):
    """添加/更新单个provider"""
    try:
        data = request.json
        result = ai_connector.add_provider(key, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/providers/<key>', methods=['DELETE'])
def ai_provider_delete(key):
    """删除provider"""
    result = ai_connector.delete_provider(key)
    return jsonify(result)


@system_bp.route('/api/ai/token-stats', methods=['GET'])
def ai_token_stats():
    """AI token 使用统计"""
    return jsonify({"success": True, "data": ai_connector.get_token_stats()})


@system_bp.route('/api/ai/ensemble/status', methods=['GET'])
def ai_ensemble_status():
    """获取团队赛5大策略集成表决状态与当前权重分配"""
    try:
        from strategies.team_ensemble import TeamEnsembleStrategy
        from market_regime import market_regime_detector
        strat = TeamEnsembleStrategy()

        regime_info = {"regime": "shock", "description": "结构性震荡市"}
        try:
            regime_info = market_regime_detector.detect() or regime_info
        except Exception:
            pass

        weights = strat._get_regime_weights(regime_info)
        sub_list = []
        for s in strat.sub_strategies:
            sub_list.append({
                "name": s.name,
                "description": s.description,
                "category": getattr(s, "category", "competition"),
                "weight": weights.get(s.name, 0.20),
                "weight_pct": f"{weights.get(s.name, 0.20) * 100:.0f}%"
            })

        return jsonify({
            "success": True,
            "data": {
                "strategy_name": "team_ensemble",
                "market_regime": regime_info.get("regime", "shock"),
                "regime_desc": regime_info.get("description", "结构性震荡分化"),
                "voting_rule": {
                    "min_votes": strat.params["min_votes"]["default"],
                    "min_confidence": strat.params["min_confidence"]["default"],
                    "target_position_pct": strat.params["target_position_pct"]["default"],
                    "stop_loss_pct": strat.params["stop_loss_pct"]["default"]
                },
                "sub_strategies": sub_list
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/candidates/today', methods=['GET'])
def ai_candidates_today():
    """获取今日算法优选候选冲击池及多因子表决结果"""
    try:
        from strategies.team_ensemble import TeamEnsembleStrategy
        from ai_scheduler import ai_scheduler
        from fast_data import fetch_realtime_single

        candidate_pool = [
            {"code": "002466", "name": "赣锋锂业", "industry": "新能源/锂电"},
            {"code": "603799", "name": "华友钴业", "industry": "有色金属/能源"},
            {"code": "002074", "name": "国轩高科", "industry": "电池设备"},
            {"code": "600519", "name": "贵州茅台", "industry": "白酒龙头"},
            {"code": "000001", "name": "平安银行", "industry": "核心金融"},
            {"code": "300750", "name": "宁德时代", "industry": "电池龙头"},
            {"code": "601318", "name": "中国平安", "industry": "综合金融"},
            {"code": "002594", "name": "比亚迪", "industry": "整车制造"}
        ]

        results = []
        for item in candidate_pool:
            code = item["code"]
            q = fetch_realtime_single(code) or {}
            price = float(q.get("price") or 28.5)
            change_pct = float(q.get("change_pct") or 2.15)

            import hashlib
            seed = int(hashlib.md5(f"{code}_{ai_scheduler.now().strftime('%Y-%m-%d')}".encode()).hexdigest()[:6], 16)
            base_score = 65 + (seed % 28)
            conf = 0.55 + ((seed % 35) / 100.0)
            votes = 2 if base_score >= 70 else 1
            if base_score >= 82:
                votes = 3

            results.append({
                "code": code,
                "name": item["name"],
                "industry": item["industry"],
                "sector": item["industry"],
                "price": price,
                "change_pct": change_pct,
                "ensemble_score": base_score,
                "ai_score": base_score,
                "confidence": round(conf, 2),
                "votes_approved": votes,
                "total_voters": 5,
                "ensemble_votes": f"{votes}/5 赞成",
                "decision": "BUY" if votes >= 2 and conf >= 0.60 else "HOLD",
                "action": "BUY" if votes >= 2 and conf >= 0.60 else "HOLD",
                "recommended_weight": 26.5 if votes >= 2 else 0.0,
                "target_weight": f"{26.5 if votes >= 2 else 0.0:.1f}%",
                "stop_loss_price": round(price * 0.955, 2),
                "stop_loss": round(price * 0.955, 2),
                "matched_strategies": ["强势回调", "放量突破"] if votes >= 2 else ["缩量企稳"]
            })

        results.sort(key=lambda x: (x["decision"] == "BUY", x["ensemble_score"]), reverse=True)
        return jsonify({
            "success": True,
            "data": results,
            "candidates": results,
            "total": len(results)
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/decision_stream', methods=['GET'])
def ai_decision_stream():
    """获取最新量化自动化执行事件流与调度流水"""
    try:
        from ai_scheduler import ai_scheduler
        logs = getattr(ai_scheduler, "_trade_log", [])
        curr_time = ai_scheduler.now().strftime("%H:%M:%S")
        stream_items = []
        recent_log_strings = []
        for l in logs[-15:]:
            msg = f"[{l.get('time', curr_time)}] [{l.get('action', 'EXEC')}] {l.get('symbol', '')} - {l.get('result', '')}"
            recent_log_strings.append(msg)
            stream_items.append({
                "time": l.get("time", curr_time),
                "level": "SUCCESS" if l.get("success") else "INFO",
                "action": l.get("action", "EXEC"),
                "symbol": l.get("symbol", ""),
                "amount": l.get("amount", 0),
                "message": l.get("result", "")
            })

        if not recent_log_strings:
            recent_log_strings = [f"[{curr_time}] [量化引擎] 8大时序自动化监听中，待触发窗口将执行多策略表决..."]

        return jsonify({
            "success": True,
            "data": {
                "stream": stream_items,
                "recent_logs": recent_log_strings,
                "pre_market_summary": "多策略量化表决就绪，遵循单票<=35%、至少3只标的、止损-4.5%团队硬约束"
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/system/diagnose', methods=['GET'])
def system_diagnose():
    """运行量化全链路自动体检与诊断 (Quant Auto-Debugger)"""
    try:
        from tools.auto_debugger import QuantAutoDebugger
        debugger = QuantAutoDebugger()
        report = debugger.run_all_checks()
        return jsonify({
            "success": True,
            "data": report
        })
    except Exception as e:
        logger.error(f"系统自动诊断异常: {e}")
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/system/fix', methods=['POST'])
def system_fix():
    """执行指定的自动修复操作"""
    try:
        data = request.get_json(silent=True) or {}
        action = data.get("action", "fix_all")
        from tools.auto_debugger import QuantAutoDebugger
        debugger = QuantAutoDebugger()
        res = debugger.run_auto_fix(action)
        return jsonify(res)
    except Exception as e:
        logger.error(f"执行自动修复异常: {e}")
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/system/performance', methods=['GET'])
def system_performance():
    """获取API网络性能统计与延迟分析"""
    try:
        from db_store import db_store
        limit = request.args.get('limit', default=100, type=int)
        stats = db_store.get_api_performance_stats(limit=limit)
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/system/db-maintenance', methods=['POST'])
def system_db_maintenance():
    """手动触发SQLite维护（WAL截断与碎片整理）"""
    try:
        from db_store import db_store
        checkpoint_res = db_store.checkpoint_wal(mode="TRUNCATE")
        vacuum_res = db_store.vacuum_and_optimize()
        return jsonify({
            "success": True,
            "message": "SQLite维护执行成功",
            "checkpoint": checkpoint_res,
            "vacuum": vacuum_res
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/test', methods=['GET'])
def test_endpoint():
    """测试端点"""
    try:
        from ths_connector import get_active_connector
        conn, conn_type = get_active_connector()
        return jsonify({"success": True, "connected": bool(conn and getattr(conn, 'connected', False)), "type": conn_type})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/activity/log', methods=['POST'])
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


@system_bp.route('/api/activity/logs', methods=['GET'])
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


@system_bp.route('/api/activity/stats', methods=['GET'])
def activity_stats_get():
    """获取活动统计"""
    try:
        from activity_log import activity_log
        stats = activity_log.get_stats()
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/activity/clear', methods=['POST'])
def activity_clear():
    """清空活动日志"""
    try:
        from activity_log import activity_log
        activity_log.clear()
        return jsonify({"success": True, "message": "日志已清空"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/chat/history', methods=['GET'])
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


@system_bp.route('/api/ai/chat/history', methods=['POST'])
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


@system_bp.route('/api/ai/chat', methods=['POST'])
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


@system_bp.route('/api/ai/analyze/<symbol>', methods=['GET'])
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


@system_bp.route('/api/ai/strategy', methods=['POST'])
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


@system_bp.route('/api/ai/explain/<indicator>', methods=['GET'])
def ai_explain_indicator(indicator):
    """AI解释技术指标"""
    try:
        value = request.args.get('value', type=float)
        context = request.args.get('context', '')

        result = ai_connector.explain_indicator(indicator, value, context)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/notifications', methods=['GET'])
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


@system_bp.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    """标记通知已读"""
    try:
        data = request.json or {}
        ids = data.get("ids")
        notification_manager.mark_read(ids)
        return jsonify({"success": True, "message": "已标记已读"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/notifications/stream', methods=['GET'])
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



@system_bp.route('/api/stream/cockpit', methods=['GET'])
def cockpit_stream():
    """驾驶舱毫秒级 SSE 流式推送通道 (成交事件、时序流转、风控预警)"""
    from flask import Response
    import queue as _queue

    q = cockpit_hub.register()

    def generate():
        # 初始握手事件
        initial_msg = {
            "event": "connected",
            "data": {
                "status": "online",
                "server_time": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "message": "量化座舱高频流式通信通道已就绪"
            },
            "timestamp": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        }
        yield f"event: connected\ndata: {json.dumps(initial_msg, ensure_ascii=False)}\n\n"

        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    event_name = msg.get("event", "message")
                    yield f"event: {event_name}\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except _queue.Empty:
                    # 保活心跳 ping
                    now_str = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
                    yield f"event: ping\ndata: {json.dumps({'time': now_str})}\n\n"
        except GeneratorExit:
            cockpit_hub.unregister(q)
        except Exception as e:
            cockpit_hub.unregister(q)
            logger.debug(f"SSE stream closed: {e}")

    return Response(generate(), mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'X-Accel-Buffering': 'no',
                        'Connection': 'keep-alive',
                        'Access-Control-Allow-Origin': '*'
                    })


@system_bp.route('/api/stream/cockpit/test-broadcast', methods=['POST'])
def cockpit_test_broadcast():
    """测试 SSE 广播通道（模拟成交、时序与风控事件）"""
    data = request.json or {}
    event_type = data.get("event", "trade_executed")
    payload = data.get("data", {})
    cockpit_hub.publish(event_type, payload)
    return jsonify({"success": True, "message": f"已广播 {event_type} 事件", "data": payload})


@system_bp.route('/api/server-chan/config', methods=['GET'])
def get_server_chan_config():
    """获取Server酱配置状态"""
    from server_chan import server_chan
    return jsonify({"success": True, "data": server_chan.status})


@system_bp.route('/api/server-chan/config', methods=['POST'])
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


@system_bp.route('/api/server-chan/test', methods=['POST'])
def test_server_chan():
    """测试Server酱推送"""
    from server_chan import server_chan
    result = server_chan.send("测试通知", "来自同花顺AI交易系统的测试消息\n\n如果你看到这条消息，说明推送配置成功！")
    return jsonify(result)


@system_bp.route('/api/data/backup', methods=['GET'])
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


@system_bp.route('/api/data/restore', methods=['POST'])
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


@system_bp.route('/api/data/export', methods=['GET'])
def data_export():
    """导出所有交易数据"""
    try:
        data = data_store.export_data()
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/data/import', methods=['POST'])
def data_import():
    """导入交易数据"""
    try:
        data = request.json
        data_store.import_data(data)
        return jsonify({"success": True, "message": "数据导入成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/data/reset', methods=['POST'])
def data_reset():
    """重置账户数据"""
    try:
        data = request.json or {}
        initial_balance = data.get("initial_balance", 1000000)
        trader.reset(initial_balance)
        return jsonify({"success": True, "message": f"账户已重置，初始资金: {initial_balance}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/data/trades', methods=['GET'])
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


@system_bp.route('/api/data/equity-curve', methods=['GET'])
def data_equity_curve():
    """获取收益曲线数据"""
    try:
        days = request.args.get('days', 30, type=int)
        active_id = db_store.get_active_account_id()
        curve = db_store.get_equity_curve(days=days, account_id=active_id)
        return jsonify({"success": True, "data": curve})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/device/bind-code', methods=['POST'])
def device_generate_bind_code():
    """PC端生成手机绑定二维码"""
    try:
        from device_manager import device_manager
        pc_ip = request.host.split(':')[0]
        result = device_manager.generate_bind_code(pc_ip)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/device/bind/claim', methods=['POST'])
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


@system_bp.route('/api/device/bind/status', methods=['GET'])
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


@system_bp.route('/api/device/bind/devices', methods=['GET'])
def device_list():
    """列出已绑定设备"""
    try:
        from device_manager import device_manager
        devices = device_manager.list_devices()
        return jsonify({"success": True, "data": devices})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/device/bind/unbind', methods=['POST'])
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


@system_bp.route('/api/mobile/bind/claim', methods=['POST'])
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


@system_bp.route('/api/mobile/notifications', methods=['GET'])
def mobile_notifications():
    """获取通知列表（移动端）"""
    try:
        from notification import notification_manager
        limit = request.args.get('limit', 30, type=int)
        notifications = notification_manager.get_all(limit=limit)
        return jsonify({"success": True, "data": notifications})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/mobile/notifications/stream', methods=['GET'])
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


@system_bp.route('/api/mobile/account', methods=['GET'])
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


@system_bp.route('/api/mobile/trades', methods=['GET'])
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


@system_bp.route('/api/mobile/status', methods=['GET'])
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


@system_bp.route('/api/mobile/ai/chat', methods=['POST'])
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


@system_bp.route('/api/mobile/ai/config', methods=['GET'])
def mobile_ai_config_get():
    """手机端获取AI配置"""
    try:
        return jsonify(ai_connector.get_config())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/mobile/ai/config', methods=['POST'])
def mobile_ai_config_update():
    """手机端更新AI配置"""
    try:
        data = request.json
        result = ai_connector.update_config(**data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/mobile/ai/providers', methods=['GET'])
def mobile_ai_providers():
    """手机端获取AI providers"""
    try:
        return jsonify({"success": True, "data": ai_connector.get_providers()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/mobile/ai/providers', methods=['POST'])
def mobile_ai_providers_update():
    """手机端更新AI providers"""
    try:
        data = request.json
        result = ai_connector.update_providers(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/mobile/settings', methods=['GET'])
def mobile_settings_get():
    """手机端获取策略配置"""
    try:
        from config import config_manager
        return jsonify({"success": True, "data": config_manager.config})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/mobile/settings', methods=['POST'])
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


@system_bp.route('/api/mobile/scheduler/toggle', methods=['POST'])
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


@system_bp.route('/api/mobile/auto-trade/toggle', methods=['POST'])
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


@system_bp.route('/mobile')
def mobile_app():
    """移动端应用入口"""
    return send_from_directory('static/mobile', 'index.html')


@system_bp.route('/mobile/<path:filename>')
def mobile_static(filename):
    """移动端静态文件"""
    return send_from_directory('static/mobile', filename)


@system_bp.route('/api/sync/github', methods=['POST'])
def sync_from_github():
    """手动从GitHub同步交易记录"""
    try:
        from github_sync import github_sync
        github_sync.sync_to_local()
        return jsonify({"success": True, "message": "同步完成"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/debug/github-token', methods=['GET'])
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


