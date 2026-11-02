"""
统计显著性与回测过拟合检验模块 (Strategy Statistical Significance & Overfitting Auditing)
严格基于 Marcos López de Prado (2012, 2014) 经典量化理论实现：
1. 概率夏普比率 (Probabilistic Sharpe Ratio, PSR) —— 修正高阶矩（偏度、超额峰度）与有限样本长度对夏普比率的误判。
2. 降维偏误夏普比率 (Deflated Sharpe Ratio, DSR) —— 修正多重测试 (Multiple Testing)、数据挖掘选择性偏差 (Selection Bias) 与参数网格搜索过拟合。
3. 索提诺比率 (Sortino Ratio) 与卡玛比率 (Calmar Ratio) 严密实现。
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
try:
    import scipy.stats as stats
    _HAS_SCIPY = True
except ImportError:
    stats = None
    _HAS_SCIPY = False

logger = logging.getLogger(__name__)

# 欧拉-马歇罗尼常数 (Euler-Mascheroni Constant)
EULER_MASCHERONI = 0.57721566490153286060


def calc_higher_moments(returns: np.ndarray) -> Tuple[float, float, float, float]:
    """
    计算收益率序列的各阶样本矩：
    - mean (均值)
    - std (样本标准差，自由度 N-1)
    - skewness (偏度)
    - kurtosis (全峰度 Pearson Kurtosis，正态分布为 3.0)

    Args:
        returns: 一维 numpy 数组，周期收益率序列

    Returns:
        (mean, std, skewness, kurtosis)
    """
    if len(returns) < 3:
        return 0.0, 1.0, 0.0, 3.0

    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std < 1e-12:
        return mean, 1.0, 0.0, 3.0

    # 消除微小偏置
    z = (returns - mean) / std
    skewness = float(np.mean(z ** 3))
    kurtosis = float(np.mean(z ** 4))  # Pearson 峰度 (正态为 3.0)

    return mean, std, skewness, kurtosis


def calc_sharpe_ratio(
    returns: Union[List[float], np.ndarray],
    risk_free_rate: float = 0.0,
    freq: int = 250
) -> float:
    """
    计算年化夏普比率 (Annualized Sharpe Ratio)

    Args:
        returns: 收益率序列
        risk_free_rate: 年化无风险利率 (默认 0.0)
        freq: 每年交易周期数 (日频为 250)
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 2:
        return 0.0

    rf_per_period = risk_free_rate / freq
    excess_r = r - rf_per_period
    mean_excess = np.mean(excess_r)
    std_excess = np.std(excess_r, ddof=1)

    if std_excess < 1e-12:
        return 0.0

    daily_sr = mean_excess / std_excess
    return float(daily_sr * np.sqrt(freq))


