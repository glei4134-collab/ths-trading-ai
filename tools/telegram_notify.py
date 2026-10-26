#!/usr/bin/env python3
"""
Telegram 通知模块
交易完成后推送到手机

用法:
  # 环境变量
  TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=xxx python tools/telegram_notify.py "测试消息"

  # 在其他脚本中导入
  from telegram_notify import send_message
  send_message("买入 600519 100股 成功")
"""

import os
import sys
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """发送Telegram消息"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print(f"[Telegram] 未配置: {text}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=10)
        if r.status_code == 200:
            print(f"[Telegram] 已发送: {text[:50]}...")
            return True
        else:
            print(f"[Telegram] 失败: {r.status_code} {r.text[:100]}")
            return False
    except Exception as e:
        print(f"[Telegram] 错误: {e}")
        return False


def send_trade_notification(action: str, symbol: str, amount: int, price: float, success: bool, message: str = ""):
    """发送交易通知"""
    emoji = "✅" if success else "❌"
    label = "买入" if action == "buy" else "卖出"
    total = amount * price

    text = (
        f"{emoji} <b>{label} {symbol}</b>\n"
        f"数量: {amount}股\n"
        f"价格: {price:.2f}\n"
        f"金额: {total:.2f}\n"
    )
    if message:
        text += f"备注: {message}\n"
    text += f"时间: {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')}"

    return send_message(text)


def send_daily_summary(balance: float, positions: list, trades: list, sentiment: int):
    """发送每日汇总"""
    text = f"📊 <b>每日交易汇总</b>\n\n"
    text += f"💰 资金: {balance:,.2f}\n"
    text += f"📈 情绪: {sentiment}/100\n\n"

    if positions:
        text += "<b>持仓:</b>\n"
        for p in positions[:5]:
            pnl = p.get("profit", 0)
            pnl_emoji = "+" if pnl >= 0 else ""
            text += f"  {p.get('name','')} {p.get('amount',0)}股 {pnl_emoji}{pnl:.2f}\n"
    else:
        text += "持仓: 空仓\n"

    if trades:
        text += f"\n<b>今日交易: {len(trades)}笔</b>\n"
        for t in trades[:5]:
            text += f"  {t.get('action','')} {t.get('symbol','')} {t.get('amount',0)}股\n"

    text += f"\n⏰ {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')}"
    return send_message(text)


def send_error(error: str):
    """发送错误通知"""
    text = f"⚠️ <b>系统异常</b>\n\n{error}\n\n⏰ {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')}"
    return send_message(text)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        msg = " ".join(sys.argv[1:])
        send_message(msg)
    else:
        send_message(f"🔔 测试通知\n时间: {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')}")
