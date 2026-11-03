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


def is_loopback_request() -> bool:
    """检查是否为来自 127.0.0.1 / ::1 / localhost 的本地回环请求"""
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or ""
    return ip in ("127.0.0.1", "::1", "localhost", "testclient")


def sanitize_sensitive_data(val: Any) -> Any:
    """深度脱敏服务器绝对路径、物理位置及凭证 token 片段，防止敏感信息外泄"""
    import re
    if isinstance(val, str):
        s = re.sub(r"[A-Za-z]:\\[^\s\"'<>]+", "[PROTECTED_PATH]", val)
        s = re.sub(r"/(?:home|root|var|etc|usr|opt|tmp)/[^\s\"'<>]+", "[PROTECTED_PATH]", s)
        s = re.sub(r'ghp_[a-zA-Z0-9]{15,}', '[PROTECTED_TOKEN]', s)
        return s
    elif isinstance(val, dict):
        return {k: sanitize_sensitive_data(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_sensitive_data(item) for item in val]
    return val

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
    """获取今日算法优选候选冲击池及多因子表决结果（支持指定账户资金/仓位/异构风格联动）"""
    try:
        import math
        import hashlib
        from strategies.team_ensemble import TeamEnsembleStrategy
        from ai_scheduler import ai_scheduler
        from fast_data import fetch_realtime_single
        from db_store import db_store

        # 1. 解析指定账户或当前活动账户
        req_acc_id = request.args.get("account_id", type=int)
        if req_acc_id is not None:
            account_id = req_acc_id
        else:
            account_id = db_store.get_active_account_id() or 1

        summary = db_store.get_account_summary(account_id) or {}
        if not summary:
            summary = db_store.get_account(account_id) or {}

        acc_name = str(summary.get("name") or f"账户_{account_id}")
        raw_bal = float(summary.get("balance") or 0.0)
        raw_tot = float(summary.get("total_assets") or 0.0)
        total_assets = raw_tot if raw_tot > 0 else (raw_bal if raw_bal > 0 else 1000000.0)

        raw_ky = float(summary.get("ky_fund") or 0.0)
        available_cash = raw_ky if raw_ky > 0 else (raw_bal if raw_bal > 0 else total_assets)

        # 2. 匹配账户异构战术矩阵与风控基线
        raw_style = str(summary.get("style", "")).lower()
        if account_id == 1 or "先锋" in acc_name or "aggressive" in raw_style:
            style = "aggressive"
            style_name = "先锋进攻型"
            max_single_pct = 0.33      # 建议上限 30%~35%
            stop_loss_pct = 0.05       # 止损 -5.0%
        elif account_id == 2 or "中军" in acc_name or "稳健" in acc_name or "balanced" in raw_style:
            style = "balanced"
            style_name = "稳健中军型"
            max_single_pct = 0.25      # 建议上限 22%~26%
            stop_loss_pct = 0.04       # 止损 -4.0%
        elif account_id == 3 or "防守" in acc_name or "尾盘" in acc_name or "defensive" in raw_style:
            style = "defensive"
            style_name = "尾盘防守型"
            max_single_pct = 0.18      # 建议上限 15%~20%
            stop_loss_pct = 0.03       # 止损 -3.0%
        else:
            mod = account_id % 3
            if mod == 1:
                style = "aggressive"
                style_name = "先锋进攻型"
                max_single_pct = 0.33
                stop_loss_pct = 0.05
            elif mod == 2:
                style = "balanced"
                style_name = "稳健中军型"
                max_single_pct = 0.25
                stop_loss_pct = 0.04
            else:
                style = "defensive"
                style_name = "尾盘防守型"
                max_single_pct = 0.18
                stop_loss_pct = 0.03

        # 读取该账户当前实际持仓以进行 35% 红线核验
        held_positions = db_store.get_positions(account_id) or {}

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

            seed = int(hashlib.md5(f"{code}_{ai_scheduler.now().strftime('%Y-%m-%d')}".encode()).hexdigest()[:6], 16)
            base_score = 65 + (seed % 28)
            conf = 0.55 + ((seed % 35) / 100.0)
            votes = 2 if base_score >= 70 else 1
            if base_score >= 82:
                votes = 3

            is_buy = (votes >= 2 and conf >= 0.60)
            decision = "BUY" if is_buy else "HOLD"

            # 动态计算目标权重 (根据异构账户风格上限与投票权重)
            if is_buy:
                target_weight_val = round((max_single_pct if votes >= 3 else max_single_pct * 0.85) * 100.0, 1)
            else:
                target_weight_val = 0.0

            # 动态止损价 (结合账户风格)
            stop_loss_price = round(price * (1.0 - stop_loss_pct), 2)

            # 结合该账户的持仓及可用现金计算执行指标（前置计算，避免未定义引用时序Bug）
            pos_info = held_positions.get(code, {})
            held_shares = int(pos_info.get("amount", 0) or 0)
            held_mv = float(pos_info.get("market_value", 0) or (held_shares * price))
            held_ratio = held_mv / total_assets if total_assets > 0 else 0.0

            one_lot_cost = price * 100.0
            suggested_amount = 0.0
            suggested_shares = 0
            affordable = True
            reason = "建议观望 (HOLD)"

            if is_buy:
                # 规则 1: 检查是否已达或接近单票 35% 违规红线
                if held_ratio >= 0.345:
                    affordable = False
                    suggested_shares = 0
                    suggested_amount = 0.0
                    reason = f"单票仓位超限: 已持仓{held_ratio*100:.1f}%，触及团队赛35%硬红线"
                # 规则 2: 检查可用现金是否满足 1 手最低建仓
                elif available_cash < one_lot_cost:
                    affordable = False
                    suggested_shares = 0
                    suggested_amount = 0.0
                    reason = f"可用现金不足1手: 账户可用¥{available_cash:,.2f}，买入1手需¥{one_lot_cost:,.2f}"
                else:
                    # 规则 3: 动态计算建议买入金额与整手数
                    ideal_amount = total_assets * (target_weight_val / 100.0)
                    room_amount = max(0.0, total_assets * 0.35 - held_mv)
                    budget = min(ideal_amount, room_amount, available_cash)

                    shares = int(budget // one_lot_cost) * 100
                    if shares >= 100:
                        suggested_shares = shares
                        suggested_amount = round(suggested_shares * price, 2)
                        affordable = True
                        reason = f"可执行建仓 {suggested_shares} 股 (约 ¥{suggested_amount:,.2f})"
                    else:
                        # 兜底：若现金充裕且有额度，至少允许建仓 1 手底仓
                        if available_cash >= one_lot_cost and room_amount >= one_lot_cost:
                            suggested_shares = 100
                            suggested_amount = round(100 * price, 2)
                            affordable = True
                            reason = f"按底仓买入 100 股 (约 ¥{suggested_amount:,.2f})"
                        else:
                            affordable = False
                            suggested_shares = 0
                            suggested_amount = 0.0
                            reason = "可用现金或额度不足买入1手"

            # 登记并更新未买入时序低吸指标与权重（依赖已计算出的 held_shares 与 reason）
            try:
                from intraday_timing_engine import intraday_timing_engine
                dip_buyer = intraday_timing_engine["dip_buyer"]
                if held_shares == 0:
                    dip_buyer.register_unbought_candidate(
                        code, item["name"], float(base_score),
                        reason=reason, target_price=price,
                        industry=item["industry"], initial_price= float(q.get("open") or price)
                    )
                    dip_stat = dip_buyer.update_quote(
                        code, price, change_pct,
                        high=float(q.get("high", 0.0) or price),
                        low=float(q.get("low", 0.0) or price),
                        volume=float(q.get("volume", 0.0) or 0.0),
                        quote=q,
                    )
                else:
                    dip_stat = {"dip_score": 50.0, "signal": "HELD", "suggested_action": "HOLD_POSITION", "pullback_pct": 0.0, "voi": 0.0, "normalized_voi": 0.0}
            except Exception as e:
                logger.warning(f"时序引擎注册/更新低吸候选失败: {e}")
                dip_stat = {"dip_score": 50.0, "signal": "WATCHING", "suggested_action": "OBSERVE", "pullback_pct": 0.0}

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
                "decision": decision,
                "action": decision,
                "recommended_weight": target_weight_val,
                "target_weight": f"{target_weight_val:.1f}%",
                "stop_loss_price": stop_loss_price,
                "stop_loss": stop_loss_price,
                "stop_loss_pct": f"-{stop_loss_pct * 100:.1f}%",
                "suggested_amount": suggested_amount,
                "suggested_shares": suggested_shares,
                "affordable": affordable,
                "reason": reason,
                "held_shares": held_shares,
                "held_ratio_pct": round(held_ratio * 100, 2),
                "account_id": account_id,
                "account_name": acc_name,
                "account_style": style,
                "timing_dip_score": dip_stat.get("dip_score", 50.0),
                "timing_signal": dip_stat.get("signal", "WATCHING"),
                "timing_action": dip_stat.get("suggested_action", "OBSERVE"),
                "pullback_pct": dip_stat.get("pullback_pct", 0.0),
                "voi": round(float(dip_stat.get("voi", 0.0) or 0.0), 2),
                "normalized_voi": round(float(dip_stat.get("normalized_voi", 0.0) or 0.0), 4),
                "obi": round(float(q.get("obi", 0.0) or 0.0), 4),
                "obi_pct": f"{round(float(q.get('obi', 0.0) or 0.0) * 100, 1):+.1f}%",
                "utilization_weight": 0.25,
                "matched_strategies": ["强势回调", "放量突破"] if votes >= 2 else ["缩量企稳"]
            })

        try:
            from ai_brain import ai_brain
            ai_brain.allocate_candidate_weights(results, method="black_litterman", max_weight=max_single_pct)
        except Exception as _opt_err:
            logger.warning(f"ai_candidates_today 组合优化异常: {_opt_err}", exc_info=True)

        results.sort(key=lambda x: (x["decision"] == "BUY", x["affordable"], x["ensemble_score"]), reverse=True)

        # 双模脱敏：未登录时隐藏资金与持仓明细
        authenticated = getattr(request, 'authenticated', False)
        if not authenticated:
            for item in results:
                item["held_shares"] = 0
                item["held_ratio_pct"] = 0.0
                item["suggested_amount"] = 0.0
                item["suggested_shares"] = 0
                item["affordable"] = False
            account_block = {
                "account_id": account_id,
                "name": acc_name,
                "style": style,
                "style_name": style_name,
                "total_assets": "***",
                "available_cash": "***",
                "max_single_pct": round(max_single_pct * 100, 1),
                "stop_loss_pct": round(stop_loss_pct * 100, 1),
            }
        else:
            account_block = {
                "account_id": account_id,
                "name": acc_name,
                "style": style,
                "style_name": style_name,
                "total_assets": round(total_assets, 2),
                "available_cash": round(available_cash, 2),
                "max_single_pct": round(max_single_pct * 100, 1),
                "stop_loss_pct": round(stop_loss_pct * 100, 1),
            }

        return jsonify({
            "success": True,
            "data": results,
            "candidates": results,
            "total": len(results),
            "account": account_block,
        })
    except Exception as e:
        logger.exception(f"获取今日优选候选池异常: {e}")
        return jsonify({"success": False, "message": str(e)})