def calc_sortino_ratio(
    returns: Union[List[float], np.ndarray],
    target_return: float = 0.0,
    freq: int = 250
) -> float:
    """
    计算年化索提诺比率 (Annualized Sortino Ratio)
    仅度量下行波动风险 (Downside Deviation)，惩罚下行亏损而不惩罚向上收益。

    Args:
        returns: 收益率序列
        target_return: 目标最低年化收益率 (默认 0.0)
        freq: 每年交易周期数 (日频为 250)
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 2:
        return 0.0

    target_per_period = target_return / freq
    excess_r = r - target_per_period
    mean_excess = np.mean(excess_r)

    # 提取下行收益差值 (负值部分)
    downside_r = np.minimum(0.0, excess_r)
    downside_variance = np.mean(downside_r ** 2)
    downside_std = np.sqrt(downside_variance)

    if downside_std < 1e-12:
        return 99.0 if mean_excess > 0 else 0.0

    daily_sortino = mean_excess / downside_std
    return float(daily_sortino * np.sqrt(freq))


def calc_calmar_ratio(annual_return: float, max_drawdown_pct: float) -> float:
    """
    计算卡玛比率 (Calmar Ratio)
    Calmar = 年化收益率 / 最大回撤幅度 (绝对值)
    """
    abs_dd = abs(max_drawdown_pct)
    if abs_dd < 1e-6:
        return 99.0 if annual_return > 0 else 0.0
    return float(annual_return / abs_dd)


def calc_probabilistic_sharpe_ratio(
    returns: Union[List[float], np.ndarray],
    benchmark_sr_ann: float = 0.0,
    freq: int = 250
) -> float:
    """
    概率夏普比率 (Probabilistic Sharpe Ratio, PSR) —— López de Prado (2012)
    检验在样本收益率存在非正态性（非零偏度、厚尾/峰度）时，策略真实夏普比率高于基准阈值 benchmark_sr_ann 的置信概率。

    数学公式：
    PSR(SR*) = Phi( (SR_hat - SR*) / sigma_SR )
    其中：
    sigma_SR = sqrt( (1 - gamma_3 * SR_hat + (gamma_4 - 1)/4 * SR_hat^2) / (T - 1) )
    注：计算在单期 (per-period) 尺度下执行，确保时间维度的数学严谨性。

    Args:
        returns: 收益率序列 (一维数组)
        benchmark_sr_ann: 年化基准夏普比率 (通常取 0.0 或指数基准的夏普比率)
        freq: 每年交易周期数 (日频为 250)

    Returns:
        置信度概率 [0.0, 1.0]。若 PSR > 0.95，表示在 95% 置信度下拒绝无超额收益的原假设。
    """
    r = np.asarray(returns, dtype=np.float64)
    T = len(r)
    if T < 5:
        return 0.5

    mean, std, skewness, kurtosis = calc_higher_moments(r)
    if std < 1e-12:
        return 0.5

    # 单期夏普比率
    sr_hat = mean / std
    sr_star = benchmark_sr_ann / np.sqrt(freq)

    # 单期夏普估计量的渐近方差
    variance_sr = (1.0 - skewness * sr_hat + ((kurtosis - 1.0) / 4.0) * (sr_hat ** 2)) / (T - 1)
    if variance_sr <= 0:
        variance_sr = 1.0 / (T - 1)

    std_sr = np.sqrt(variance_sr)
    z_score = (sr_hat - sr_star) / std_sr

    psr = float(norm_cdf(z_score))
    return max(0.0, min(1.0, psr))


def calc_expected_max_sharpe(
    n_trials: int,
    var_sharpe_ann: float,
    freq: int = 250
) -> float:
    """
    估算在零真实Alpha假设下，经过 N 次独立/弱相关策略试验后，可能出现的最高虚假年化夏普比率的期望值：
    E[max_n SR_n] ≈ sqrt(V) * ( (1 - gamma) * Phi^(-1)(1 - 1/N) + gamma * Phi^(-1)(1 - 1/(N * e)) )
    其中 gamma 为欧拉常数。

    Args:
        n_trials: 策略或参数测试总试验次数 (N >= 1)
        var_sharpe_ann: N 次试验的年化夏普比率方差
        freq: 年交易频率

    Returns:
        期望最大虚假年化夏普比率
    """
    if n_trials <= 1:
        return 0.0

    var_sharpe_ann = max(1e-6, var_sharpe_ann)
    std_sharpe_ann = np.sqrt(var_sharpe_ann)

    # 极值分布渐近展开分位数
    p1 = max(1e-12, min(1.0 - 1e-12, 1.0 - 1.0 / n_trials))
    p2 = max(1e-12, min(1.0 - 1e-12, 1.0 - 1.0 / (n_trials * math.e)))

    z1 = norm_ppf(p1)
    z2 = norm_ppf(p2)

    expected_max = std_sharpe_ann * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)
    return float(expected_max)


def calc_deflated_sharpe_ratio(
    returns: Union[List[float], np.ndarray],
    n_trials: int = 100,
    var_sharpe_ann: Optional[float] = None,
    trial_sharpes_ann: Optional[List[float]] = None,
    freq: int = 250
) -> float:
    """
    降维偏误夏普比率 (Deflated Sharpe Ratio, DSR) —— Bailey & López de Prado (2014)
    修正以下三大回测陷阱：
    1. 非正态收益分布 (偏度、峰度)
    2. 样本历史过短 (有限 T)
    3. 多重测试与数据挖掘陷阱 (在参数网格优化或多个策略候选池中挑选最佳，引发虚假显著)

    核心原理：
    将基准夏普比率从 0.0 提升至经过 N 次试验下的最大期望夏普比率 E[max_n SR_n]，再计算 PSR。

    Args:
        returns: 最佳被测策略的日收益率序列
        n_trials: 参数网格优化或策略候选集试验总数 (如 Optuna 运行了 100 次 trial，则为 100)
        var_sharpe_ann: 所有试验夏普比率的方差。若提供 trial_sharpes_ann 则自动估算，默认保守取 0.25 (即标准差 0.5)
        trial_sharpes_ann: 所有优化试运行策略的年化夏普比率列表
        freq: 每年交易周期数

    Returns:
        DSR 置信概率 [0.0, 1.0]。若 DSR >= 0.95，说明该策略在经历高强度数据挖掘后依然具有统计显著性。
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 5:
        return 0.5

    # 确定试验夏普比率的方差
    if trial_sharpes_ann is not None and len(trial_sharpes_ann) > 1:
        v_ann = float(np.var(trial_sharpes_ann, ddof=1))
        n_eff = len(trial_sharpes_ann)
    else:
        v_ann = var_sharpe_ann if var_sharpe_ann is not None else 0.25
        n_eff = max(1, n_trials)

    # 计算期望虚假最大夏普比率作为基准门槛
    sr_star_ann = calc_expected_max_sharpe(n_trials=n_eff, var_sharpe_ann=v_ann, freq=freq)

    # 以此门槛计算概率夏普比率
    dsr = calc_probabilistic_sharpe_ratio(r, benchmark_sr_ann=sr_star_ann, freq=freq)
    return dsr


