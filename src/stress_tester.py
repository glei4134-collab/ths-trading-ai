# -*- coding: utf-8 -*-
"""
极端情景压力测试与下单前 What-If 边际 VaR/CVaR 沙盘推演引擎 (Scenario Stress Tester)
Stage 3 核心风险度量模块

核心能力:
1. 4 大历史极端情景重放:
   - 2015 千股跌停与流动性枯竭 (2015 Flash Crash)
   - 2020 全球流动性危机与恐慌熔断 (2020 Global Liquidity Shock)
   - 2021 核心资产白马抱团瓦解 (2021 Core Asset Unwind)
   - 2024 微盘股流动性踩踏与量化平仓 (2024 Micro-Cap Stampede)
2. 参数化 / 历史模拟 VaR (95%, 99%) 与 CVaR (Expected Shortfall) 计算
3. 下单前 What-If 边际风险与多维合规沙盘推演 (Marginal VaR / CVaR / 35%红线)
"""

import math
import logging
from typing import Dict, List, Optional, Any, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class ExtremeScenarios:
    """4大极端情景特征基线"""
    SCENARIO_2015_FLASH_CRASH = "2015_flash_crash"
    SCENARIO_2020_COVID_SHOCK = "2020_covid_shock"
    SCENARIO_2021_CORE_UNWIND = "2021_core_unwind"
    SCENARIO_2024_MICRO_CAP_STAMPEDE = "2024_micro_cap_stampede"

    DEFINITIONS = {
        SCENARIO_2015_FLASH_CRASH: {
            "name": "2015千股跌停与流动性枯竭",
            "market_shock_pct": -8.5,
            "liquidity_haircut": 0.40,
            "industry_multipliers": {
                "金融": 0.65, "核心金融": 0.60, "银行": 0.50,
                "白酒": 0.85, "白酒龙头": 0.80,
                "新能源": 1.30, "锂电": 1.35, "芯片": 1.40,
                "小微盘": 1.50, "default": 1.15
            },
            "desc": "场外配资去杠杆，流动性休克，千股连续无量跌停"
        },
        SCENARIO_2020_COVID_SHOCK: {
            "name": "2020全球流动性危机与恐慌熔断",
            "market_shock_pct": -4.8,
            "liquidity_haircut": 0.25,
            "industry_multipliers": {
                "金融": 0.80, "核心金融": 0.75, "银行": 0.70,
                "白酒": 1.05, "医药": 0.40,
                "新能源": 1.25, "有色金属": 1.35, "周期": 1.30,
                "小微盘": 1.20, "default": 1.00
            },
            "desc": "全球资产恐慌无差别抛售，北向资金单周创纪录净流出"
        },
        SCENARIO_2021_CORE_UNWIND: {
            "name": "2021核心资产白马抱团瓦解",
            "market_shock_pct": -3.2,
            "liquidity_haircut": 0.15,
            "industry_multipliers": {
                "金融": 0.40, "核心金融": 0.45, "银行": 0.35,
                "白酒": 1.85, "白酒龙头": 1.90, "核心消费": 1.60,
                "新能源": 0.70, "电池龙头": 0.75, "光伏": 0.70,
                "小微盘": 0.50, "default": 0.85
            },
            "desc": "高估值公募抱团股雪崩，机构踩踏出逃，小微盘与周期逆市抗跌"
        },
        SCENARIO_2024_MICRO_CAP_STAMPEDE: {
            "name": "2024微盘股流动性踩踏与量化平仓",
            "market_shock_pct": -3.8,
            "liquidity_haircut": 0.35,
            "industry_multipliers": {
                "金融": 0.30, "核心金融": 0.25, "权重蓝筹": 0.30,
                "白酒龙头": 0.50, "大市值": 0.40,
                "新能源": 1.10, "成长": 1.20,
                "小微盘": 2.20, "微盘股": 2.40, "default": 1.10
            },
            "desc": "DMA杠杆集中平仓，微盘股中证2000连续重挫，大盘蓝筹托底分化"
        }
    }


