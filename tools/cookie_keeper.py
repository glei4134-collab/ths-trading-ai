#!/usr/bin/env python3
"""
Cookie云端续签脚本 — 保持同花顺会话活跃

用法:
  # 本地测试
  python tools/cookie_keeper.py "your_cookie_here"

  # GitHub Actions（从 secrets 读取）
  THS_COOKIE="${{ secrets.THS_COOKIE }}" python tools/cookie_keeper.py

原理: 带Cookie调一次THS API（查询资金），服务器收到请求后延长会话有效期。
"""

import os
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

THS_API = "https://mncg.10jqka.com.cn/cgiwt/delegate/updateclass"
HEADERS = {
    "Host": "mncg.10jqka.com.cn",
    "Origin": "https://mncg.10jqka.com.cn",
    "Referer": "https://mncg.10jqka.com.cn/cgiwt/index/index",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
}


def refresh(cookie):
    """调用THS API保持Cookie活跃"""
    headers = {**HEADERS, "Cookie": cookie}
    resp = requests.post(
        THS_API,
        headers=headers,
        data={"type": "cmd_wt_mairu", "updateClass": "qryzijin|"},
        timeout=15,
    )
    if resp.status_code == 200:
        r = resp.json()
        if str(r.get("errorcode", "")) == "0":
            # 防御 None 值：key 存在但值为 null 时 .get() 返回 None，需要兜底 {}
            fund = (
                (r.get("result") or {})
                .get("qryzijin") or {}
                .get("result") or {}
                .get("data") or {}
            )
            return {"ok": True, "balance": fund.get("kyje", "?")}
        return {"ok": False, "error": r.get("errormsg", "unknown")}
    return {"ok": False, "status": resp.status_code}


if __name__ == "__main__":
    cookie = os.environ.get("THS_COOKIE") or (
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    if not cookie:
        print("ERROR: No cookie. Set THS_COOKIE env var or pass as argument.")
        sys.exit(1)

    now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
    try:
        result = refresh(cookie)
        if result["ok"]:
            print(f"[{now}] OK - balance: {result['balance']}")
            sys.exit(0)
        else:
            print(f"[{now}] FAILED - {result}")
            sys.exit(1)
    except Exception as e:
        print(f"[{now}] ERROR - {e}")
        sys.exit(1)