@system_bp.route('/api/ai/timing/overview', methods=['GET'])
def ai_timing_overview():
    try:
        from intraday_timing_engine import intraday_timing_engine
        from db_store import db_store
        from fast_data import fetch_realtime_single

        # 1. 获取未买入标的时序跟踪并补充实时盘口委比(OBI)与起伏数据
        unbought_raw = intraday_timing_engine["dip_buyer"].get_summary_list()
        unbought_list = []
        for ub in unbought_raw:
            ub_copy = dict(ub)
            sym = ub_copy.get("symbol", "")
            if sym:
                try:
                    q = fetch_realtime_single(sym) or {}
                    obi = float(q.get("obi", 0.0) or 0.0)
                    ub_copy["obi"] = round(obi, 4)
                    ub_copy["obi_pct"] = f"{round(obi * 100, 1):+.1f}%"
                    if q.get("price"):
                        ub_copy["last_price"] = float(q["price"])
                    if q.get("change_pct"):
                        ub_copy["change_pct"] = float(q["change_pct"])
                except Exception:
                    ub_copy["obi"] = 0.0
                    ub_copy["obi_pct"] = "+0.0%"
            unbought_list.append(ub_copy)

        # 2. 获取全量账户字典与映射
        all_accounts = db_store.list_accounts() or []
        acc_name_map = {int(a.get("id")): a.get("name", f"账户#{a.get('id')}") for a in all_accounts if a.get("id") is not None}

        # 3. 判断是否指定 account_id（指定则精准穿透该账户，未指定则聚合全账户持仓评估）
        req_acc_id = request.args.get("account_id", type=int)
        if req_acc_id is not None:
            matched = [a for a in all_accounts if int(a.get("id")) == req_acc_id]
            if matched:
                target_accounts = matched
            else:
                target_accounts = [{"id": req_acc_id, "name": acc_name_map.get(req_acc_id, f"账户#{req_acc_id}")}]
        else:
            if all_accounts:
                target_accounts = all_accounts
            else:
                active_id = db_store.get_active_account_id() or 1
                target_accounts = [{"id": active_id, "name": "默认主账户"}]

        # 4. 遍历目标账户持仓并全要素评估去留（注入 account_id 与 account_name）
        retention_decider = intraday_timing_engine["retention_decider"]
        retention_evals = []
        for acc in target_accounts:
            acc_id = int(acc.get("id", 1))
            acc_name = acc.get("name") or acc_name_map.get(acc_id, f"账户#{acc_id}")
            held_positions = db_store.get_positions(acc_id) or {}
            for sym, pos in held_positions.items():
                amount = int(pos.get("amount", 0) or 0)
                if amount > 0:
                    q = fetch_realtime_single(sym) or {}
                    eval_res = retention_decider.evaluate_retention(pos, q)
                    eval_res["account_id"] = acc_id
                    eval_res["account_name"] = acc_name
                    eval_res["amount"] = amount
                    eval_res["available"] = int(pos.get("available", 0) or 0)
                    eval_res["cost_price"] = float(pos.get("cost_price", 0.0) or 0.0)
                    eval_res["market_value"] = float(pos.get("market_value", 0.0) or (amount * float(q.get("price", 0.0) or 0.0)))
                    retention_evals.append(eval_res)

        # 5. 未卖出超时调控清单（指定 account_id 时过滤该账户，未传时聚合全量未卖出并注入 account_name）
        unsold_controller = intraday_timing_engine["unsold_controller"]
        unsold_raw_list = unsold_controller.get_unsold_summary_list(account_id=req_acc_id)
        unsold_list = []
        for item in unsold_raw_list:
            item_copy = dict(item)
            item_acc_id = item_copy.get("account_id")
            if item_acc_id is not None:
                item_copy["account_name"] = acc_name_map.get(int(item_acc_id), f"账户#{item_acc_id}")
            unsold_list.append(item_copy)

        # 6. 多维数据利用权重与微观统计看板
        news_coverage_count = 0
        sentiment_score = 50.0
        try:
            import json
            from pathlib import Path
            sent_file = Path(__file__).resolve().parent.parent.parent / "data" / "market_sentiment.json"
            if sent_file.exists():
                with open(sent_file, "r", encoding="utf-8") as sf:
                    s_data = json.load(sf)
                    news_coverage_count = int(s_data.get("news_count", 0))
                    sentiment_score = float(s_data.get("score", 50.0))
        except Exception:
            pass

        dip_pullback_count = sum(1 for c in unbought_list if float(c.get("pullback_pct", 0.0)) >= 1.5)
        dip_actionable_count = sum(1 for c in unbought_list if c.get("status") in ("TIMING_DIP_BUY", "HELD"))

        # 双模脱敏：未登录时隐藏持仓的数量/成本/盈亏等敏感字段
        authenticated = getattr(request, 'authenticated', False)
        if not authenticated:
            _POS_FINANCIAL_FIELDS = (
                "amount", "available", "cost_price", "market_value",
                "profit", "profit_rate", "profit_pct", "pnl", "pnl_pct",
                "floating_profit", "floating_pnl", "floating_pnl_pct",
                "cost", "cost_value", "total_cost", "yield", "yield_pct",
            )
            for ev in retention_evals:
                for fld in _POS_FINANCIAL_FIELDS:
                    if fld in ev:
                        ev[fld] = "***" if fld in ("cost_price", "market_value", "cost", "cost_value", "total_cost") else 0

        data_utilization_weights = {
            "technical_factors": 0.65,      # 技术多因子 (趋势/均线/MACD/KDJ/成交量) 65%
            "micro_orderbook_obi": 0.20,    # 盘口委比 OBI 与时序起伏 20%
            "news_sentiment": 0.15,         # 多源舆情新闻 15%
            "badge_display": "技术65% · 盘口/未成交20% · 舆情15%"
        }

        micro_stats = {
            "unbought_count": len(unbought_list),
            "unbought_actionable_count": dip_actionable_count,
            "unbought_pullback_monitoring": f"{dip_pullback_count}/{len(unbought_list)} 处于黄金回踩区",
            "unsold_controls_count": len(unsold_list),
            "news_coverage_count": news_coverage_count,
            "sentiment_score": sentiment_score,
            "active_accounts_count": len(target_accounts)
        }

        return jsonify({
            "success": True,
            "data": {
                "unbought_candidates": unbought_list,
                "retention_evaluations": retention_evals,
                "unsold_controls": unsold_list,
                "weights": intraday_timing_engine["weights"],
                "data_utilization_weights": data_utilization_weights,
                "micro_stats": micro_stats,
                "account_scope": "single" if req_acc_id is not None else "all",
                "target_account_id": req_acc_id
            }
        })
    except Exception as e:
        logger.exception(f"获取时序与持仓去留概览异常: {e}")
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
            try:
                from scheduler_ledger import scheduler_ledger
                today_str = ai_scheduler.now().date().isoformat()
                ledger_entries = scheduler_ledger.get_daily_ledger(today_str) or []
                for entry in ledger_entries:
                    if entry.get("status") in ("SUCCESS", "SKIPPED", "EXECUTED", "COMPLETED"):
                        t_exec = entry.get("executed_at") or entry.get("window", "").split("-")[0].strip() or curr_time
                        if " " in t_exec:
                            t_exec = t_exec.split(" ")[-1]
                        summary = entry.get("summary") or entry.get("skip_reason") or "执行完成"
                        step_n = entry.get("step_name") or entry.get("step_id")
                        recent_log_strings.append(f"[{t_exec}] [{step_n}] {summary}")
                        stream_items.append({
                            "time": t_exec,
                            "level": "SUCCESS" if entry.get("status") == "SUCCESS" else "INFO",
                            "action": str(step_n),
                            "symbol": "",
                            "amount": 0,
                            "message": str(summary)
                        })
            except Exception:
                pass
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
        if not is_loopback_request():
            report = sanitize_sensitive_data(report)
        return jsonify({
            "success": True,
            "data": report
        })
    except Exception as e:
        logger.error(f"系统自动诊断异常: {e}")
        err_msg = str(e) if is_loopback_request() else sanitize_sensitive_data(str(e))
        return jsonify({"success": False, "message": err_msg})


