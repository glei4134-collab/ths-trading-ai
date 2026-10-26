"""
AI API连接模块
支持多种AI模型进行股票分析和交易建议
"""

import json
import os
import hashlib
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

logger = logging.getLogger(__name__)

# AI配置目录
from paths import CONFIG_DIR
AI_CONFIG_FILE = CONFIG_DIR / "ai_config.json"


@dataclass
class AIConfig:
    """AI配置"""
    provider: str = "openai"  # openai / claude / deepseek / local
    api_key: str = ""
    model: str = "gpt-4"
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 2000


# providers config file
PROVIDERS_FILE = CONFIG_DIR / "ai_providers.json"

# default providers (written to disk on first run)
_DEFAULT_PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini"],
        "default_url": "https://api.openai.com/v1"
    },
    "claude": {
        "name": "Claude (Anthropic)",
        "models": ["claude-opus-4-20250514", "claude-sonnet-4-20250514",
                   "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022"],
        "default_url": "https://api.anthropic.com"
    },
    "deepseek": {
        "name": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-coder", "deepseek-reasoner"],
        "default_url": "https://api.deepseek.com"
    },
    "moonshot": {
        "name": "Moonshot (月之暗面)",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "default_url": "https://api.moonshot.cn/v1"
    },
    "zhipu": {
        "name": "智谱AI (GLM)",
        "models": ["glm-4", "glm-4-flash", "glm-3-turbo"],
        "default_url": "https://open.bigmodel.cn/api/paas/v4"
    },
    "local": {
        "name": "本地模型 (Ollama/LM Studio)",
        "models": ["llama3", "qwen2", "mistral"],
        "default_url": "http://localhost:11434/v1"
    }
}


