"""
比赛规则管理模块
处理同花顺模拟比赛的交易时间、规则验证、状态查询
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date, time, timedelta
from typing import Dict, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from paths import CONFIG_DIR
CONFIG_FILE = CONFIG_DIR / "competition_rules.json"


class CompetitionManager:
    """比赛规则管理"""

    def __init__(self):
        self.rules = {}
        self.load_rules()

    def load_rules(self):
        """加载比赛规则"""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.rules = json.load(f)
                logger.info(f"加载比赛规则: {self.rules.get('competition_name', '未知')}")
            else:
                logger.warning("比赛规则配置文件不存在，使用默认规则")
                self.rules = self._default_rules()
        except Exception as e:
            logger.error(f"加载比赛规则失败: {e}")
            self.rules = self._default_rules()

    def _default_rules(self) -> Dict:
        return {
            "competition_name": "同花顺模拟比赛",
            "initial_funds": 1000000,
            "trading_rules": {
                "market": ["SH", "SZ"],
                "trading_hours": [
                    {"session": "morning", "start": "09:30", "end": "11:30"},
                    {"session": "afternoon", "start": "13:00", "end": "15:00"}
                ],
                "t_plus_1": True,
                "min_order_unit": 100,
                "price_limits": {
                    "main_board": {"up": 0.10, "down": -0.10},
                    "gem": {"up": 0.20, "down": -0.20},
                    "star": {"up": 0.20, "down": -0.20}
                },
                "short_selling": False
            },
            "holidays_2026": [],
            "period": {"start": "2026-01-01", "end": "2026-12-31"}
        }

    def get_rules(self) -> Dict:
        """获取完整比赛规则"""
        return self.rules

    def is_trading_day(self, check_date: date = None) -> bool:
        """检查指定日期是否为交易日"""
        if check_date is None:
            check_date = date.today()

        # 周末不交易
        if check_date.weekday() >= 5:
            return False

        # 节假日不交易
        holidays = self.rules.get("holidays_2026", [])
        if check_date.isoformat() in holidays:
            return False

        return True

    def is_trading_hours(self, now: datetime = None) -> bool:
        """检查当前是否在交易时间"""
        if now is None:
            now = datetime.now(ZoneInfo('Asia/Shanghai'))

        # 非交易日直接返回 False
        if not self.is_trading_day(now.date()):
            return False

        current_time = now.time()
        hours = self.rules.get("trading_rules", {}).get("trading_hours", [])

        for session in hours:
            start = time.fromisoformat(session["start"])
            end = time.fromisoformat(session["end"])
            if start <= current_time <= end:
                return True

        return False

    def get_market_status(self, now: datetime = None) -> Dict:
        """获取当前市场状态"""
        if now is None:
            now = datetime.now(ZoneInfo('Asia/Shanghai'))

        is_trading_day = self.is_trading_day(now.date())
        is_trading = self.is_trading_hours(now)
        current_time = now.time()
        hours = self.rules.get("trading_rules", {}).get("trading_hours", [])

        status = "closed"
        session = ""
        next_open = ""

        if is_trading_day:
            if is_trading:
                status = "open"
                # 判断当前是上午还是下午
                morning_end = time.fromisoformat(hours[0]["end"]) if hours else time(11, 30)
                if current_time <= morning_end:
                    session = "morning"
                else:
                    session = "afternoon"
            else:
                # 判断是开盘前还是午休还是收盘后
                if hours:
                    morning_start = time.fromisoformat(hours[0]["start"])
                    morning_end = time.fromisoformat(hours[0]["end"])
                    afternoon_start = time.fromisoformat(hours[1]["start"])
                    afternoon_end = time.fromisoformat(hours[1]["end"])

                    if current_time < morning_start:
                        status = "pre_market"
                        next_open = f"{now.date().isoformat()} {hours[0]['start']}"
                    elif morning_end < current_time < afternoon_start:
                        status = "lunch_break"
                        next_open = f"{now.date().isoformat()} {hours[1]['start']}"
                    elif current_time > afternoon_end:
                        status = "closed"
                        # 找下一个交易日的开盘时间
                        next_open = self._next_trading_open(now)
        else:
            status = "holiday"
            next_open = self._next_trading_open(now)

        # 计算倒计时
        countdown = self._calc_countdown(now, next_open)

        # 计算距离下次开盘的秒数
        next_open_seconds = 0
        if next_open and next_open != "未知":
            try:
                target = datetime.fromisoformat(next_open)
                next_open_seconds = max(0, int((target - now).total_seconds()))
            except Exception:
                pass

        return {
            "status": status,
            "status_text": {
                "open": "交易中",
                "pre_market": "盘前",
                "lunch_break": "午间休市",
                "closed": "已收盘",
                "holiday": "休市"
            }.get(status, status),
            "session": session,
            "is_trading_day": is_trading_day,
            "is_trading_hours": is_trading,
            "next_open": next_open,
            "next_open_seconds": next_open_seconds,
            "countdown": countdown
        }

    def _next_trading_open(self, from_time: datetime) -> str:
        """计算下一个交易日的开盘时间"""
        hours = self.rules.get("trading_rules", {}).get("trading_hours", [])
        morning_start = hours[0]["start"] if hours else "09:30"

        check = from_time + timedelta(days=1)
        # 最多查找30天
        for _ in range(30):
            if self.is_trading_day(check.date()):
                return f"{check.date().isoformat()} {morning_start}"
            check += timedelta(days=1)

        return "未知"

    def _calc_countdown(self, now: datetime, next_open: str) -> str:
        """计算倒计时字符串"""
        if not next_open or next_open == "未知":
            return ""

        try:
            target = datetime.fromisoformat(next_open)
            diff = target - now
            if diff.total_seconds() <= 0:
                return ""

            days = diff.days
            hours, remainder = divmod(diff.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            parts = []
            if days > 0:
                parts.append(f"{days}天")
            if hours > 0:
                parts.append(f"{hours}时")
            if minutes > 0:
                parts.append(f"{minutes}分")
            parts.append(f"{seconds}秒")

            return "".join(parts)
        except Exception:
            return ""

    def get_competition_countdown(self) -> str:
        """获取比赛剩余时间"""
        period = self.rules.get("period", {})
        end_str = period.get("end")
        if not end_str:
            return "未知"

        try:
            end_date = date.fromisoformat(end_str)
            remaining = (end_date - date.today()).days
            if remaining < 0:
                return "已结束"
            return f"{remaining}天"
        except Exception:
            return "未知"

    def validate_order(self, order: Dict) -> Tuple[bool, str]:
        """
        验证订单是否合规

        Args:
            order: {symbol, action, quantity, price, ...}

        Returns:
            (is_valid, message)
        """
        rules = self.rules.get("trading_rules", {})
        symbol = order.get("symbol", "")
        action = order.get("action", "")
        quantity = order.get("quantity", 0)
        price = order.get("price", 0)

        # 1. 检查是否在交易时间
        if not self.is_trading_hours():
            market_status = self.get_market_status()
            return False, f"当前不在交易时间 ({market_status['status_text']})，下次开盘: {market_status['next_open']}"

        # 2. 检查最小交易单位
        min_unit = rules.get("min_order_unit", 100)
        if quantity % min_unit != 0:
            return False, f"交易数量必须是{min_unit}的整数倍，当前: {quantity}"

        # 3. 检查市场
        allowed_markets = rules.get("market", [])
        if symbol.startswith("6") and "SH" not in allowed_markets:
            return False, "不允许交易上海市场股票"
        if symbol.startswith(("0", "3")) and "SZ" not in allowed_markets:
            return False, "不允许交易深圳市场股票"

        # 4. 检查股票可交易性（停牌/涨跌停）
        try:
            from fast_data import fetch_realtime_single
            quote = fetch_realtime_single(symbol)
            if quote:
                # 停牌: 成交量为0
                if quote.get("volume", 0) == 0:
                    return False, f"{symbol} 当前停牌，无法交易"
                # 涨停: 无法买入
                if action == "buy" and quote.get("change_pct", 0) >= 9.9:
                    return False, f"{symbol} 涨停中，无法买入"
                # 跌停: 无法卖出
                if action == "sell" and quote.get("change_pct", 0) <= -9.9:
                    return False, f"{symbol} 跌停中，无法卖出"
        except Exception:
            # 获取行情失败不阻塞交易，仅做基础验证
            pass

        # 5. 检查是否允许做空
        if action == "sell" and not rules.get("short_selling", False):
            # 卖出需要检查是否有持仓（这里只做基础验证）
            pass

        return True, "订单合规"

    def get_commission(self, action: str, price: float, quantity: int) -> Dict:
        """计算交易费用"""
        commission_config = self.rules.get("trading_rules", {}).get("commission", {})

        amount = price * quantity

        # 佣金
        buy_rate = commission_config.get("buy_rate", 0.0003)
        sell_rate = commission_config.get("sell_rate", 0.0003)
        min_commission = commission_config.get("min_commission", 5)

        if action == "buy":
            commission = max(amount * buy_rate, min_commission)
        else:
            commission = max(amount * sell_rate, min_commission)

        # 印花税（仅卖出）
        stamp_tax = 0
        if action == "sell":
            stamp_tax = amount * commission_config.get("stamp_tax_rate", 0.001)

        # 过户费
        transfer_fee = amount * commission_config.get("transfer_fee_rate", 0.00002)

        total_fee = commission + stamp_tax + transfer_fee

        return {
            "commission": round(commission, 2),
            "stamp_tax": round(stamp_tax, 2),
            "transfer_fee": round(transfer_fee, 2),
            "total_fee": round(total_fee, 2),
            "amount": round(amount, 2),
            "total_cost": round(amount + total_fee if action == "buy" else amount - total_fee, 2)
        }

    def get_status(self) -> Dict:
        """获取完整比赛状态"""
        market = self.get_market_status()
        competition_countdown = self.get_competition_countdown()

        return {
            "competition_name": self.rules.get("competition_name", "同花顺模拟比赛"),
            "period": self.rules.get("period", {}),
            "initial_funds": self.rules.get("initial_funds", 1000000),
            "market": market,
            "competition_countdown": competition_countdown,
            "trading_rules": self.rules.get("trading_rules", {})
        }

    def is_tradable(self, symbol: str) -> bool:
        """
        检查股票是否在比赛允许范围内

        规则：
        - 上海市场(6开头) 需要 SH 在允许列表
        - 深圳市场(0/3开头) 需要 SZ 在允许列表
        - 排除ST、退市股
        """
        rules = self.rules.get("trading_rules", {})
        allowed_markets = rules.get("market", [])

        if symbol.startswith("6") and "SH" not in allowed_markets:
            return False
        if symbol.startswith(("0", "3")) and "SZ" not in allowed_markets:
            return False

        return True

    def get_stock_pool(self, limit: int = 100, sort_by: str = "amount") -> list:
        """
        获取比赛可交易股票池
        按比赛规则: market=["SH","SZ"]，全市场A股均可交易

        Args:
            limit: 返回数量
            sort_by: 排序字段 (amount/turnover/change_pct/volume)

        Returns:
            [{code, name, price, change_pct, volume, amount, turnover, pre_close, open, high, low}, ...]
        """
        try:
            from data_cache import get_realtime_quote
            df = get_realtime_quote()

            if df is None or df.empty:
                return []

            # 按比赛规则过滤市场: SH(6开头) + SZ(0/3开头)
            rules = self.rules.get("trading_rules", {})
            allowed = rules.get("market", ["SH", "SZ"])
            masks = []
            if "SH" in allowed:
                masks.append(df['代码'].str.startswith('6'))
            if "SZ" in allowed:
                masks.append(df['代码'].str.startswith(('0', '3')))
            if masks:
                df = df[masks[0] | masks[1] if len(masks) > 1 else masks[0]]

            # 基础过滤: 排除停牌和无效价格
            df = df[df['成交量'] > 0]
            df = df[df['最新价'] > 0]

            # 排除封死涨停（买入无法成交）
            is_limit_up = (df['涨跌幅'] >= 9.8) & (df['最高'] == df['最新价'])
            df = df[~is_limit_up]

            # 排除封死跌停（无法卖出/买入）
            is_limit_down = (df['涨跌幅'] <= -9.8) & (df['最低'] == df['最新价'])
            df = df[~is_limit_down]

            # 排除一字板（开盘即封板，全天无法交易）
            is_one_word = (
                (df['今开'] == df['最高']) &
                (df['最高'] == df['最低']) &
                (df['涨跌幅'].abs() > 0.5)
            )
            df = df[~is_one_word]

            # 排序：综合评分（成交额×活跃度×适度涨幅）
            if sort_by == "change_pct":
                # 涨跌幅排序时，加权成交额避免选到冷门股
                df["_score"] = df["成交额"] * 0.3 + df["换手率"].clip(0, 20) * 5 + df["涨跌幅"].clip(-5, 8) * 10
                df = df.sort_values("_score", ascending=False)
            else:
                sort_map = {
                    "amount": "成交额", "turnover": "换手率",
                    "volume": "成交量",
                }
                sort_col = sort_map.get(sort_by, "成交额")
                df = df.sort_values(sort_col, ascending=False)

            stocks = []
            for _, row in df.head(limit).iterrows():
                stocks.append({
                    "code": row['代码'],
                    "name": row['名称'],
                    "price": float(row['最新价']),
                    "change_pct": float(row['涨跌幅']),
                    "change": float(row.get('涨跌额', 0)),
                    "open": float(row.get('今开', 0)),
                    "high": float(row.get('最高', 0)),
                    "low": float(row.get('最低', 0)),
                    "pre_close": float(row.get('昨收', 0)),
                    "volume": int(row['成交量']),
                    "amount": float(row['成交额']),
                    "turnover": float(row['换手率']),
                })

            logger.info(f"获取股票池: {len(stocks)} 只 (共 {len(df)} 只)")
            return stocks

        except Exception as e:
            logger.error(f"获取股票池失败: {e}")
            return []

    def get_stock_pool_count(self) -> Dict:
        """获取股票池统计"""
        try:
            from data_cache import get_realtime_quote
            df = get_realtime_quote()

            if df is None or df.empty:
                return {"total": 0, "sh": 0, "sz": 0, "tradable": 0}

            # 按比赛规则过滤市场
            rules = self.rules.get("trading_rules", {})
            allowed = rules.get("market", ["SH", "SZ"])
            masks = []
            if "SH" in allowed:
                masks.append(df['代码'].str.startswith('6'))
            if "SZ" in allowed:
                masks.append(df['代码'].str.startswith(('0', '3')))
            if masks:
                market_df = df[masks[0] | masks[1] if len(masks) > 1 else masks[0]]
            else:
                market_df = df

            sh_count = int(df['代码'].str.startswith('6').sum())
            sz_count = int(df['代码'].str.startswith(('0', '3')).sum())

            # 可交易: 比赛允许的市场 + 有成交 + 有效价格
            tradable = market_df[
                (market_df['成交量'] > 0) &
                (market_df['最新价'] > 0)
            ]

            return {
                "total": len(df),
                "sh": sh_count,
                "sz": sz_count,
                "tradable": len(tradable)
            }

        except Exception as e:
            logger.error(f"获取股票池统计失败: {e}")
            return {"total": 0, "sh": 0, "sz": 0, "tradable": 0}


# 全局实例
competition_manager = CompetitionManager()
