#!/usr/bin/env python3
"""
云端自动交易脚本（完整版）
使用34个策略 + AI分析，不依赖本地服务器

GitHub Actions 每小时触发
  → 34策略筛选 → AI深度分析 → 自动执行交易
"""

import os
import sys
import json
import re
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# 添加项目源码路径
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

# ========== THS API ==========
THS_API = "https://mncg.10jqka.com.cn/cgiwt/delegate"
THS_HEADERS = {
    "Host": "mncg.10jqka.com.cn",
    "Origin": "https://mncg.10jqka.com.cn",
    "Referer": "https://mncg.10jqka.com.cn/cgiwt/index/index",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
}


class THSClient:
    """THS API 客户端"""
    def __init__(self, cookie):
        self.cookie = cookie
        self.headers = {**THS_HEADERS, "Cookie": cookie}

    def _post(self, path, data):
        try:
            r = requests.post(f"{THS_API}/{path}", headers=self.headers, data=data, timeout=15)
            return r.json() if r.status_code == 200 else {"errorcode": -1}
        except Exception as e:
            return {"errorcode": -1, "errormsg": str(e)}

    def get_fund(self):
        r = self._post("updateclass", {"type": "cmd_wt_mairu", "updateClass": "qryzijin|"})
        if str(r.get("errorcode")) == "0":
            return r.get("result", {}).get("qryzijin", {}).get("result", {}).get("data", {})
        return {}

    def get_positions(self):
        r = self._post("updateclass", {"type": "cmd_wt_mairu", "updateClass": "qrygphl|"})
        if str(r.get("errorcode")) == "0":
            return r.get("result", {}).get("qrygphl", {}).get("result", {}).get("data", {}).get("gphlList", [])
        return []

    def get_orders(self):
        r = self._post("updateclass", {"type": "cmd_wt_mairu", "updateClass": "qrywt|"})
        if str(r.get("errorcode")) == "0":
            return r.get("result", {}).get("qrywt", {}).get("result", {}).get("data", {}).get("wtList", [])
        return []

    def buy(self, symbol, price, amount):
        return self._post("tradestock/", {
            "cmd_wt_mairu": "cmd_wt_mairu", "wtfs": "0", "gddm": "",
            "zqdm": symbol, "wtjg": f"{price:.2f}", "wtsl": str(amount)
        })

    def sell(self, symbol, price, amount):
        return self._post("tradestock/", {
            "cmd_wt_mairu": "cmd_wt_maichu", "wtfs": "0", "gddm": "",
            "zqdm": symbol, "wtjg": f"{price:.2f}", "wtsl": str(amount)
        })

    def cancel_order(self, order_id, date):
        return self._post("cancelDelegated/", {"htbh": order_id, "wtrq": date})


def get_realtime_quote(symbol):
    """获取实时行情（通过THS API）"""
    try:
        url = f"https://qt.gtimg.cn/q={'sh' if symbol.startswith('6') else 'sz'}{symbol}"
        r = requests.get(url, timeout=5)
        parts = r.text.split("~")
        if len(parts) > 30:
            return {
                "code": symbol, "name": parts[1], "price": float(parts[3] or 0),
                "change_pct": float(parts[32] or 0), "volume": float(parts[6] or 0),
                "amount": float(parts[5] or 0), "turnover": float(parts[38] or 0),
                "open": float(parts[5] or 0), "high": float(parts[33] or 0),
                "low": float(parts[34] or 0), "pre_close": float(parts[4] or 0),
            }
    except Exception:
        pass
    return None


def get_stock_pool(client, limit=100):
    """获取股票池（复用 competition 的逻辑）"""
    try:
        from competition import CompetitionManager
        cm = CompetitionManager()
        return cm.get_stock_pool(sort_by="change_pct", limit=limit)
    except Exception:
        # 简化版：直接从行情接口获取
        try:
            r = requests.get("https://qt.gtimg.cn/q=sh000001", timeout=5)
            # 简化：返回空列表
            return []
        except:
            return []


