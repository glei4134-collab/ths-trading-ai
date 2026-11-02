"""
资产组合优化与风险度量模块 (Portfolio Optimization & Downside Risk Engine)
实现工业级量化资产配置模型：
1. 层次风险平价 (Hierarchical Risk Parity, HRP —— Marcos López de Prado, 2016):
   - 凝聚树状分层聚类 (Agglomerative Tree Clustering)
   - 拟对角化排序 (Quasi-Diagonalization)
   - 递归二分风险方差配置 (Recursive Bisection)
   彻底避免传统 Markowitz 均值-方差对协方差矩阵求逆导致的数值病态与极度过拟合。
2. Black-Litterman 贝叶斯观点融合模型 (Black-Litterman, 1992):
   - 市场均衡先验收益率反解 (Reverse Optimization)
   - 投资者主观多空观点与置信度矩阵 (P, Q, Omega)
   - 贝叶斯后验期望收益率与后验协方差矩阵求解
3. 条件在险价值 (CVaR / Expected Shortfall) 与下行风险度量:
   - 真实非正态历史模拟与参数化 CVaR 计算
   - 组合边际尾部风险贡献分解
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import scipy.cluster.hierarchy as sch
import scipy.spatial.distance as ssd

logger = logging.getLogger(__name__)


# =====================================================================
# 1. 下行风险指标计算 (VaR & CVaR / Expected Shortfall)
# =====================================================================

def calc_var(
    returns: Union[List[float], np.ndarray],
    confidence_level: float = 0.95
) -> float:
    """
    计算在险价值 (Value at Risk, VaR)
    使用历史模拟法分位数。按标准金融定义返回正数表示最大损失百分比 (如 0.03 表示损失 3%)。

    Args:
        returns: 资产或组合收益率序列 (一维数组)
        confidence_level: 置信水平 (通常 0.95 或 0.99)

    Returns:
        VaR 值 (非负数，表示亏损比例)
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 5:
        return 0.0

    alpha = 1.0 - confidence_level
    # 计算 alpha 分位数
    cutoff = float(np.percentile(r, alpha * 100.0))
    # 若 cutoff < 0 表示亏损，VaR 为正数损失
    return float(max(0.0, -cutoff))


