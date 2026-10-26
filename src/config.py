"""
配置管理模块
管理API密钥、交易参数、策略配置等
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

from paths import CONFIG_DIR
CONFIG_FILE = CONFIG_DIR / "settings.json"


@dataclass
class TradingConfig:
    """交易配置"""
    initial_balance: float = 1000000.0  # 初始资金（100万）
    max_position_ratio: float = 0.1  # 单只股票最大持仓比例
    max_total_position_ratio: float = 0.8  # 最大总仓位比例
    min_trade_amount: int = 100  # 最小交易单位（100股）
    commission_rate: float = 0.0003  # 佣金费率（万三）
    stamp_tax_rate: float = 0.001  # 印花税费率（千一，仅卖出）
    min_commission: float = 5.0  # 最低佣金


@dataclass
class StrategyConfig:
    """策略配置"""
    technical_weight: float = 0.5
    momentum_weight: float = 0.3
    value_weight: float = 0.2
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    ma_short: int = 5
    ma_long: int = 20
    momentum_period: int = 10
    volume_threshold: float = 1.5


@dataclass
class BrokerConfig:
    """券商配置"""
    mode: str = "simulation"  # simulation / real
    exe_path: str = ""  # 同花顺客户端路径
    user: str = ""
    password: str = ""
    auto_trade: bool = False
    max_daily_trades: int = 10


@dataclass
class MCPConfig:
    """MCP Server配置"""
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    log_level: str = "INFO"


@dataclass
class AppConfig:
    """应用总配置"""
    trading: TradingConfig = None
    strategy: StrategyConfig = None
    broker: BrokerConfig = None
    mcp: MCPConfig = None

    def __post_init__(self):
        if self.trading is None:
            self.trading = TradingConfig()
        if self.strategy is None:
            self.strategy = StrategyConfig()
        if self.broker is None:
            self.broker = BrokerConfig()
        if self.mcp is None:
            self.mcp = MCPConfig()


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = Path(config_file) if config_file else CONFIG_FILE
        self.config = AppConfig()
        self.load()

    def load(self) -> AppConfig:
        """加载配置文件"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # 解析各部分配置
                if 'trading' in data:
                    self.config.trading = TradingConfig(**data['trading'])
                if 'strategy' in data:
                    self.config.strategy = StrategyConfig(**data['strategy'])
                if 'broker' in data:
                    self.config.broker = BrokerConfig(**data['broker'])
                if 'mcp' in data:
                    self.config.mcp = MCPConfig(**data['mcp'])

                logger.info(f"配置已加载: {self.config_file}")
            else:
                logger.info("配置文件不存在，使用默认配置")
                self.save()

        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            logger.info("使用默认配置")

        return self.config

    def save(self) -> None:
        """保存配置文件"""
        try:
            # 创建配置目录
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                'trading': asdict(self.config.trading),
                'strategy': asdict(self.config.strategy),
                'broker': asdict(self.config.broker),
                'mcp': asdict(self.config.mcp)
            }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info(f"配置已保存: {self.config_file}")

        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def update(self, section: str, **kwargs) -> None:
        """更新指定部分的配置"""
        if section == 'trading':
            for key, value in kwargs.items():
                if hasattr(self.config.trading, key):
                    setattr(self.config.trading, key, value)
        elif section == 'strategy':
            for key, value in kwargs.items():
                if hasattr(self.config.strategy, key):
                    setattr(self.config.strategy, key, value)
        elif section == 'broker':
            for key, value in kwargs.items():
                if hasattr(self.config.broker, key):
                    setattr(self.config.broker, key, value)
        elif section == 'mcp':
            for key, value in kwargs.items():
                if hasattr(self.config.mcp, key):
                    setattr(self.config.mcp, key, value)
        else:
            raise ValueError(f"未知配置部分: {section}")

        self.save()

    def get(self, section: str) -> Any:
        """获取指定部分的配置"""
        if section == 'trading':
            return self.config.trading
        elif section == 'strategy':
            return self.config.strategy
        elif section == 'broker':
            return self.config.broker
        elif section == 'mcp':
            return self.config.mcp
        else:
            raise ValueError(f"未知配置部分: {section}")


# 全局配置管理器实例
config_manager = ConfigManager()