def run_strategy_analysis(stocks, client):
    """用34策略分析股票"""
    try:
        from ai_brain import ai_brain
        from data_cache import get_realtime_quote as _grq, compute_indicators
        from auto_trader import auto_trader

        flagged = []
        for stock in stocks[:50]:
            symbol = stock.get("code", "")
            if not symbol:
                continue

            stock_data = get_realtime_quote(symbol)
            if not stock_data or stock_data["price"] <= 0:
                continue

            try:
                indicators = compute_indicators(symbol) or {"close": stock_data["price"]}
                decision = ai_brain.decide(
                    symbol=symbol, stock_data=stock_data,
                    indicators=indicators, account_info={"balance": 0}
                )
                if decision.action in ("buy", "sell") and decision.confidence > 0.4:
                    flagged.append({
                        "symbol": symbol, "name": stock_data["name"],
                        "action": decision.action, "confidence": decision.confidence,
                        "stock_data": stock_data,
                        "target_price": getattr(decision, "target_price", None),
                        "stop_loss": getattr(decision, "stop_loss", None),
                    })
            except Exception:
                pass

        print(f"  策略筛选: {len(stocks)}只 → {len(flagged)}只有信号")
        return flagged
    except Exception as e:
        print(f"  策略分析失败: {e}")
        return []


def ai_analyze(flagged_stocks):
    """AI深度分析有信号的股票"""
    api_key = os.environ.get("AI_API_KEY", "")
    if not api_key:
        print("  无AI API Key，跳过AI分析")
        return flagged_stocks  # 直接用策略结果

    api_url = os.environ.get("AI_API_URL", "https://api.deepseek.com/chat/completions")
    model = os.environ.get("AI_MODEL", "deepseek-chat")

    # 构建分析上下文
    context = "以下是策略筛选出的有信号股票，请分析并决定最终交易：\n\n"
    for item in flagged_stocks[:10]:
        sd = item["stock_data"]
        context += f"【{item['name']} {item['symbol']}】\n"
        context += f"  价格: {sd.get('price', '?')} | 涨跌: {sd.get('change_pct', '?')}%\n"
        context += f"  策略信号: {item['action'].upper()} 置信度{item['confidence']:.0%}\n"
        context += f"  目标价: {item.get('target_price', '?')} | 止损: {item.get('stop_loss', '?')}\n\n"

    context += "请决定最终交易，格式: [TRADE:BUY:代码:数量] 或 [TRADE:SELL:代码:数量]"

    try:
        r = requests.post(api_url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }, json={"model": model, "messages": [{"role": "user", "content": context}],
                 "max_tokens": 500}, timeout=30)

        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            print(f"  AI分析: {content[:100]}...")
            return parse_trade_tags(content, flagged_stocks)
        else:
            print(f"  AI API错误: {r.status_code}")
    except Exception as e:
        print(f"  AI分析失败: {e}")

    return flagged_stocks[:3]  # fallback: 取前3个策略信号


def parse_trade_tags(text, fallback_stocks):
    """解析AI交易标签"""
    trades = []
    for m in re.finditer(r'\[TRADE:(BUY|SELL):(\d{6}):(\d+)\]', text, re.IGNORECASE):
        action = m.group(1).lower()
        symbol = m.group(2)
        amount = int(m.group(3))
        # 找到对应的股票数据
        stock_data = next((s["stock_data"] for s in fallback_stocks if s["symbol"] == symbol), None)
        if stock_data:
            trades.append({"action": action, "symbol": symbol, "amount": amount, "stock_data": stock_data})
    return trades if trades else fallback_stocks[:3]