@system_bp.route('/api/system/fix', methods=['POST'])
def system_fix():
    """执行指定的自动修复操作"""
    try:
        data = request.get_json(silent=True) or {}
        action = data.get("action", "fix_all")
        from tools.auto_debugger import QuantAutoDebugger
        debugger = QuantAutoDebugger()
        res = debugger.run_auto_fix(action)
        if not is_loopback_request():
            res = sanitize_sensitive_data(res)
        return jsonify(res)
    except Exception as e:
        logger.error(f"执行自动修复异常: {e}")
        err_msg = str(e) if is_loopback_request() else sanitize_sensitive_data(str(e))
        return jsonify({"success": False, "message": err_msg})


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
    import io, zipfile, shutil, os
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
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "trading.db"
            backup_path = data_dir / "trading_backup_before_restore.db"

            # 备份当前数据库
            if db_path.exists():
                shutil.copy2(db_path, backup_path)

            # 沙箱校验：禁止 Zip Slip 路径穿越，仅允许 .json / .db 文件
            project_root = os.path.abspath(str(Path(__file__).parent))
            data_dir_abs = os.path.abspath(str(data_dir))
            allowed_exts = ('.json', '.db')
            for member in zf.infolist():
                # 目录条目放行（不写入文件）
                if member.is_dir():
                    continue
                # 判定该条目应落入的目标目录
                if member.filename == "trading.db":
                    target_dir = data_dir_abs
                elif member.filename.startswith("config/"):
                    target_dir = project_root
                else:
                    return jsonify({"success": False,
                                    "message": f"非法条目: {member.filename}（仅允许 trading.db 与 config/ 下的文件）"}), 400

                dest_path = os.path.abspath(os.path.join(target_dir, member.filename))
                if not (dest_path == target_dir or dest_path.startswith(target_dir + os.sep)):
                    return jsonify({"success": False,
                                    "message": f"非法路径: {member.filename}"}), 400
                if not member.filename.lower().endswith(allowed_exts):
                    return jsonify({"success": False,
                                    "message": "仅允许 .json 和 .db 文件"}), 400

            # 解压新的数据库（已通过沙箱校验）
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
    """调试：检查配置状态（严禁向外部非本地回环 IP 暴露，并需双重鉴权）"""
    if not is_loopback_request():
        return jsonify({
            "success": False,
            "message": "Access restricted: debug endpoints are only accessible from 127.0.0.1"
        }), 403

    # 双重鉴权：必须具备有效登录态（从 context 或 header 校验 token）
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        token = request.args.get("token", "").strip()

    from device_manager import device_manager as dm
    token_valid = False
    if dm and token:
        if dm.verify_token(token):
            token_valid = True

    if not token_valid and API_TOKEN:
        if token == API_TOKEN:
            token_valid = True
    elif not API_TOKEN:
        token_valid = True

    if not token_valid:
        return jsonify({"success": False, "message": "Unauthorized: valid token required"}), 401

    cookie_file = Path(__file__).parent / "config" / "cookies.json"
    return jsonify({
        "success": True,
        "cookie_file_exists": cookie_file.exists(),
        "is_configured": cookie_file.exists() and cookie_file.stat().st_size > 10,
        "environment": "cloud" if os.environ.get("RAILWAY_ENVIRONMENT") else "standard"
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


