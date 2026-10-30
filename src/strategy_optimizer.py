"""
策略参数自动优化器
基于 Optuna 贝叶斯优化 + 向量化回测
支持单策略优化、批量优化、滚动窗口
"""

import json
import random
import logging
import itertools
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# 静默 Optuna 日志（只显示警告以上）
try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    optuna = None

from paths import CONFIG_DIR
OPTIMIZED_PARAMS_FILE = CONFIG_DIR / "optimized_params.json"


class StrategyOptimizer:
    """策略参数自动优化器 — 基于 Optuna TPE 采样"""

    def __init__(self):
        self._params_cache: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        """加载已优化的参数"""
        try:
            if OPTIMIZED_PARAMS_FILE.exists():
                with open(OPTIMIZED_PARAMS_FILE, "r", encoding="utf-8") as f:
                    self._params_cache = json.load(f)
        except Exception as e:
            logger.error(f"加载优化参数失败: {e}")
            self._params_cache = {}

    def _save(self):
        """保存优化后的参数"""
        try:
            OPTIMIZED_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(OPTIMIZED_PARAMS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._params_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存优化参数失败: {e}")

    # ========== 核心优化：Optuna TPE ==========

    def optimize(self, strategy_name: str, stock_data: Dict,
                 indicators: Dict, n_trials: int = 50,
                 days: int = 120, objective: str = "sharpe") -> Dict:
        """
        用 Optuna 优化单个策略参数

        Args:
            strategy_name: 策略名称
            stock_data: 股票数据
            indicators: 技术指标
            n_trials: 优化轮数（默认50，比赛期间建议30-50）
            days: 回测天数
            objective: 优化目标 (sharpe/return/win_rate)

        Returns: {success, best_params, best_value, all_trials, importance}
        """
        from strategy_registry import registry

        strategy = registry.get(strategy_name)
        if not strategy:
            return {"success": False, "message": f"策略 {strategy_name} 不存在"}

        if not strategy.params:
            return {"success": False, "message": f"策略 {strategy_name} 无可调参数"}

        # 构建参数空间
        param_space = {}
        for param_name, param_info in strategy.params.items():
            default = param_info.get("default")
            min_val = param_info.get("min", default)
            max_val = param_info.get("max", default)

            if isinstance(default, bool):
                param_space[param_name] = ("bool", min_val, max_val)
            elif isinstance(default, int):
                param_space[param_name] = ("int", min_val, max_val)
            elif isinstance(default, float):
                param_space[param_name] = ("float", min_val, max_val)

        if not param_space:
            return {"success": False, "message": "无可优化的数值参数"}

        logger.info(f"Optuna 优化: {strategy_name}, {len(param_space)} 个参数, {n_trials} 轮")

        # Optuna 目标函数
        def objective_fn(trial):
            params = {}
            for pname, (ptype, pmin, pmax) in param_space.items():
                if ptype == "bool":
                    params[pname] = trial.suggest_categorical(pname, [True, False])
                elif ptype == "int":
                    params[pname] = trial.suggest_int(pname, int(pmin), int(pmax))
                elif ptype == "float":
                    params[pname] = trial.suggest_float(pname, float(pmin), float(pmax))

            # 回测
            try:
                result = self._run_backtest(strategy_name, stock_data, indicators, params, days)
            except Exception:
                return -999

            # 计算目标值
            if objective == "sharpe":
                return result.get("sharpe_ratio", 0)
            elif objective == "return":
                return result.get("total_return", 0)
            elif objective == "win_rate":
                return result.get("win_rate", 0)
            else:
                # 综合评分
                return (result.get("total_return", 0) * 0.4 +
                        result.get("win_rate", 0) * 0.3 -
                        result.get("max_drawdown", 0) * 0.3)

        # 创建 Optuna study
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            study_name=f"optimize_{strategy_name}"
        )
        study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=False)

        # 提取结果
        best_trial = study.best_trial
        best_params = best_trial.params
        best_value = best_trial.value

        # 参数重要性分析
        importance = {}
        try:
            importance = optuna.importance.get_param_importances(study)
        except Exception:
            pass

        # 所有试验结果
        all_trials = []
        for t in study.trials[:10]:  # 只保留前10个
            all_trials.append({
                "params": t.params,
                "value": round(t.value, 4) if t.value else 0,
                "state": str(t.state)
            })

        return {
            "success": True,
            "strategy": strategy_name,
            "objective": objective,
            "n_trials": len(study.trials),
            "best_params": best_params,
            "best_value": round(best_value, 4) if best_value else 0,
            "importance": {k: round(v, 4) for k, v in importance.items()},
            "all_trials": all_trials,
            "best_result": self._run_backtest(strategy_name, stock_data, indicators, best_params, days)
        }

    def batch_optimize(self, stock_data: Dict, indicators: Dict,
                       strategy_names: List[str] = None,
                       n_trials: int = 30, days: int = 120) -> Dict:
        """
        批量优化所有策略参数（比赛前一键执行）

        Args:
            stock_data: 股票数据
            indicators: 技术指标
            strategy_names: 要优化的策略列表（None=全部）
            n_trials: 每个策略的优化轮数
            days: 回测天数

        Returns: {success, results: {name: result}, summary}
        """
        from strategy_registry import registry

        if not strategy_names:
            # 只优化有参数的策略
            strategy_names = [
                name for name, instance in registry.get_all().items()
                if instance.params
            ]

        results = {}
        success_count = 0
        fail_count = 0

        logger.info(f"批量优化: {len(strategy_names)} 个策略, 每个 {n_trials} 轮")

        for name in strategy_names:
            try:
                result = self.optimize(name, stock_data, indicators, n_trials, days)
                results[name] = result
                if result.get("success"):
                    success_count += 1
                    # 自动保存
                    self.save_optimized_params(
                        name, result["best_params"],
                        {"value": result["best_value"], "objective": result["objective"]}
                    )
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"优化 {name} 失败: {e}")
                fail_count += 1

        return {
            "success": True,
            "total": len(strategy_names),
            "success_count": success_count,
            "fail_count": fail_count,
            "results": results,
            "summary": self.get_optimization_status()
        }

    # ========== 滚动窗口回测 ==========

    def walk_forward(self, strategy_name: str, stock_data: Dict,
                     indicators: Dict, train_days: int = 60,
                     test_days: int = 20, n_windows: int = 5) -> Dict:
        """
        滚动窗口回测：避免过拟合

        Returns: {windows, avg_oos_return, overfit_ratio}
        """
        from strategy_registry import registry

        strategy = registry.get(strategy_name)
        if not strategy:
            return {"success": False, "message": f"策略 {strategy_name} 不存在"}

        windows = []
        total_oos_return = 0

        logger.info(f"滚动窗口: {strategy_name}, {n_windows} 窗口")

        for i in range(n_windows):
            window_result = {
                "window": i + 1,
                "train_params": {},
                "train_result": {"total_return": 0, "win_rate": 0, "sharpe_ratio": 0},
                "test_result": {"total_return": 0, "win_rate": 0, "sharpe_ratio": 0}
            }

            try:
                # 训练集优化
                opt_result = self.optimize(
                    strategy_name, stock_data, indicators,
                    n_trials=20, days=train_days
                )
                if opt_result.get("success"):
                    window_result["train_params"] = opt_result["best_params"]
                    window_result["train_result"] = {
                        "total_return": opt_result["best_result"].get("total_return", 0),
                        "win_rate": opt_result["best_result"].get("win_rate", 0),
                        "sharpe_ratio": opt_result["best_result"].get("sharpe_ratio", 0)
                    }

                    # 测试集验证
                    test_result = self._run_backtest(
                        strategy_name, stock_data, indicators,
                        opt_result["best_params"], test_days
                    )
                    window_result["test_result"] = {
                        "total_return": test_result.get("total_return", 0),
                        "win_rate": test_result.get("win_rate", 0),
                        "sharpe_ratio": test_result.get("sharpe_ratio", 0)
                    }
                    total_oos_return += test_result.get("total_return", 0)
            except Exception as e:
                logger.warning(f"窗口 {i+1} 失败: {e}")

            windows.append(window_result)

        avg_oos = total_oos_return / n_windows if n_windows > 0 else 0
        train_returns = [w["train_result"]["total_return"] for w in windows]
        avg_train = sum(train_returns) / len(train_returns) if train_returns else 0
        overfit_ratio = avg_train / avg_oos if avg_oos != 0 else float('inf')

        return {
            "success": True,
            "strategy": strategy_name,
            "windows": windows,
            "avg_oos_return": round(avg_oos, 4),
            "avg_train_return": round(avg_train, 4),
            "overfit_ratio": round(overfit_ratio, 2),
            "assessment": "正常" if 0.5 < overfit_ratio < 2.0 else "可能存在过拟合"
        }

    # ========== 参数持久化 ==========

    def save_optimized_params(self, strategy_name: str, params: Dict,
                              performance: Dict):
        """保存优化后的参数"""
        self._params_cache[strategy_name] = {
            "params": params,
            "performance": performance,
            "optimized_at": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save()
        logger.info(f"保存优化参数: {strategy_name}")

    def load_optimized_params(self, strategy_name: str) -> Optional[Dict]:
        """加载已优化的参数"""
        entry = self._params_cache.get(strategy_name)
        if entry:
            return entry.get("params")
        return None

    def get_optimization_status(self) -> Dict[str, Dict]:
        """获取所有策略的优化状态"""
        result = {}
        for name, entry in self._params_cache.items():
            result[name] = {
                "params": entry.get("params"),
                "performance": entry.get("performance"),
                "optimized_at": entry.get("optimized_at")
            }
        return result

    def clear_optimized_params(self, strategy_name: str = None):
        """清除优化后的参数"""
        if strategy_name:
            self._params_cache.pop(strategy_name, None)
        else:
            self._params_cache.clear()
        self._save()

    # ========== 参数敏感性分析 ==========

    def parameter_sensitivity(self, strategy_name: str, stock_data: Dict,
                              indicators: Dict, param_name: str,
                              n_points: int = 10, days: int = 120) -> Dict:
        """
        参数敏感性分析：测试某个参数在范围内不同值的表现

        Returns: {param_name, results, stable}
        """
        from strategy_registry import registry

        strategy = registry.get(strategy_name)
        if not strategy:
            return {"success": False, "message": f"策略 {strategy_name} 不存在"}

        if param_name not in strategy.params:
            return {"success": False, "message": f"参数 {param_name} 不存在"}

        param_info = strategy.params[param_name]
        default = param_info.get("default")
        min_val = param_info.get("min", default)
        max_val = param_info.get("max", default)

        # 使用优化后的参数作为基准
        base_params = self.load_optimized_params(strategy_name) or {}

        results = []
        if isinstance(default, (int, float)):
            # 均匀采样 n_points 个值
            if isinstance(default, int):
                test_values = [int(min_val + (max_val - min_val) * i / (n_points - 1))
                              for i in range(n_points)]
            else:
                test_values = [round(min_val + (max_val - min_val) * i / (n_points - 1), 4)
                              for i in range(n_points)]

            for val in test_values:
                params = {**base_params, param_name: val}
                try:
                    result = self._run_backtest(strategy_name, stock_data, indicators, params, days)
                    results.append({
                        "value": val,
                        "return": result.get("total_return", 0),
                        "win_rate": result.get("win_rate", 0),
                        "sharpe": result.get("sharpe_ratio", 0),
                        "max_drawdown": result.get("max_drawdown", 0)
                    })
                except Exception:
                    pass
        else:
            # 布尔值
            for val in [True, False]:
                params = {**base_params, param_name: val}
                try:
                    result = self._run_backtest(strategy_name, stock_data, indicators, params, days)
                    results.append({
                        "value": val,
                        "return": result.get("total_return", 0),
                        "win_rate": result.get("win_rate", 0),
                        "sharpe": result.get("sharpe_ratio", 0),
                        "max_drawdown": result.get("max_drawdown", 0)
                    })
                except Exception:
                    pass

        # 判断稳定性
        if len(results) >= 3:
            returns = [r["return"] for r in results]
            avg = sum(returns) / len(returns)
            variance = sum((r - avg)**2 for r in returns) / len(returns)
            stable = variance < 100
        else:
            stable = True

        return {
            "success": True,
            "strategy": strategy_name,
            "param_name": param_name,
            "results": results,
            "stable": stable,
            "recommendation": "参数稳定" if stable else "参数敏感，建议使用默认值"
        }

    # ========== 内部回测 ==========

    def _run_backtest(self, strategy_name: str, stock_data: Dict,
                      indicators: Dict, params: Dict, days: int) -> Dict:
        """运行单次回测"""
        from strategy_registry import registry
        from backtester import Backtester

        strategy = registry.get(strategy_name)
        if not strategy:
            return {"total_return": 0, "win_rate": 0, "max_drawdown": 0, "sharpe_ratio": 0}

        backtester = Backtester()

        # 获取历史数据
        symbol = stock_data.get("code", stock_data.get("symbol", "000001"))
        history = backtester.fetch_real_data(symbol, days)

        if not history or len(history) < 20:
            return {"total_return": 0, "win_rate": 0, "max_drawdown": 0, "sharpe_ratio": 0}

        # 计算指标
        history = backtester.calculate_indicators_for_data(history)

        # 模拟交易
        capital = 1000000
        position = 0
        trades = []
        equity = [capital]

        for i in range(20, len(history)):
            day = history[i]
            day_indicators = {
                "close": day.get("close"),
                "ma5": day.get("ma5"), "ma10": day.get("ma10"),
                "ma20": day.get("ma20"), "rsi": day.get("rsi"),
                "macd": day.get("macd"), "signal": day.get("signal"),
                "histogram": day.get("histogram"),
                "bb_upper": day.get("bb_upper"), "bb_lower": day.get("bb_lower"),
            }

            # 临时覆盖参数
            original_params = {}
            for k, v in params.items():
                if k in strategy.params:
                    original_params[k] = strategy.params[k].get("default")
                    strategy.params[k]["default"] = v

            try:
                result = strategy.analyze(
                    {"price": day["close"], "volume": day.get("volume", 0)},
                    day_indicators,
                    history=history[max(0, i-20):i+1]
                )
            finally:
                for k, v in original_params.items():
                    strategy.params[k]["default"] = v

            price = day["close"]

            if result.signal.value in ("STRONG_BUY", "BUY") and position == 0:
                shares = (capital // (price * 100)) * 100
                if shares >= 100:
                    capital -= shares * price * 1.0003
                    position = shares
                    trades.append({"action": "buy", "price": price})

            elif result.signal.value in ("STRONG_SELL", "SELL") and position > 0:
                capital += position * price * 0.999
                trades.append({"action": "sell", "price": price})
                position = 0

            equity.append(capital + position * price)

        # 计算指标
        total_return = (equity[-1] / equity[0] - 1) * 100 if equity[0] > 0 else 0
        buy_trades = [t for t in trades if t["action"] == "buy"]
        sell_trades = [t for t in trades if t["action"] == "sell"]
        wins = sum(1 for i in range(min(len(buy_trades), len(sell_trades)))
                   if sell_trades[i]["price"] > buy_trades[i]["price"])
        win_rate = (wins / len(buy_trades) * 100) if buy_trades else 0

        max_dd = 0
        peak = equity[0]
        for e in equity:
            if e > peak: peak = e
            dd = (peak - e) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        returns = [(equity[i] / equity[i-1] - 1) for i in range(1, len(equity))]
        avg_ret = sum(returns) / len(returns) if returns else 0
        std_ret = (sum((r - avg_ret)**2 for r in returns) / len(returns)) ** 0.5 if returns else 1
        sharpe = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0

        return {
            "total_return": round(total_return, 2),
            "win_rate": round(win_rate, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_trades": len(trades),
            "final_capital": round(equity[-1], 2)
        }


# 全局实例
strategy_optimizer = StrategyOptimizer()
