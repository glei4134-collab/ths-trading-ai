"""
蓝图路由聚合导出
"""

from api.routes.account import account_bp
from api.routes.trade import trade_bp
from api.routes.market import market_bp
from api.routes.scheduler import scheduler_bp
from api.routes.system import system_bp

__all__ = [
    'account_bp',
    'trade_bp',
    'market_bp',
    'scheduler_bp',
    'system_bp'
]