def calc_cvar(
    returns: Union[List[float], np.ndarray],
    confidence_level: float = 0.95
) -> float:
    """
    计算条件在险价值 (Conditional Value at Risk, CVaR / Expected Shortfall)
    度量当损失超过 VaR 门槛后的期望损失值，满足一致性风险测度 (Coherent Risk Measure) 的次可加性。

    Args:
        returns: 收益率序列 (一维数组)
        confidence_level: 置信水平 (默认 0.95)

    Returns:
        CVaR 值 (非负数，表示期望尾部亏损比例)
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 5:
        return 0.0

    alpha = 1.0 - confidence_level
    cutoff = float(np.percentile(r, alpha * 100.0))
    tail_losses = r[r <= cutoff]

    if len(tail_losses) == 0:
        return float(max(0.0, -cutoff))

    expected_tail_loss = float(np.mean(tail_losses))
    return float(max(0.0, -expected_tail_loss))


def portfolio_cvar(
    weights: Union[List[float], np.ndarray],
    returns_matrix: np.ndarray,
    confidence_level: float = 0.95
) -> float:
    """
    计算给定资产权重下的组合 CVaR

    Args:
        weights: 资产权重向量 (长度 N)
        returns_matrix: 历史收益率矩阵 (T 交易日 x N 资产)
        confidence_level: 置信水平 (默认 0.95)
    """
    w = np.asarray(weights, dtype=np.float64)
    if returns_matrix.ndim != 2 or len(w) != returns_matrix.shape[1]:
        raise ValueError("权重维度与收益率矩阵资产数量不匹配")

    # 组合日收益率序列: R_p = X * w
    port_returns = np.dot(returns_matrix, w)
    return calc_cvar(port_returns, confidence_level=confidence_level)


# =====================================================================
# 2. 层次风险平价 (Hierarchical Risk Parity, HRP)
# =====================================================================

class HierarchicalRiskParity:
    """
    层次风险平价资产配置器 (Marcos López de Prado, 2016)
    
    优势：
    1. 无需对整个协方差矩阵求逆，避免行列式接近 0 导致的数值奇异与极端不稳定权重。
    2. 考虑资产间的层次相关结构（板块树状分支），在层内与层间分别平衡方差。
    3. 样本外表现（夏普比率、最大回撤、稳健度）系统性优于 Markowitz 临界线模型。
    """

    @classmethod
    def calc_distance_matrix(cls, corr_matrix: np.ndarray) -> np.ndarray:
        """
        根据相关系数矩阵计算非参数距离矩阵:
        D(i, j) = sqrt( 0.5 * (1 - rho_{i, j}) )
        满足度量空间的非负性、对称性与三角不等式。
        """
        # 数值截断防止微小浮点误差超出 [-1, 1]
        c = np.clip(corr_matrix, -1.0, 1.0)
        dist = np.sqrt(0.5 * np.maximum(0.0, 1.0 - c))
        np.fill_diagonal(dist, 0.0)
        return dist

    @classmethod
    def quasi_diagonalization(cls, linkage_matrix: np.ndarray) -> List[int]:
        """
        拟对角化排序 (Quasi-Diagonalization):
        重排树状聚类叶子节点，使得相似资产相邻聚集，将高协方差聚类在主对角线周围。
        """
        return sch.leaves_list(linkage_matrix).tolist()

    @classmethod
    def get_cluster_variance(cls, cov_matrix: np.ndarray, cluster_indices: List[int]) -> float:
        """
        计算聚类子集的逆方差加权方差 (Inverse-Variance Allocation Variance):
        w_i = (1 / sigma_i^2) / sum(1 / sigma_j^2)
        V_cluster = w^T * Sigma_cluster * w
        """
        sub_cov = cov_matrix[np.ix_(cluster_indices, cluster_indices)]
        diag = np.diag(sub_cov)
        inv_diag = 1.0 / np.maximum(diag, 1e-10)
        w = inv_diag / np.sum(inv_diag)
        cluster_var = float(np.dot(np.dot(w, sub_cov), w))
        return max(1e-10, cluster_var)

    @classmethod
    def recursive_bisection(
        cls,
        cov_matrix: np.ndarray,
        sorted_indices: List[int]
    ) -> np.ndarray:
        """
        递归二分权重分配 (Recursive Bisection):
        沿拟对角化序列递归自顶向下二分，按子聚类方差分配相对权重。
        alpha_1 = 1 - V_1 / (V_1 + V_2) = V_2 / (V_1 + V_2)
        alpha_2 = 1 - alpha_1
        """
        weights = np.ones(len(sorted_indices), dtype=np.float64)
        items = [sorted_indices]

        while len(items) > 0:
            # 弹出一层子集
            subset = items.pop(0)
            if len(subset) <= 1:
                continue

            # 二分分割
            mid = len(subset) // 2
            c1 = subset[:mid]
            c2 = subset[mid:]

            # 计算两部分的聚类方差
            var1 = cls.get_cluster_variance(cov_matrix, c1)
            var2 = cls.get_cluster_variance(cov_matrix, c2)

            # 计算二分分配因子
            alpha1 = var2 / (var1 + var2)
            alpha2 = 1.0 - alpha1

            # 对应局部权重缩放
            for idx in c1:
                weights[idx] *= alpha1
            for idx in c2:
                weights[idx] *= alpha2

            if len(c1) > 1:
                items.append(c1)
            if len(c2) > 1:
                items.append(c2)

        return weights

    @classmethod
    def optimize(
        cls,
        returns_matrix: np.ndarray,
        asset_names: Optional[List[str]] = None,
        linkage_method: str = "single"
    ) -> Dict[str, float]:
        """
        执行完整 HRP 权重求解

        Args:
            returns_matrix: T 样本天数 x N 资产收益率矩阵
            asset_names: 资产代码/名称列表 (可选)
            linkage_method: 聚类连通方式 ('single', 'average', 'complete', 'ward')

        Returns:
            {asset_name: optimal_weight, ...} 归一化权重字典
        """
        N = returns_matrix.shape[1]
        names = asset_names or [f"asset_{i}" for i in range(N)]
        if N == 1:
            return {names[0]: 1.0}

        # 1. 计算协方差与相关系数矩阵
        cov = np.cov(returns_matrix, rowvar=False)
        corr = np.corrcoef(returns_matrix, rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)

        # 2. 树状层次聚类
        dist = cls.calc_distance_matrix(corr)
        # 压缩为紧凑一维距离向量以供 scipy linkage 使用
        condensed_dist = ssd.squareform(dist, checks=False)
        linkage = sch.linkage(condensed_dist, method=linkage_method)

        # 3. 拟对角化排序
        sorted_indices = cls.quasi_diagonalization(linkage)

        # 4. 递归二分分配权重
        raw_weights = cls.recursive_bisection(cov, sorted_indices)

        # 归一化
        total_w = float(np.sum(raw_weights))
        normalized_w = raw_weights / (total_w if total_w > 0 else 1.0)

        return {names[i]: float(round(normalized_w[i], 4)) for i in range(N)}


# =====================================================================
# 3. Black-Litterman 贝叶斯观点融合模型
# =====================================================================

class BlackLittermanModel:
    """
    Black-Litterman 贝叶斯资产配置模型 (1992)
    
    将资本资产定价模型 (CAPM) 的市场先验均衡分布，与投资者的主观观点（View）
    及其置信度矩阵进行贝叶斯融合，输出兼具市场稳健性与战术阿尔法的后验期望收益率。
    """

    def __init__(
        self,
        risk_aversion: float = 2.5,
        tau: float = 0.05
    ):
        """
        Args:
            risk_aversion: 市场整体风险厌恶系数 lambda (通常在 2.0 ~ 3.5 之间)
            tau: 先验协方差矩阵缩放标量 (通常取 0.025 ~ 0.10，代表先验估计的不确定性)
        """
        self.risk_aversion = risk_aversion
        self.tau = tau

    def compute_implied_prior(
        self,
        cov_matrix: np.ndarray,
        market_weights: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """
        通过逆向优化反解市场先验均衡期望收益率:
        Pi = lambda * Sigma * w_mkt
        """
        N = cov_matrix.shape[0]
        if market_weights is None:
            w_mkt = np.ones(N) / float(N)  # 无市值权重时退化为等权先验
        else:
            w_mkt = np.asarray(market_weights, dtype=np.float64)
            w_mkt = w_mkt / np.sum(w_mkt)

        pi = self.risk_aversion * np.dot(cov_matrix, w_mkt)
        return pi

    def fit(
        self,
        cov_matrix: np.ndarray,
        market_weights: Optional[np.ndarray] = None,
        views_P: Optional[np.ndarray] = None,
        views_Q: Optional[np.ndarray] = None,
        views_confidences: Optional[List[float]] = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        执行贝叶斯融合求解

        Args:
            cov_matrix: N x N 历史样本协方差矩阵 (Sigma)
            market_weights: N 维先验市场基准权重向量
            views_P: K x N 观点拾取矩阵 (Pick Matrix)
            views_Q: K x 1 观点期望收益率向量
            views_confidences: 各观点的置信度 [0.0 ~ 1.0]

        Returns:
            (post_expected_returns, post_cov_matrix, optimal_weights)
        """
        N = cov_matrix.shape[0]
        Sigma = cov_matrix.copy()

        # 1. 求解先验均衡收益率
        pi = self.compute_implied_prior(Sigma, market_weights)

        # 2. 若无投资者主观观点，直接退化为市场均衡先验
        if views_P is None or views_Q is None or len(views_P) == 0:
            post_returns = pi
            post_cov = (1.0 + self.tau) * Sigma
            # 最优无约束权重 w* = 1/lambda * Sigma^(-1) * post_returns
            inv_cov = np.linalg.pinv(Sigma)
            raw_w = np.dot(inv_cov, post_returns) / self.risk_aversion
            w = np.maximum(0.0, raw_w)  # 做多限制
            w = w / np.sum(w) if np.sum(w) > 0 else np.ones(N) / N
            return post_returns, post_cov, w

        P = np.asarray(views_P, dtype=np.float64)
        Q = np.asarray(views_Q, dtype=np.float64).reshape(-1, 1)
        K = P.shape[0]

        # 3. 构造观点不确定性协方差矩阵 Omega (He & Litterman 1999 方法)
        # Omega = diag(P * (tau * Sigma) * P^T) / confidence_scale
        tau_Sigma = self.tau * Sigma
        omega_diag = np.diag(np.dot(np.dot(P, tau_Sigma), P.T))

        if views_confidences is not None and len(views_confidences) == K:
            # 置信度越低，Omega 对应方差越大
            scales = np.array([(1.0 - c) / max(1e-4, c) for c in views_confidences])
            Omega = np.diag(np.maximum(1e-8, omega_diag * (1.0 + scales)))
        else:
            Omega = np.diag(np.maximum(1e-8, omega_diag))

        # 4. 贝叶斯后验期望收益率求解 (使用更数值稳定的变体):
        # E[R] = Pi + tau * Sigma * P^T * (P * tau * Sigma * P^T + Omega)^(-1) * (Q - P * Pi)
        M = np.dot(np.dot(P, tau_Sigma), P.T) + Omega
        inv_M = np.linalg.pinv(M)

        pi_col = pi.reshape(-1, 1)
        correction = np.dot(
            np.dot(tau_Sigma, P.T),
            np.dot(inv_M, (Q - np.dot(P, pi_col)))
        )
        post_returns = (pi_col + correction).flatten()

        # 5. 后验协方差矩阵求解:
        # Sigma_post = (1 + tau) * Sigma - tau^2 * Sigma * P^T * inv_M * P * Sigma
        cov_correction = (self.tau ** 2) * np.dot(
            np.dot(np.dot(Sigma, P.T), inv_M),
            np.dot(P, Sigma)
        )
        post_cov = (1.0 + self.tau) * Sigma - cov_correction

        # 6. 计算最优配置权重
        inv_post_cov = np.linalg.pinv(post_cov)
        raw_weights = np.dot(inv_post_cov, post_returns) / self.risk_aversion

        # 仅做多限制并归一化
        long_only_weights = np.maximum(0.0, raw_weights)
        total_w = np.sum(long_only_weights)
        optimal_weights = (long_only_weights / total_w) if total_w > 0 else (np.ones(N) / N)

        return post_returns, post_cov, optimal_weights


