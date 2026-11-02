"""
高阶算法交易与最优清算执行库 (Algorithmic Execution & Microstructure Order Slicing)
"""
from .algo_orders import (
    AlmgrenChrissExecutor,
    DynamicResidualVWAP,
    PeggedIcebergPlanner,
    estimate_kyle_lambda_slippage
)

__all__ = [
    "AlmgrenChrissExecutor",
    "DynamicResidualVWAP",
    "PeggedIcebergPlanner",
    "estimate_kyle_lambda_slippage"
]
