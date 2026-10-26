"""
测试同花顺API - 查看订单和持仓状态
"""

import json
from pathlib import Path
from src.ths_connector import THSWebConnector

# 加载Cookie
cookie_file = Path("config/ths_cookie.json")
if not cookie_file.exists():
    print("未找到Cookie文件，请先保存Cookie")
    exit(1)

with open(cookie_file, 'r', encoding='utf-8') as f:
    data = json.load(f)
    cookie = data.get("cookie")

if not cookie:
    print("Cookie为空")
    exit(1)

# 创建连接器
connector = THSWebConnector()
connector.cookie = cookie
connector.connected = True

print("=" * 60)
print("同花顺API测试")
print("=" * 60)

# 1. 查询资金
print("\n[1] 查询资金...")
fund_result = connector.get_fund()
print(f"  结果: {json.dumps(fund_result, ensure_ascii=False, indent=2)}")

# 2. 查询持仓
print("\n[2] 查询持仓...")
pos_result = connector.get_positions()
print(f"  结果: {json.dumps(pos_result, ensure_ascii=False, indent=2)}")

# 3. 查询委托订单
print("\n[3] 查询委托订单...")
order_result = connector.get_orders()
print(f"  结果: {json.dumps(order_result, ensure_ascii=False, indent=2)}")

# 4. 查询完整摘要
print("\n[4] 查询完整摘要...")
summary_result = connector.get_account_summary()
print(f"  结果: {json.dumps(summary_result, ensure_ascii=False, indent=2)}")
