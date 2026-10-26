"""
策略注册表
自动收集所有 BaseStrategy 子类，提供策略目录和实例化
"""

import json
import sys
import logging
from typing import Dict, List, Optional, Type
from pathlib import Path

logger = logging.getLogger(__name__)

# 自定义策略目录
from paths import CONFIG_DIR, DATA_DIR
CUSTOM_STRATEGIES_DIR = CONFIG_DIR / "custom_strategies"


class StrategyRegistry:
    """策略注册表"""

    def __init__(self):
        self._strategies: Dict[str, Type] = {}  # name -> class
        self._instances: Dict[str, object] = {}  # name -> instance

    def register(self, strategy_class: Type):
        """注册策略类"""
        instance = strategy_class()
        name = instance.name
        if name in self._strategies:
            logger.warning(f"策略 {name} 已存在，将被覆盖")
        self._strategies[name] = strategy_class
        self._instances[name] = instance
        logger.debug(f"注册策略: {name} ({instance.category})")

    def get(self, name: str):
        """获取策略实例"""
        return self._instances.get(name)

    def get_all(self) -> Dict[str, object]:
        """获取所有策略实例"""
        return self._instances.copy()

    def list_names(self) -> List[str]:
        """列出所有策略名称"""
        return list(self._strategies.keys())

    def list_by_category(self, category: str) -> List[str]:
        """按类别列出策略"""
        return [
            name for name, instance in self._instances.items()
            if instance.category == category
        ]

    def list_by_risk(self, risk_level: str) -> List[str]:
        """按风险等级列出策略"""
        return [
            name for name, instance in self._instances.items()
            if instance.risk_level == risk_level
        ]

    def get_categories(self) -> Dict[str, List[str]]:
        """获取所有类别及策略列表"""
        categories = {}
        for name, instance in self._instances.items():
            cat = instance.category
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(name)
        return categories

    def get_catalog(self) -> str:
        """获取完整策略目录（给AI读的文本）"""
        categories = self.get_categories()
        category_names = {
            "trend": "趋势跟踪",
            "momentum": "动量策略",
            "reversal": "反转策略",
            "volatility": "波动率策略",
            "volume": "量能策略",
            "pattern": "形态策略",
            "composite": "综合策略",
            "shortterm": "超短线策略(1-5天)",
            "competition": "比赛制胜策略",
        }

        lines = ["=" * 60, "可用策略目录", "=" * 60, ""]

        for cat, names in categories.items():
            cat_display = category_names.get(cat, cat)
            lines.append(f"## {cat_display} ({cat})")
            lines.append("-" * 40)
            for name in names:
                instance = self._instances[name]
                lines.append(f"  [{name}] {instance.description}")
                lines.append(f"    风险等级: {instance.risk_level}")
            lines.append("")

        lines.append("=" * 60)
        lines.append(f"共 {len(self._strategies)} 个策略可用")
        lines.append("使用方式: 调用 registry.get('策略名称').analyze(stock_data, indicators)")
        lines.append("=" * 60)

        return "\n".join(lines)

    def get_catalog_json(self) -> List[Dict]:
        """获取策略目录（JSON格式，给API用）"""
        result = []
        for name, instance in self._instances.items():
            result.append({
                "name": name,
                "description": instance.description,
                "category": instance.category,
                "risk_level": instance.risk_level,
                "params": instance.params
            })
        return result

    def get_strategy_doc(self, name: str) -> str:
        """获取单个策略的接口文档"""
        instance = self._instances.get(name)
        if instance:
            return instance.get_interface_doc()
        return f"策略 {name} 不存在"

    def load_custom_strategies(self):
        """加载用户自定义策略"""
        CUSTOM_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        for f in CUSTOM_STRATEGIES_DIR.glob("*.py"):
            if f.name.startswith("_"):
                continue
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(f.stem, f)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "STRATEGY_CLASS"):
                    self.register(module.STRATEGY_CLASS)
                    logger.info(f"加载自定义策略: {f.name}")
            except Exception as e:
                logger.error(f"加载自定义策略 {f.name} 失败: {e}")

    def save_custom_strategy(self, name: str, code: str):
        """保存自定义策略代码"""
        CUSTOM_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        filepath = CUSTOM_STRATEGIES_DIR / f"{name}.py"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        logger.info(f"保存自定义策略: {filepath}")

    def reload(self):
        """重新加载所有策略"""
        self._strategies.clear()
        self._instances.clear()
        # 触发重新导入
        import importlib
        from . import strategies as pkg
        importlib.reload(pkg)


# 全局策略注册表
registry = StrategyRegistry()


def _auto_register():
    """自动注册所有 BaseStrategy 子类"""
    import inspect

    # 确保 strategies 包已导入
    try:
        from strategies.base import BaseStrategy
        from strategies import trend_strategies, momentum_strategies, reversal_strategies
        from strategies import volatility_strategies, volume_strategies, pattern_strategies
        from strategies import composite_strategies, shortterm_strategies
        from strategies import competition_strategies
    except ImportError:
        # 如果导入失败，添加路径后重试
        src_dir = str(Path(__file__).parent)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from strategies.base import BaseStrategy
        from strategies import trend_strategies, momentum_strategies, reversal_strategies
        from strategies import volatility_strategies, volume_strategies, pattern_strategies
        from strategies import composite_strategies, shortterm_strategies
        from strategies import competition_strategies

    # 扫描所有策略模块中的 BaseStrategy 子类
    modules = [trend_strategies, momentum_strategies, reversal_strategies,
               volatility_strategies, volume_strategies, pattern_strategies,
               composite_strategies, shortterm_strategies, competition_strategies]

    for mod in modules:
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                inspect.isclass(attr)
                and issubclass(attr, BaseStrategy)
                and attr is not BaseStrategy
                and hasattr(attr, "name")
                and attr.name != "base"
            ):
                registry.register(attr)

    # 加载自定义策略
    registry.load_custom_strategies()


_auto_register()
