# -*- coding: utf-8 -*-
"""
AI 时序与宏观遥测网关接口 (AI Timing & Macro Telemetry API)
Stage 3 核心网关与遥测扩展服务

功能列表:
1. 宏观四象限状态机遥测接口 (/api/macro/quadrant)
2. 组合行业敞口矩阵与 HHI 集中度指数 (/api/risk/industry-exposure)
3. 交易审计 HMAC-SHA256 防篡改数字签名与验签接口 (/api/audit/sign-order, /api/audit/verify-signature)
4. 4 大历史极端情景压力测试遥测接口 (/api/risk/stress-test)
5. 下单前 What-If 边际 VaR/CVaR 沙盘推演接口 (/api/risk/what-if)
"""

import os
import hmac
import hashlib
import json
import time
import logging
from typing import Dict, List, Any, Optional
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

ai_timing_bp = Blueprint('ai_timing', __name__)

# HMAC 签名使用的机密 Key (从环境变量读取或降级固定种子)
HMAC_AUDIT_SECRET = os.environ.get("HMAC_AUDIT_SECRET", "THS_QUANT_AUDIT_SALT_9981").encode("utf-8")


def generate_trade_audit_signature(payload: Dict[str, Any], secret_key: Optional[bytes] = None) -> str:
    """
    针对报单与成交审计要素生成防篡改 HMAC-SHA256 签名
    要素包括: account_id, symbol, action, price, amount, timestamp
    """
    key = secret_key or HMAC_AUDIT_SECRET
    acc_id = str(payload.get("account_id", "1"))
    sym = str(payload.get("symbol", "")).strip()
    act = str(payload.get("action", "")).upper().strip()
    price = f"{float(payload.get('price', 0.0)):.3f}"
    amt = str(int(payload.get("amount", 0)))
    ts = str(payload.get("timestamp", ""))

    raw_canonical = f"account={acc_id}&symbol={sym}&action={act}&price={price}&amount={amt}&ts={ts}"
    sig = hmac.new(key, raw_canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig


def verify_trade_audit_signature(payload: Dict[str, Any], signature: str, secret_key: Optional[bytes] = None) -> bool:
    """校验交易审计签名的防篡改完整性"""
    if not signature:
        return False
    expected = generate_trade_audit_signature(payload, secret_key)
    return hmac.compare_digest(expected, signature.strip())


@ai_timing_bp.route('/api/macro/quadrant', methods=['GET'])
def get_macro_quadrant():
    """获取四象限宏观状态罗盘与后验概率分布"""
    try:
        from market_regime import market_regime_detector
        regime_data = market_regime_detector.detect()

        quadrant = regime_data.get("quadrant_regime", "bull_low_vol")
        confidence = float(regime_data.get("confidence", 0.75))
        probs = regime_data.get("regime_probs", {})

        # 罗盘直角坐标归一化映射 (-1 到 1)
        # X 轴: 趋势动量倾角 (-1 强空, +1 强多)
        # Y 轴: 波动率高低 (-1 极端低波, +1 恐慌高波)
        trend_slope = float(regime_data.get("trend_slope", 0.0))
        annual_vol = float(regime_data.get("annualized_volatility", 0.18))

        compass_x = max(-1.0, min(1.0, trend_slope / 0.35))
        compass_y = max(-1.0, min(1.0, (annual_vol - 0.16) / 0.12))

        quadrant_titles = {
            "bull_low_vol": "牛市低波 (做多主升)",
            "bull_high_vol": "牛市高波 (震荡洗盘)",
            "bear_low_vol": "熊市低波 (磨底阴跌)",
            "bear_high_vol": "熊市高波 (恐慌踩踏)"
        }

        return jsonify({
            "success": True,
            "data": {
                "quadrant_regime": quadrant,
                "quadrant_title": quadrant_titles.get(quadrant, "牛市低波 (做多主升)"),
                "confidence": confidence,
                "confidence_pct": f"{confidence * 100:.1f}%",
                "probabilities": probs,
                "garch_volatility": regime_data.get("garch_volatility", 0.18),
                "annualized_volatility": regime_data.get("annualized_volatility", 0.18),
                "compass_coordinates": {
                    "x": round(compass_x, 3),
                    "y": round(compass_y, 3)
                },
                "factors": regime_data.get("factors", []),
                "description": regime_data.get("description", "")
            }
        })
    except Exception as e:
        logger.exception(f"获取宏观四象限状态异常: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@ai_timing_bp.route('/api/risk/industry-exposure', methods=['GET'])
def get_industry_exposure():
    """获取多账户组合行业敞口矩阵与 HHI 行业集中度指数"""
    try:
        from db_store import db_store
        req_acc_id = request.args.get("account_id", type=int)

        all_accounts = db_store.list_accounts() or []
        if req_acc_id is not None:
            target_accs = [a for a in all_accounts if int(a.get("id")) == req_acc_id]
            if not target_accs:
                target_accs = [{"id": req_acc_id, "name": f"账户#{req_acc_id}"}]
        else:
            target_accs = all_accounts or [{"id": 1, "name": "默认主账户"}]

        # 归集持仓与资产
        total_portfolio_assets = 0.0
        portfolio_positions = {}
        for acc in target_accs:
            acc_id = int(acc.get("id", 1))
            acc_info = db_store.get_account(acc_id) or {}
            acc_assets = float(acc_info.get("balance") or acc_info.get("initial_balance") or 200000.0)
            total_portfolio_assets += acc_assets

            held = db_store.get_positions(acc_id) or {}
            for sym, pos in held.items():
                amt = int(pos.get("amount", 0) or 0)
                if amt > 0:
                    price = float(pos.get("price", 0.0) or pos.get("cost_price", 0.0) or 10.0)
                    mv = float(pos.get("market_value", 0.0) or (amt * price))
                    if sym in portfolio_positions:
                        portfolio_positions[sym]["amount"] += amt
                        portfolio_positions[sym]["market_value"] += mv
                    else:
                        portfolio_positions[sym] = {
                            "symbol": sym,
                            "name": pos.get("name", sym),
                            "amount": amt,
                            "price": price,
                            "market_value": mv,
                            "industry": pos.get("industry") or pos.get("sector") or "主线热门"
                        }

        if total_portfolio_assets <= 0:
            total_portfolio_assets = max(1.0, sum(p["market_value"] for p in portfolio_positions.values()))

        # 汇总行业敞口
        industry_map = {}
        for sym, pos in portfolio_positions.items():
            ind = str(pos.get("industry") or "其他板块").strip()
            # 聚合常见同类细分板块
            if any(k in ind for k in ("新能源", "锂电", "电池")):
                main_ind = "新能源/锂电"
            elif any(k in ind for k in ("金融", "银行", "证券")):
                main_ind = "金融核心"
            elif any(k in ind for k in ("白酒", "消费")):
                main_ind = "消费/白酒"
            elif any(k in ind for k in ("芯片", "半导体", "电子")):
                main_ind = "半导体/芯片"
            elif any(k in ind for k in ("有色", "金属", "周期")):
                main_ind = "有色金属/周期"
            else:
                main_ind = ind or "综合板块"

            if main_ind not in industry_map:
                industry_map[main_ind] = {
                    "industry": main_ind,
                    "market_value": 0.0,
                    "stock_count": 0,
                    "symbols": []
                }
            industry_map[main_ind]["market_value"] += pos["market_value"]
            industry_map[main_ind]["stock_count"] += 1
            industry_map[main_ind]["symbols"].append(f"{pos['name']}({sym})")

        # 计算敞口比例与 HHI 指数
        hhi_index = 0.0
        exposure_list = []
        for ind_name, item in industry_map.items():
            ratio = item["market_value"] / total_portfolio_assets
            item["ratio_pct"] = round(ratio * 100.0, 2)
            item["market_value"] = round(item["market_value"], 2)
            item["is_overweight"] = ratio > 0.30  # 单一行业超配预警 > 30%
            hhi_index += (ratio * 100.0) ** 2
            exposure_list.append(item)

        exposure_list.sort(key=lambda x: x["market_value"], reverse=True)

        return jsonify({
            "success": True,
            "data": {
                "total_portfolio_assets": round(total_portfolio_assets, 2),
                "industry_count": len(exposure_list),
                "hhi_index": round(hhi_index, 1),
                "hhi_evaluation": "分散良好" if hhi_index < 2500 else ("中度集中" if hhi_index < 4000 else "高度集中"),
                "exposures": exposure_list
            }
        })
    except Exception as e:
        logger.exception(f"获取行业敞口矩阵异常: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@ai_timing_bp.route('/api/audit/sign-order', methods=['POST'])
def sign_trade_order():
    """为交易报单/撤单请求生成防篡改 HMAC-SHA256 审计签名"""
    try:
        data = request.get_json(silent=True) or {}
        if not data.get("symbol") or not data.get("action"):
            return jsonify({"success": False, "message": "Missing symbol or action"}), 400

        payload = {
            "account_id": data.get("account_id", 1),
            "symbol": data.get("symbol", ""),
            "action": data.get("action", ""),
            "price": float(data.get("price", 0.0)),
            "amount": int(data.get("amount", 0)),
            "timestamp": data.get("timestamp") or str(int(time.time()))
        }
        sig = generate_trade_audit_signature(payload)
        return jsonify({
            "success": True,
            "data": {
                "signature": sig,
                "payload": payload,
                "algorithm": "HMAC-SHA256"
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@ai_timing_bp.route('/api/audit/verify-signature', methods=['POST'])
def verify_audit_signature():
    """校验报单/审计流水 HMAC-SHA256 签名合法性"""
    try:
        data = request.get_json(silent=True) or {}
        payload = data.get("payload") or {}
        signature = data.get("signature") or ""

        valid = verify_trade_audit_signature(payload, signature)
        return jsonify({
            "success": True,
            "data": {
                "valid": valid,
                "verified_at": int(time.time())
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@ai_timing_bp.route('/api/risk/stress-test', methods=['GET'])
def get_stress_test_telemetry():
    """获取当前组合 4 大历史极端情景压力测试与前瞻 VaR/CVaR 遥测指标"""
    try:
        from stress_tester import scenario_stress_tester
        from db_store import db_store

        req_acc_id = request.args.get("account_id", type=int)
        all_accounts = db_store.list_accounts() or []
        if req_acc_id is not None:
            target_accs = [a for a in all_accounts if int(a.get("id")) == req_acc_id]
            if not target_accs:
                target_accs = [{"id": req_acc_id, "name": f"账户#{req_acc_id}"}]
        else:
            target_accs = all_accounts or [{"id": 1, "name": "默认主账户"}]

        # 汇总持仓与资产
        total_assets = 0.0
        combined_positions = {}
        for acc in target_accs:
            acc_id = int(acc.get("id", 1))
            acc_info = db_store.get_account(acc_id) or {}
            total_assets += float(acc_info.get("balance") or acc_info.get("initial_balance") or 200000.0)

            held = db_store.get_positions(acc_id) or {}
            for sym, pos in held.items():
                amt = int(pos.get("amount", 0) or 0)
                if amt > 0:
                    price = float(pos.get("price", 0.0) or pos.get("cost_price", 0.0) or 10.0)
                    mv = float(pos.get("market_value", 0.0) or (amt * price))
                    if sym in combined_positions:
                        combined_positions[sym]["amount"] += amt
                        combined_positions[sym]["market_value"] += mv
                    else:
                        combined_positions[sym] = dict(pos)
                        combined_positions[sym]["price"] = price
                        combined_positions[sym]["market_value"] = mv

        scenarios_res = scenario_stress_tester.replay_scenarios(combined_positions, total_assets)
        var_res = scenario_stress_tester.calculate_var_cvar(combined_positions, total_assets)

        return jsonify({
            "success": True,
            "data": {
                "account_id": req_acc_id,
                "total_portfolio_assets": round(total_assets, 2),
                "stress_scenarios": scenarios_res["scenarios"],
                "worst_portfolio_loss_pct": scenarios_res["worst_portfolio_loss_pct"],
                "resilience_score": scenarios_res["resilience_score"],
                "resilience_level": scenarios_res["resilience_level"],
                "var_metrics": var_res
            }
        })
    except Exception as e:
        logger.exception(f"执行压力测试遥测异常: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@ai_timing_bp.route('/api/risk/what-if', methods=['POST'])
def post_what_if_analysis():
    """下单前 What-If 边际 VaR/CVaR 沙盘推演与合规预审"""
    try:
        from stress_tester import scenario_stress_tester
        from db_store import db_store

        data = request.get_json(silent=True) or {}
        sym = data.get("symbol")
        shares = int(data.get("shares") or data.get("amount") or 0)
        price = float(data.get("price") or 0.0)

        if not sym or shares <= 0 or price <= 0:
            return jsonify({"success": False, "message": "Invalid symbol, shares or price"}), 400

        acc_id = int(data.get("account_id") or 1)
        acc_info = db_store.get_account(acc_id) or {}
        total_assets = float(acc_info.get("balance") or acc_info.get("initial_balance") or 200000.0)
        held_positions = db_store.get_positions(acc_id) or {}

        result = scenario_stress_tester.what_if_marginal_analysis(
            current_positions=held_positions,
            total_assets=total_assets,
            new_symbol=sym,
            new_shares=shares,
            new_price=price,
            new_name=data.get("name", ""),
            new_industry=data.get("industry", "")
        )

        return jsonify({
            "success": True,
            "data": result
        })
    except Exception as e:
        logger.exception(f"What-If 沙盘推演异常: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
