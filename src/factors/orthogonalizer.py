"""
因子挖掘与正交中性化模块 (Alpha Factor Orthogonalization)
实现对称正交化 (Lowödin Orthogonalization)、Gram-Schmidt正交化以及Rank IC半衰期衰减分析。
用于彻底消除动量、反转、流动性、波动率等多因子间的多重共线性。
"""

import math
import logging
from typing import Dict, List, Tuple, Optional, Union
import numpy as np

logger = logging.getLogger(__name__)


class LowodinOrthogonalizer:
    """
    Lowödin 对称正交化器 (Symmetric Orthogonalization)
    
    数学原理：
    设因子截面矩阵为 F (N samples x K factors)，各因子均值已中心化且方差标准化。
    重叠重合矩阵 M = F^T * F / N 为 K x K 的对称半正定相关性矩阵。
    进行谱分解 (Spectral Decomposition): M = V * Lambda * V^T
    构造变换矩阵 T = V * Lambda^(-1/2) * V^T
    正交化后的因子矩阵: F_tilde = F * T
    
    性质：
    1. F_tilde^T * F_tilde / N = I (严格两两正交，相关系数为0)
    2. 在所有满足正交条件的矩阵中，F_tilde 与原始因子 F 的弗罗贝尼乌斯范数距离最小:
       argmin || F_tilde - F ||_F
    3. 各因子对待完全平等，不依赖顺序假设，最大程度保留因子的原始经济学物理含义。
    """

    def __init__(self, eps: float = 1e-8):
        self.eps = eps
        self.transform_matrix: Optional[np.ndarray] = None
        self.factor_names: List[str] = []

    def fit_transform(
        self,
        factor_matrix: np.ndarray,
        factor_names: Optional[List[str]] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        输入:
            factor_matrix: N 样本 x K 因子 (numpy ndarray)
            factor_names: 因子名称列表
        返回:
            (orthogonal_factors, transform_matrix)
        """
        N, K = factor_matrix.shape
        if K < 2:
            return factor_matrix.copy(), np.eye(K)

        self.factor_names = factor_names or [f"factor_{i}" for i in range(K)]

        # 1. 截面标准化 (Z-score 处理，按列中心化与单位方差化)
        means = np.nanmean(factor_matrix, axis=0)
        stds = np.nanstd(factor_matrix, axis=0)
        stds = np.where(stds < self.eps, 1.0, stds)
        
        # 填充缺失值并标准化
        F = np.where(np.isnan(factor_matrix), means, factor_matrix)
        F = (F - means) / stds

        # 2. 计算经验协方差/相关性矩阵 M = (F^T * F) / N
        M = np.dot(F.T, F) / float(N)

        # 保证 M 对称性防微小浮点误差
        M = 0.5 * (M + M.T)

        # 3. 特征值分解 (使用 eigh 处理实对称矩阵)
        eigenvalues, eigenvectors = np.linalg.eigh(M)

        # 数值截断：防止由于多重共线性导致的极小或负特征值
        eigenvalues = np.maximum(eigenvalues, self.eps)

        # 4. 构造对称正交变换矩阵 T = V * diag(Lambda^(-1/2)) * V^T
        diag_inv_sqrt = np.diag(1.0 / np.sqrt(eigenvalues))
        T = np.dot(np.dot(eigenvectors, diag_inv_sqrt), eigenvectors.T)

        self.transform_matrix = T

        # 5. 投影到对称正交空间
        F_tilde = np.dot(F, T)

        # 验证正交性 (仅用于debug/日志)
        corr_check = np.dot(F_tilde.T, F_tilde) / float(N)
        max_off_diag = np.max(np.abs(corr_check - np.eye(K)))
        if max_off_diag > 1e-4:
            logger.warning(f"Lowodin 正交化后最大非对角线相关性残差偏高: {max_off_diag:.6f}")

        return F_tilde, T

    def transform_dict(
        self,
        data_records: List[Dict[str, float]],
        keys: List[str]
    ) -> List[Dict[str, float]]:
        """
        便捷接口：接收 [{'f1': 1.2, 'f2': 0.5}, ...] 字典列表并输出正交化后的新字典列表
        """
        if not data_records or not keys:
            return data_records

        matrix = np.array([[row.get(k, 0.0) for k in keys] for row in data_records], dtype=float)
        f_tilde, _ = self.fit_transform(matrix, keys)

        result = []
        for i, row in enumerate(data_records):
            new_row = dict(row)
            for j, k in enumerate(keys):
                new_row[f"{k}_ortho"] = float(f_tilde[i, j])
            result.append(new_row)
        return result


class GramSchmidtOrthogonalizer:
    """
    Gram-Schmidt 顺序正交化器
    依序对指定列表因子进行残差投影，第 i 个因子被投影到前 i-1 个因子的正交补空间中。
    适用于有明确经济学优先级假定的场景（例如：首先保留动量，其余因子仅保留对动量残差）。
    """

    def fit_transform(
        self,
        factor_matrix: np.ndarray,
        eps: float = 1e-8
    ) -> np.ndarray:
        N, K = factor_matrix.shape
        Q = np.zeros_like(factor_matrix, dtype=float)

        # 标准化
        means = np.nanmean(factor_matrix, axis=0)
        stds = np.nanstd(factor_matrix, axis=0)
        stds = np.where(stds < eps, 1.0, stds)
        F = (factor_matrix - means) / stds

        for j in range(K):
            v = F[:, j].copy()
            for i in range(j):
                q_i = Q[:, i]
                denom = np.dot(q_i, q_i)
                if denom > eps:
                    proj = (np.dot(v, q_i) / denom) * q_i
                    v -= proj
            norm = np.linalg.norm(v)
            if norm > eps:
                Q[:, j] = v / (norm / np.sqrt(N))  # 保持方差约为1
            else:
                Q[:, j] = np.zeros(N)

        return Q


def calculate_rank_ic(
    factor_values: Union[List[float], np.ndarray, Dict[str, float]],
    forward_returns: Union[List[float], np.ndarray, Dict[str, float]]
) -> float:
    """
    计算截面 Rank IC (Spearman Rank Correlation)
    """
    if isinstance(factor_values, dict) and isinstance(forward_returns, dict):
        common_keys = [k for k in factor_values if k in forward_returns]
        if len(common_keys) < 3:
            return 0.0
        f_arr = np.array([factor_values[k] for k in common_keys], dtype=float)
        r_arr = np.array([forward_returns[k] for k in common_keys], dtype=float)
    else:
        f_arr = np.asarray(factor_values, dtype=float)
        r_arr = np.asarray(forward_returns, dtype=float)

    valid_mask = (~np.isnan(f_arr)) & (~np.isnan(r_arr))
    if np.sum(valid_mask) < 3:
        return 0.0

    f_valid = f_arr[valid_mask]
    r_valid = r_arr[valid_mask]

    # 计算 Rank (使用 argsort 排序)
    f_ranks = np.argsort(np.argsort(f_valid)).astype(float)
    r_ranks = np.argsort(np.argsort(r_valid)).astype(float)

    # Pearson 相关系数计算 Rank IC
    f_diff = f_ranks - np.mean(f_ranks)
    r_diff = r_ranks - np.mean(r_ranks)
    
    denom = np.sqrt(np.sum(f_diff**2) * np.sum(r_diff**2))
    if denom <= 1e-10:
        return 0.0
    return float(np.sum(f_diff * r_diff) / denom)


def factor_ic_decay(
    factor_history: List[Dict[str, float]],
    forward_returns_history: List[Dict[str, float]],
    max_lags: int = 10
) -> Dict[str, Union[float, List[float]]]:
    """
    计算因子 IC 衰减谱与半衰期 (Half-Life Decay)
    输入:
        factor_history: 每日个股因子字典列表
        forward_returns_history: 对应日期的未来多期收益率字典列表
    返回:
        {
            "ic_series": [...],
            "mean_ic": 0.045,
            "ir": 1.25,
            "half_life_days": 3.4
        }
    """
    if len(factor_history) < 5 or len(forward_returns_history) < 5:
        return {"mean_ic": 0.0, "ir": 0.0, "half_life_days": 0.0, "ic_lags": []}

    min_len = min(len(factor_history), len(forward_returns_history))
    ic_lags = []

    # 计算滞后 lag 期的一步 IC
    for lag in range(1, min(max_lags + 1, min_len)):
        day_ics = []
        for t in range(min_len - lag):
            f_t = factor_history[t]
            r_t_plus_lag = forward_returns_history[t + lag]
            ic_val = calculate_rank_ic(f_t, r_t_plus_lag)
            if not np.isnan(ic_val):
                day_ics.append(ic_val)
        if day_ics:
            ic_lags.append(float(np.mean(day_ics)))
        else:
            ic_lags.append(0.0)

    if not ic_lags:
        return {"mean_ic": 0.0, "ir": 0.0, "half_life_days": 0.0, "ic_lags": []}

    mean_ic = float(np.mean(ic_lags))
    std_ic = float(np.std(ic_lags))
    ir = mean_ic / (std_ic + 1e-8)

    # 拟合半衰期: IC(t) = IC_0 * exp(-lambda * t) => ln(IC(t)) = a - lambda * t
    half_life = 0.0
    try:
        abs_ics = [abs(x) for x in ic_lags if abs(x) > 1e-6]
        if len(abs_ics) >= 3 and abs_ics[0] > 0:
            x_vals = np.arange(len(abs_ics))
            y_vals = np.log(abs_ics)
            # 最小二乘线性拟合斜率
            slope, _ = np.polyfit(x_vals, y_vals, 1)
            decay_rate = -slope
            if decay_rate > 0.01:
                half_life = float(np.log(2) / decay_rate)
    except Exception as e:
        logger.debug(f"拟合因子半衰期失败: {e}")

    return {
        "mean_ic": round(mean_ic, 4),
        "ir": round(ir, 4),
        "half_life_days": round(half_life, 2) if half_life > 0 else 0.0,
        "ic_lags": [round(x, 4) for x in ic_lags]
    }