class ScenarioStressTester:
    """极端情景压力测试与 What-If 边际沙盘推演器"""

    def __init__(self):
        pass

    def _get_industry_multiplier(self, scenario_key: str, industry_str: str) -> float:
        defn = ExtremeScenarios.DEFINITIONS.get(scenario_key, {})
        multipliers = defn.get("industry_multipliers", {})
        ind = str(industry_str or "").strip()
        for k, v in multipliers.items():
            if k != "default" and k in ind:
                return v
        return multipliers.get("default", 1.0)

    def replay_scenarios(self, positions: Dict[str, Any], total_assets: float) -> Dict[str, Any]:
        """
        对当前持仓全要素执行 4 大极端历史情景重放推演
        :param positions: {symbol: {name, amount, cost_price, price, market_value, industry...}}
        :param total_assets: 账户当前总资产
        :return: 包含 4 大情景的预期损失额、跌幅、受创最重标的及韧性评分
        """
        if total_assets <= 0:
            total_assets = max(1.0, sum(p.get("market_value", 0) for p in positions.values()))

        results = {}
        worst_scenario_loss_pct = 0.0

        for sc_key, sc_info in ExtremeScenarios.DEFINITIONS.items():
            mkt_shock = sc_info["market_shock_pct"] / 100.0  # 如 -0.085
            haircut = sc_info["liquidity_haircut"]

            sim_pos_losses = []
            total_loss_amount = 0.0

            for sym, pos in positions.items():
                amt = int(pos.get("amount", 0) or 0)
                if amt <= 0:
                    continue
                price = float(pos.get("price", 0.0) or pos.get("cost_price", 0.0) or 10.0)
                mv = float(pos.get("market_value", 0.0) or (amt * price))
                ind = pos.get("industry") or pos.get("sector") or ""
                name = pos.get("name", sym)

                multiplier = self._get_industry_multiplier(sc_key, ind)
                # 预计个股受损率
                stock_loss_rate = mkt_shock * multiplier * (1.0 + haircut * 0.2)
                stock_loss_rate = max(-0.25, min(0.05, stock_loss_rate)) # 限制单日极端跌停范围

                loss_amount = abs(mv * stock_loss_rate)
                total_loss_amount += loss_amount

                sim_pos_losses.append({
                    "symbol": sym,
                    "name": name,
                    "market_value": round(mv, 2),
                    "estimated_loss_rate_pct": round(stock_loss_rate * 100, 2),
                    "estimated_loss_amount": round(loss_amount, 2)
                })

            sim_pos_losses.sort(key=lambda x: x["estimated_loss_amount"], reverse=True)
            port_loss_pct = (total_loss_amount / total_assets) * 100.0
            if port_loss_pct > worst_scenario_loss_pct:
                worst_scenario_loss_pct = port_loss_pct

            results[sc_key] = {
                "name": sc_info["name"],
                "desc": sc_info["desc"],
                "portfolio_loss_amount": round(total_loss_amount, 2),
                "portfolio_loss_pct": round(port_loss_pct, 2),
                "is_stop_loss_breached": port_loss_pct >= 4.5,  # 是否触及团队 -4.5% 止损线
                "worst_hit_stock": sim_pos_losses[0] if sim_pos_losses else None,
                "positions_detail": sim_pos_losses
            }

        # 组合情景韧性评分 (0~100)
        resilience_score = max(0.0, min(100.0, 100.0 - (worst_scenario_loss_pct * 8.5)))

        return {
            "scenarios": results,
            "worst_portfolio_loss_pct": round(worst_scenario_loss_pct, 2),
            "resilience_score": round(resilience_score, 1),
            "resilience_level": "EXCELLENT" if resilience_score >= 80 else ("FAIR" if resilience_score >= 60 else "VULNERABLE")
        }

    def calculate_var_cvar(self, positions: Dict[str, Any], total_assets: float, 
                           confidence_levels: Tuple[float, float] = (0.95, 0.99)) -> Dict[str, Any]:
        """
        计算多资产持仓组合前瞻 Parametric & Historical VaR 与 CVaR (Expected Shortfall)
        :param positions: 账户当前持仓
        :param total_assets: 当前资产
        :param confidence_levels: 置信区间 (0.95, 0.99)
        :return: VaR / CVaR 结果字典
        """
        if total_assets <= 0:
            total_assets = max(1.0, sum(p.get("market_value", 0) for p in positions.values()))

        # 提取权重与个股典型年化波动率（基于行业与A股典型分布）
        weights = []
        volatilities = []
        for sym, pos in positions.items():
            amt = int(pos.get("amount", 0) or 0)
            if amt <= 0:
                continue
            mv = float(pos.get("market_value", 0.0) or 0.0)
            w = mv / total_assets
            weights.append(w)
            
            # 行业波动率基准（日频）
            ind = str(pos.get("industry") or pos.get("sector") or "")
            if any(k in ind for k in ("新能源", "锂电", "芯片", "半导体")):
                daily_vol = 0.024
            elif any(k in ind for k in ("金融", "银行")):
                daily_vol = 0.012
            elif any(k in ind for k in ("白酒", "消费")):
                daily_vol = 0.018
            else:
                daily_vol = 0.019
            volatilities.append(daily_vol)

        total_weight = sum(weights)
        if total_weight <= 0.001:
            return {
                "var_95_pct": 0.0, "var_95_amount": 0.0,
                "var_99_pct": 0.0, "var_99_amount": 0.0,
                "cvar_95_pct": 0.0, "cvar_95_amount": 0.0,
                "cvar_99_pct": 0.0, "cvar_99_amount": 0.0,
                "holding_count": 0, "total_exposure_ratio": 0.0
            }

        # 假设标的间平均相关系数 0.45 构造协方差阵
        n = len(weights)
        corr_matrix = np.full((n, n), 0.45)
        np.fill_diagonal(corr_matrix, 1.0)
        
        cov_matrix = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                cov_matrix[i, j] = corr_matrix[i, j] * volatilities[i] * volatilities[j]

        w_arr = np.array(weights)
        port_daily_variance = float(np.dot(w_arr.T, np.dot(cov_matrix, w_arr)))
        port_daily_sigma = math.sqrt(max(1e-8, port_daily_variance))

        # 标准正态分位数 (z_95 = 1.645, z_99 = 2.326)
        # CVaR (Expected Shortfall) = sigma * (pdf(z) / (1 - alpha))
        z_95 = 1.64485
        z_99 = 2.32635
        
        es_mult_95 = 2.06271  # phi(1.645) / 0.05
        es_mult_99 = 2.66521  # phi(2.326) / 0.01

        var_95_pct = port_daily_sigma * z_95 * 100.0
        var_99_pct = port_daily_sigma * z_99 * 100.0
        cvar_95_pct = port_daily_sigma * es_mult_95 * 100.0
        cvar_99_pct = port_daily_sigma * es_mult_99 * 100.0

        return {
            "var_95_pct": round(var_95_pct, 2),
            "var_95_amount": round(total_assets * (var_95_pct / 100.0), 2),
            "var_99_pct": round(var_99_pct, 2),
            "var_99_amount": round(total_assets * (var_99_pct / 100.0), 2),
            "cvar_95_pct": round(cvar_95_pct, 2),
            "cvar_95_amount": round(total_assets * (cvar_95_pct / 100.0), 2),
            "cvar_99_pct": round(cvar_99_pct, 2),
            "cvar_99_amount": round(total_assets * (cvar_99_pct / 100.0), 2),
            "portfolio_daily_sigma": round(port_daily_sigma, 4),
            "holding_count": n,
            "total_exposure_ratio": round(total_weight * 100.0, 1)
        }

    def what_if_marginal_analysis(self, current_positions: Dict[str, Any], total_assets: float,
                                  new_symbol: str, new_shares: int, new_price: float,
                                  new_name: str = "", new_industry: str = "") -> Dict[str, Any]:
        """
        下单前 What-If 边际 VaR/CVaR 沙盘推演与合规预审
        :param current_positions: 下单前持仓
        :param total_assets: 账户总资产
        :param new_symbol: 拟买入标的代码
        :param new_shares: 拟买入股数
        :param new_price: 拟买入价格
        :return: 下单前后风险指标对比、边际变化 (Delta VaR) 与硬红线判定
        """
        sym = str(new_symbol).strip()
        add_mv = float(new_shares * new_price)

        # 1. 计算原始组合风险
        pre_risk = self.calculate_var_cvar(current_positions, total_assets)
        pre_scenarios = self.replay_scenarios(current_positions, total_assets)

        # 2. 构建假设买入后的虚拟组合
        post_positions = {k: dict(v) for k, v in current_positions.items()}
        if sym in post_positions:
            old_amt = int(post_positions[sym].get("amount", 0) or 0)
            old_mv = float(post_positions[sym].get("market_value", 0.0) or (old_amt * new_price))
            post_positions[sym]["amount"] = old_amt + new_shares
            post_positions[sym]["market_value"] = old_mv + add_mv
            post_positions[sym]["price"] = new_price
        else:
            post_positions[sym] = {
                "symbol": sym,
                "name": new_name or sym,
                "amount": new_shares,
                "price": new_price,
                "market_value": add_mv,
                "industry": new_industry or "综合热门"
            }

        # 3. 计算买入后组合风险
        post_risk = self.calculate_var_cvar(post_positions, total_assets)
        post_scenarios = self.replay_scenarios(post_positions, total_assets)

        # 4. 边际风险度量 (Marginal Contribution)
        delta_var_95_pct = round(post_risk["var_95_pct"] - pre_risk["var_95_pct"], 2)
        delta_cvar_95_pct = round(post_risk["cvar_95_pct"] - pre_risk["cvar_95_pct"], 2)

        # 5. 团队赛 35% 单票硬红线核验
        post_target_mv = float(post_positions[sym].get("market_value", 0.0))
        target_stock_ratio = (post_target_mv / total_assets) if total_assets > 0 else 0.0
        is_single_limit_breached = target_stock_ratio > 0.35

        post_holding_count = len([s for s, p in post_positions.items() if p.get("amount", 0) > 0])
        is_diversification_satisfied = post_holding_count >= 3

        return {
            "symbol": sym,
            "name": new_name or sym,
            "order_shares": new_shares,
            "order_price": new_price,
            "order_amount": round(add_mv, 2),
            "target_stock_ratio_pct": round(target_stock_ratio * 100.0, 2),
            "is_single_limit_breached": is_single_limit_breached,
            "is_diversification_satisfied": is_diversification_satisfied,
            "post_holding_count": post_holding_count,
            "pre_var_95_pct": pre_risk["var_95_pct"],
            "post_var_95_pct": post_risk["var_95_pct"],
            "delta_var_95_pct": delta_var_95_pct,
            "pre_cvar_95_pct": pre_risk["cvar_95_pct"],
            "post_cvar_95_pct": post_risk["cvar_95_pct"],
            "delta_cvar_95_pct": delta_cvar_95_pct,
            "pre_resilience_score": pre_scenarios["resilience_score"],
            "post_resilience_score": post_scenarios["resilience_score"],
            "delta_resilience": round(post_scenarios["resilience_score"] - pre_scenarios["resilience_score"], 1),
            "worst_scenario_loss_post_pct": post_scenarios["worst_portfolio_loss_pct"],
            "execution_approval": not is_single_limit_breached and (post_risk["var_95_pct"] <= 5.0)
        }


# 全局单例
scenario_stress_tester = ScenarioStressTester()