def execute_trades(client, trades):
    """执行交易"""
    results = []
    for t in trades[:5]:  # 最多执行5笔（分散化）
        action = t["action"]
        symbol = t["symbol"]
        amount = t["amount"]
        stock_data = t.get("stock_data", {})
        price = stock_data.get("price", 0)

        if price <= 0:
            results.append({"action": action, "symbol": symbol, "success": False, "msg": "无价格"})
            continue

        # 整手验证
        if action == "buy":
            amount = (amount // 100) * 100
            if amount < 100:
                results.append({"action": action, "symbol": symbol, "success": False, "msg": "不足1手"})
                continue

        # 涨跌停检查
        change = stock_data.get("change_pct", 0)
        if action == "buy" and change >= 9.9:
            results.append({"action": action, "symbol": symbol, "success": False, "msg": "涨停中"})
            continue
        if action == "sell" and change <= -9.9:
            results.append({"action": action, "symbol": symbol, "success": False, "msg": "跌停中"})
            continue

        # 执行
        if action == "buy":
            r = client.buy(symbol, price, amount)
        else:
            r = client.sell(symbol, price, amount)

        success = str(r.get("errorcode")) == "0"
        results.append({
            "action": action, "symbol": symbol, "amount": amount,
            "price": price, "success": success,
            "msg": r.get("errormsg", "OK" if success else "FAIL")
        })
        print(f"  {'买入' if action == 'buy' else '卖出'} {symbol} {amount}股@{price} → {'成功' if success else '失败'}")

    return results


def send_notification(message):
    """发送Telegram通知（可选）"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception:
        pass


def save_results(results, fund_data, flagged_count):
    """保存交易结果到JSON文件（供本地服务器同步）"""
    try:
        output_dir = Path(__file__).parent.parent / "data"
        output_dir.mkdir(exist_ok=True)

        log_file = output_dir / "trade_log.json"
        # 读取已有记录
        history = []
        if log_file.exists():
            try:
                history = json.loads(log_file.read_text(encoding="utf-8"))
            except:
                history = []

        # 追加本次记录
        entry = {
            "timestamp": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
            "balance": float(fund_data.get("kyje", 0)),
            "total_assets": float(fund_data.get("zzc", 0)),
            "flagged_count": flagged_count,
            "trades": results,
        }
        history.append(entry)

        # 只保留最近100条
        history = history[-100:]

        log_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  交易记录已保存: {log_file}")
    except Exception as e:
        print(f"  保存失败: {e}")


def run():
    """主流程"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 云端自动交易启动")

    # 检查交易时段
    if now.weekday() >= 5:
        print("  周末跳过")
        return
    if now.hour < 9 or (now.hour == 9 and now.minute < 30) or now.hour >= 15:
        print(f"  非交易时段 ({now.strftime('%H:%M')}) 跳过")
        return

    cookie = os.environ.get("THS_COOKIE", "")
    if not cookie:
        print("  THS_COOKIE 未设置")
        return

    client = THSClient(cookie)

    # 1. 检查连接
    fund = client.get_fund()
    if not fund:
        print("  Cookie无效")
        return
    balance = float(fund.get("kyje", 0))
    print(f"  余额: {balance:,.2f}")

    # 2. 获取股票池
    print("  获取股票池...")
    stocks = get_stock_pool(client, limit=100)
    if not stocks:
        print("  无法获取股票池")
        return

    # 3. 策略分析
    print("  运行34策略分析...")
    flagged = run_strategy_analysis(stocks, client)

    # 4. AI分析（如果有Key）
    if flagged:
        print("  AI深度分析...")
        final_trades = ai_analyze(flagged)
    else:
        final_trades = []
        print("  无策略信号")

    # 5. 执行交易
    if final_trades:
        print(f"  执行 {len(final_trades)} 笔交易...")
        results = execute_trades(client, final_trades)

        # 发送通知
        success_count = sum(1 for r in results if r["success"])
        msg = f"🤖 云端交易: {success_count}/{len(results)} 笔成功\n"
        for r in results:
            label = "买入" if r["action"] == "buy" else "卖出"
            status = "✅" if r["success"] else "❌"
            msg += f"{status} {label} {r['symbol']} {r['amount']}股@{r.get('price', 0)}\n"
        send_notification(msg)
        save_results(results, fund, len(flagged))
        print(f"  完成: {success_count}/{len(results)} 笔成功")
    else:
        print("  无需交易")

    print(f"[{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')}] 完成")


if __name__ == "__main__":
    run()