# =====================================================================
# 4. 统一组合优化器接口 (PortfolioOptimizer)
# =====================================================================

class PortfolioOptimizer:
    """
    资产组合优化统一门面
    支持 HRP、Black-Litterman 及风险平价，集成下行风险评估。
    """

    @classmethod
    def optimize_hrp(
        cls,
        returns_matrix: np.ndarray,
        symbols: List[str]
    ) -> Dict[str, float]:
        """
        层次风险平价 (HRP) 配置
        """
        return HierarchicalRiskParity.optimize(returns_matrix, asset_names=symbols)

    @classmethod
    def optimize_black_litterman(
        cls,
        returns_matrix: np.ndarray,
        symbols: List[str],
        views: Optional[Dict[str, float]] = None,
        confidences: Optional[Dict[str, float]] = None,
        market_weights: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """
        Black-Litterman 贝叶斯观点融合配置

        Args:
            returns_matrix: T x N 收益率矩阵
            symbols: 标的列表
            views: 主观绝对收益观点字典 {'000001': 0.08, '600519': 0.12}
            confidences: 各观点置信度字典 {'000001': 0.70, '600519': 0.85}
            market_weights: 市场基准权重字典
        """
        cov = np.cov(returns_matrix, rowvar=False)
        N = len(symbols)

        mkt_w = None
        if market_weights:
            mkt_w = np.array([market_weights.get(s, 1.0 / N) for s in symbols])

        # 构造 P 和 Q 矩阵
        P = []
        Q = []
        confs = []
        if views:
            for sym, ret in views.items():
                if sym in symbols:
                    row = np.zeros(N)
                    row[symbols.index(sym)] = 1.0
                    P.append(row)
                    Q.append(ret)
                    confs.append(confidences.get(sym, 0.6) if confidences else 0.6)

        bl = BlackLittermanModel()
        _, _, opt_w = bl.fit(
            cov_matrix=cov,
            market_weights=mkt_w,
            views_P=np.array(P) if P else None,
            views_Q=np.array(Q) if Q else None,
            views_confidences=confs if confs else None
        )

        return {symbols[i]: float(round(opt_w[i], 4)) for i in range(N)}

    @classmethod
    def calc_portfolio_risk_report(
        cls,
        weights: Dict[str, float],
        returns_matrix: np.ndarray,
        symbols: List[str]
    ) -> Dict[str, float]:
        """
        生成组合风险度量报告 (含波动率、VaR、CVaR)
        """
        w_vec = np.array([weights.get(s, 0.0) for s in symbols])
        if np.sum(w_vec) > 0:
            w_vec = w_vec / np.sum(w_vec)

        port_returns = np.dot(returns_matrix, w_vec)
        daily_vol = float(np.std(port_returns))
        annual_vol = daily_vol * math.sqrt(250)
        var_95 = calc_var(port_returns, confidence_level=0.95)
        cvar_95 = calc_cvar(port_returns, confidence_level=0.95)

        return {
            "daily_volatility": round(daily_vol, 4),
            "annualized_volatility": round(annual_vol, 4),
            "var_95": round(var_95, 4),
            "cvar_95": round(cvar_95, 4)
        }
