"""
策略包 - 自动注册所有策略到注册表
"""

from .base import BaseStrategy, StrategyResult, Signal

# 导入所有策略模块（触发自动注册）
from . import trend_strategies
from . import momentum_strategies
from . import reversal_strategies
from . import volatility_strategies
from . import volume_strategies
from . import pattern_strategies
from . import composite_strategies
from . import competition_strategies
from . import team_ensemble

__all__ = ["BaseStrategy", "StrategyResult", "Signal"]