class StrategySignificanceAuditor:
    """
    策略统计显著性与抗过拟合审计器
    集成 PSR、DSR、Sortino、Calmar 及高阶矩，生成标准化量化审计诊断报告。
    """

    @classmethod
    def audit_returns(
        cls,
        returns: Union[List[float], np.ndarray],
        n_trials: int = 50,
        benchmark_ann_sr: float = 0.0,
        freq: int = 250
    ) -> Dict[str, Union[float, bool, str]]:
        """
        全要素统计显著性审计
        """
        r = np.asarray(returns, dtype=np.float64)
        if len(r) < 5:
            return {
                "valid": False,
                "reason": "样本数据长度不足5期",
                "sample_length": len(r)
            }

        mean, std, skew, kurt = calc_higher_moments(r)
        sharpe = calc_sharpe_ratio(r, freq=freq)
        sortino = calc_sortino_ratio(r, freq=freq)
        psr = calc_probabilistic_sharpe_ratio(r, benchmark_sr_ann=benchmark_ann_sr, freq=freq)
        dsr = calc_deflated_sharpe_ratio(r, n_trials=n_trials, freq=freq)

        # 最小回测样本长度要求 (MinTR): 在给定夏普比率与高阶矩下，达到 95% 置信度所需的最小样本天数
        if sharpe > 0:
            sr_daily = mean / std
            var_term = 1.0 - skew * sr_daily + ((kurt - 1.0) / 4.0) * (sr_daily ** 2)
            z_95 = 1.644853  # 95% 单侧标准正态分位数
            min_tr = 1 + int(var_term * (z_95 / sr_daily) ** 2)
        else:
            min_tr = 999999

        is_significant = bool(dsr >= 0.95)
        warning_overfitting = bool(sharpe > 1.5 and dsr < 0.80)

        diagnosis = "优秀且稳健"
        if warning_overfitting:
            diagnosis = "严重警惕：高夏普但DSR不达标，存在严重多重测试过拟合与数据窥探偏误！"
        elif not is_significant and psr < 0.90:
            diagnosis = "统计不显著：样本收益可能仅为随机市场噪声"

        return {
            "valid": True,
            "sample_length": len(r),
            "annualized_sharpe": round(sharpe, 4),
            "annualized_sortino": round(sortino, 4),
            "skewness": round(skew, 4),
            "kurtosis": round(kurt, 4),
            "psr_confidence": round(psr, 4),
            "dsr_confidence": round(dsr, 4),
            "trials_discounted": n_trials,
            "min_track_record_length": min_tr,
            "is_statistically_significant": is_significant,
            "overfitting_warning": warning_overfitting,
            "diagnosis": diagnosis
        }

    @classmethod
    def audit_equity_curve(
        cls,
        equity_curve: List[Dict],
        n_trials: int = 50,
        freq: int = 250
    ) -> Dict:
        """
        直接从净值曲线 [{date: ..., equity: ...}, ...] 进行显著性审计
        """
        if len(equity_curve) < 2:
            return {"valid": False, "reason": "净值曲线数据点不足"}

        returns = []
        for j in range(1, len(equity_curve)):
            prev_eq = float(equity_curve[j-1].get("equity", 0))
            cur_eq = float(equity_curve[j].get("equity", 0))
            if prev_eq > 0:
                returns.append((cur_eq - prev_eq) / prev_eq)

        return cls.audit_returns(returns, n_trials=n_trials, freq=freq)


# 兼容别名导出
probabilistic_sharpe_ratio = calc_probabilistic_sharpe_ratio
deflated_sharpe_ratio = calc_deflated_sharpe_ratio

def calc_distribution_moments(returns):
    m, s, sk, ku = calc_higher_moments(np.asarray(returns, dtype=float))
    return {'mean': m, 'std': s, 'skewness': sk, 'kurtosis': ku}

def norm_cdf(x):
    if _HAS_SCIPY and stats is not None:
        return float(stats.norm.cdf(x))
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_ppf(p):
    if _HAS_SCIPY and stats is not None:
        return float(stats.norm.ppf(p))
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    if abs(p - 0.5) < 1e-12:
        return 0.0
    x = 2.0 * p - 1.0
    sgn = 1.0 if x > 0 else -1.0
    x_abs = abs(x)
    a = 0.147
    log_term = math.log(1.0 - x_abs ** 2)
    part1 = (2.0 / (math.pi * a)) + (log_term / 2.0)
    part2 = log_term / a
    inner = math.sqrt(part1 ** 2 - part2) - part1
    erf_inv = sgn * math.sqrt(max(inner, 0.0))
    return math.sqrt(2.0) * erf_inv