class AIConnector:
    """
    AI API连接器
    支持多种AI模型进行股票分析
    模型列表从 config/ai_providers.json 读取，可在网页中编辑
    """

    def __init__(self):
        self.config = AIConfig()
        self.connected = False
        self.PROVIDERS = self._load_providers()
        self.load_config()

        # ---- Token 优化: 缓存 + 限速 ----
        self._cache: Dict[str, Dict] = {}      # key → {"data": ..., "ts": float}
        self._cache_ttl: Dict[str, int] = {    # 默认 TTL (秒)
            "analyze_stock": 300,               # 5分钟: 同一只股票的分析
            "analyze_with_strategy": 300,
            "generate_strategy": 3600,          # 1小时: 策略生成
            "explain_indicator": 1800,          # 30分钟: 指标解释
            "chat": 0,                          # 0 = 不缓存用户聊天
        }
        self._last_call_ts: float = 0           # 限速: 上次调用时间
        self._min_interval: float = 1.0         # 最小调用间隔 (秒)
        self._total_tokens: int = 0             # 累计 token 消耗
        self._total_calls: int = 0              # 累计 API 调用次数
        self._cache_hits: int = 0               # 缓存命中次数

    # ---------- Token 优化: 缓存 + 限速 ----------

    @staticmethod
    def _cache_key(func_name: str, *args) -> str:
        """根据函数名 + 参数生成缓存 key"""
        raw = f"{func_name}:{json.dumps(args, ensure_ascii=False, default=str)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _cache_get(self, key: str, func_name: str) -> Optional[Dict]:
        """从缓存获取结果，TTL过期返回None"""
        entry = self._cache.get(key)
        if not entry:
            return None
        ttl = self._cache_ttl.get(func_name, 0)
        if ttl <= 0:
            return None  # 不缓存
        if time.time() - entry["ts"] > ttl:
            del self._cache[key]
            return None
        self._cache_hits += 1
        return entry["data"]

    def _cache_set(self, key: str, data: Dict):
        """写入缓存"""
        self._cache[key] = {"data": data, "ts": time.time()}
        # 限制缓存大小 (最多200条)
        if len(self._cache) > 200:
            oldest_key = min(self._cache, key=lambda k: self._cache[k]["ts"])
            del self._cache[oldest_key]

    def _rate_limit_wait(self):
        """简单限速: 两次调用间隔至少 _min_interval 秒"""
        elapsed = time.time() - self._last_call_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_ts = time.time()

    def get_token_stats(self) -> Dict:
        """返回 token 使用统计"""
        return {
            "total_api_calls": self._total_calls,
            "total_tokens": self._total_tokens,
            "cache_hits": self._cache_hits,
            "cache_size": len(self._cache),
            "connected": self.connected,
            "provider": self.config.provider,
            "model": self.config.model,
        }

    # ---------- providers config ----------

    @staticmethod
    def _load_providers() -> Dict:
        """从配置文件读取providers，不存在则创建默认"""
        try:
            if PROVIDERS_FILE.exists():
                with open(PROVIDERS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load providers: {e}")

        # Write defaults
        AIConnector._save_providers(_DEFAULT_PROVIDERS)
        return dict(_DEFAULT_PROVIDERS)

    @staticmethod
    def _save_providers(providers: Dict):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(PROVIDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(providers, f, ensure_ascii=False, indent=2)

    def get_providers(self) -> Dict:
        return self.PROVIDERS

    def update_providers(self, providers: Dict) -> Dict:
        """Replace all providers and save to disk"""
        self.PROVIDERS = providers
        self._save_providers(providers)
        return {"success": True, "message": "Providers updated"}

    def add_provider(self, key: str, provider: Dict) -> Dict:
        """Add or update a single provider"""
        self.PROVIDERS[key] = provider
        self._save_providers(self.PROVIDERS)
        return {"success": True, "message": f"Provider '{key}' saved"}

    def delete_provider(self, key: str) -> Dict:
        if key in self.PROVIDERS:
            del self.PROVIDERS[key]
            self._save_providers(self.PROVIDERS)
            return {"success": True, "message": f"Provider '{key}' deleted"}
        return {"success": False, "message": f"Provider '{key}' not found"}

    # ---- 敏感字段加密 ----
    _SENSITIVE_FIELDS = ("api_key", "base_url", "model")

    @staticmethod
    def _encrypt_value(val: str) -> str:
        """加密单个字段"""
        if not val:
            return ""
        from ths_connector import encrypt_cookie
        return encrypt_cookie(val)

    @staticmethod
    def _decrypt_value(val: str) -> str:
        """解密单个字段（兼容明文）"""
        if not val:
            return ""
        from ths_connector import decrypt_cookie
        return decrypt_cookie(val)

    def _is_encrypted(self, val: str) -> bool:
        """判断值是否已加密（Base64只含 A-Za-z0-9+/=）"""
        if not val:
            return False
        import re
        return bool(re.fullmatch(r'[A-Za-z0-9+/]+=*', val))

    def load_config(self):
        """加载AI配置（自动解密敏感字段）"""
        try:
            if AI_CONFIG_FILE.exists():
                with open(AI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # 解密敏感字段
                for field in self._SENSITIVE_FIELDS:
                    if field in data and data[field]:
                        data[field] = self._decrypt_value(data[field])

                self.config = AIConfig(**data)
                self.connected = bool(self.config.api_key)
        except Exception as e:
            logger.error(f"加载AI配置失败: {e}")

    def save_config(self):
        """保存AI配置（加密敏感字段）"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            # 加密敏感字段
            raw = {}
            for k, v in self.config.__dict__.items():
                if k in self._SENSITIVE_FIELDS and v:
                    raw[k] = self._encrypt_value(v)
                else:
                    raw[k] = v

            with open(AI_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            return {"success": True, "message": "配置保存成功"}
        except Exception as e:
            return {"success": False, "message": f"保存失败: {str(e)}"}

    def update_config(self, **kwargs) -> Dict:
        """更新配置"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        self.connected = bool(self.config.api_key)
        return self.save_config()

    def get_config(self) -> Dict:
        """获取当前配置"""
        return {
            "success": True,
            "data": {
                "provider": self.config.provider,
                "model": self.config.model,
                "base_url": self.config.base_url,
                "has_api_key": bool(self.config.api_key),
                "connected": self.connected,
                "providers": self.PROVIDERS
            }
        }

    def _build_headers(self) -> Dict:
        """构建请求头"""
        if self.config.provider == "claude":
            return {
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        else:
            return {
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json"
            }

    def _build_payload(self, messages: List[Dict], system_prompt: str = "") -> Dict:
        """构建请求体"""
        if self.config.provider == "claude":
            payload = {
                "model": self.config.model,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "messages": messages
            }
            if system_prompt:
                payload["system"] = system_prompt
            return payload
        else:
            all_messages = []
            if system_prompt:
                all_messages.append({"role": "system", "content": system_prompt})
            all_messages.extend(messages)
            return {
                "model": self.config.model,
                "messages": all_messages,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens
            }

    def chat(self, user_message: str, system_prompt: str = "", history: List[Dict] = None) -> Dict:
        """
        与AI对话（带限速 + token 统计）

        Args:
            user_message: 用户消息
            system_prompt: 系统提示词
            history: 对话历史

        Returns:
            AI回复
        """
        if not self.connected:
            return {"success": False, "message": "未配置AI API，请先设置API密钥"}

        # 限速
        self._rate_limit_wait()

        try:
            import requests

            messages = history or []
            messages.append({"role": "user", "content": user_message})

            headers = self._build_headers()
            payload = self._build_payload(messages, system_prompt)

            url = f"{self.config.base_url}/chat/completions"
            if self.config.provider == "claude":
                url = f"{self.config.base_url}/v1/messages"

            response = requests.post(url, json=payload, headers=headers, timeout=60)

            if response.status_code == 200:
                data = response.json()
                if self.config.provider == "claude":
                    content = data["content"][0]["text"]
                else:
                    content = data["choices"][0]["message"]["content"]

                # 统计 token
                self._total_calls += 1
                usage = data.get("usage", {})
                self._total_tokens += usage.get("total_tokens", 0)

                return {
                    "success": True,
                    "data": {
                        "content": content,
                        "model": self.config.model,
                        "usage": usage
                    }
                }
            else:
                return {
                    "success": False,
                    "message": f"API请求失败: {response.status_code} - {response.text[:200]}"
                }

        except ImportError:
            return {"success": False, "message": "请安装requests: pip install requests"}
        except Exception as e:
            return {"success": False, "message": f"请求失败: {str(e)}"}

    def analyze_stock(self, symbol: str, stock_data: Dict, indicators: Dict = None) -> Dict:
        """
        AI分析股票（带缓存: 同一股票5分钟内不重复调用）

        Args:
            symbol: 股票代码
            stock_data: 股票实时数据
            indicators: 技术指标数据

        Returns:
            分析结果
        """
        # 缓存检查: 用 symbol + price + change_pct 作为 key
        cache_key = self._cache_key(
            "analyze_stock", symbol,
            stock_data.get("price"), stock_data.get("change_pct")
        )
        cached = self._cache_get(cache_key, "analyze_stock")
        if cached:
            return cached

        system_prompt = """你是一个专业的股票分析师和交易顾问。请根据提供的股票数据和技术指标，给出专业的分析和交易建议。

分析要求：
1. 综合分析股票的基本面和技术面
2. 识别关键的技术指标信号
3. 评估当前的市场情绪和趋势
4. 给出明确的交易建议（买入/卖出/持有）
5. 提供目标价位和止损价位
6. 分析潜在的风险因素

请用中文回答，格式清晰，重点突出。"""

        # 构建分析请求
        data_text = f"""
股票代码: {symbol}
股票名称: {stock_data.get('name', 'N/A')}
当前价格: {stock_data.get('price', 'N/A')}
涨跌幅: {stock_data.get('change_pct', 'N/A')}%
开盘价: {stock_data.get('open', 'N/A')}
最高价: {stock_data.get('high', 'N/A')}
最低价: {stock_data.get('low', 'N/A')}
成交量: {stock_data.get('volume', 'N/A')}
成交额: {stock_data.get('amount', 'N/A')}
换手率: {stock_data.get('turnover', 'N/A')}%
"""
        if indicators:
            data_text += f"""
技术指标:
- MA5: {indicators.get('ma5', 'N/A')}
- MA10: {indicators.get('ma10', 'N/A')}
- MA20: {indicators.get('ma20', 'N/A')}
- RSI: {indicators.get('rsi', 'N/A')}
- MACD: {indicators.get('macd', 'N/A')}
- MACD Signal: {indicators.get('signal', 'N/A')}
- 布林带上轨: {indicators.get('bb_upper', 'N/A')}
- 布林带中轨: {indicators.get('bb_middle', 'N/A')}
- 布林带下轨: {indicators.get('bb_lower', 'N/A')}
"""

        data_text += "\n请分析这只股票并给出交易建议。"

        # 注入用户资金信息
        try:
            from account_manager import account_manager
            from db_store import db_store
            accounts = db_store.list_accounts()
            if accounts:
                acc = next((a for a in accounts if a.get("ths_gdzh")), accounts[0])
                trader = account_manager.get_trader(acc["id"])
                if trader and trader.connected:
                    pos_result = trader.get_positions()
                    if pos_result["success"]:
                        fund = pos_result["data"].get("fund", {})
                        balance = fund.get("available", 0)
                        total = fund.get("total_assets", 0)
                        data_text += f"\n用户资金: 可用资金 {balance} 元, 总资产 {total} 元"
                        data_text += f"\n请确保推荐的买入数量不超过可用资金，且单只股票仓位不超过总资产的30%。"
        except Exception:
            pass

        result = self.chat(data_text, system_prompt)
        if result.get("success"):
            self._cache_set(cache_key, result)
        return result

    def generate_strategy(self, market_data: Dict, risk_level: str = "medium") -> Dict:
        """
        AI生成交易策略（带缓存: 同市场数据1小时内不重复调用）

        Args:
            market_data: 市场数据
            risk_level: 风险等级 (low/medium/high)

        Returns:
            策略建议
        """
        cache_key = self._cache_key("generate_strategy", risk_level, json.dumps(market_data, default=str)[:500])
        cached = self._cache_get(cache_key, "generate_strategy")
        if cached:
            return cached
        risk_descriptions = {
            "low": "保守型投资者，追求稳定收益，可承受较小亏损",
            "medium": "稳健型投资者，追求收益与风险平衡",
            "high": "激进型投资者，追求高收益，可承受较大波动"
        }

        system_prompt = f"""你是一个专业的量化交易策略师。请根据市场数据为{risk_descriptions.get(risk_level, risk_descriptions['medium'])}的投资者设计交易策略。

策略要求：
1. 明确的买入/卖出条件
2. 仓位管理建议
3. 止损止盈设置
4. 风险控制措施
5. 预期收益和风险评估

请用中文回答，提供可执行的交易策略。"""

        data_text = f"""
市场数据:
{json.dumps(market_data, ensure_ascii=False, indent=2)}

风险等级: {risk_level}

请设计适合的交易策略。"""

        result = self.chat(data_text, system_prompt)
        if result.get("success"):
            self._cache_set(cache_key, result)
        return result

    def analyze_with_strategy(self, symbol: str, stock_data: Dict, indicators: Dict,
                              strategy_catalog: str = "") -> Dict:
        """
        AI 选择策略进行分析

        Args:
            symbol: 股票代码
            stock_data: 股票数据
            indicators: 技术指标
            strategy_catalog: 策略目录文本

        Returns:
            AI分析结果
        """
        # 缓存检查
        cache_key = self._cache_key(
            "analyze_with_strategy", symbol,
            stock_data.get("price"), stock_data.get("change_pct")
        )
        cached = self._cache_get(cache_key, "analyze_with_strategy")
        if cached:
            return cached

        system_prompt = f"""你是一个专业的AI交易决策系统。你需要：
1. 分析股票数据和技术指标
2. 从可用策略中选择最适合的策略
3. 综合判断给出交易建议

可用策略目录:
{strategy_catalog}

请用JSON格式回答:
{{
    "selected_strategies": ["策略名称1", "策略名称2"],
    "reason": "选择理由",
    "action": "buy/sell/hold",
    "confidence": 0.0-1.0,
    "analysis": "详细分析"
}}"""

        data_text = f"""
股票代码: {symbol}
当前价格: {stock_data.get('price', 'N/A')}
涨跌幅: {stock_data.get('change_pct', 'N/A')}%
成交量: {stock_data.get('volume', 'N/A')}
换手率: {stock_data.get('turnover', 'N/A')}%
技术指标: {json.dumps(indicators, ensure_ascii=False)}
"""
        result = self.chat(data_text, system_prompt)
        if result.get("success"):
            self._cache_set(cache_key, result)
        return result

    def explain_indicator(self, indicator_name: str, value: float, context: str = "") -> Dict:
        """
        AI解释技术指标（带缓存: 同一指标值30分钟内不重复调用）

        Args:
            indicator_name: 指标名称
            value: 指标值
            context: 上下文信息

        Returns:
            指标解释
        """
        # 缓存检查
        cache_key = self._cache_key("explain_indicator", indicator_name, value, context)
        cached = self._cache_get(cache_key, "explain_indicator")
        if cached:
            return cached

        system_prompt = """你是一个技术分析专家。请用通俗易懂的语言解释技术指标的含义和交易信号。

要求：
1. 解释指标的基本原理
2. 分析当前值的含义
3. 给出交易信号解读
4. 提供操作建议"""

        data_text = f"""
技术指标: {indicator_name}
当前值: {value}
{f"上下文: {context}" if context else ""}

请解释这个技术指标。"""

        result = self.chat(data_text, system_prompt)
        if result.get("success"):
            self._cache_set(cache_key, result)
        return result


# 全局AI连接器实例
ai_connector = AIConnector()
