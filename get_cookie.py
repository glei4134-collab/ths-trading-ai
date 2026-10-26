"""
同花顺模拟比赛 Cookie 自动获取工具
支持从 Chrome/Edge 浏览器读取 Cookie（包括 HttpOnly 类型）
"""

import json
import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime

COOKIE_DIR = Path(__file__).parent / "config"
COOKIE_FILE = COOKIE_DIR / "ths_cookie.json"
THS_DOMAIN = "10jqka.com.cn"


def try_edge_cookies():
    """从 Edge 浏览器读取 Cookie"""
    try:
        import browser_cookie3
        cj = browser_cookie3.edge(domain_name=THS_DOMAIN)
        cookies_dict = {c.name: c.value for c in cj}
        if cookies_dict:
            return "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
    except Exception as e:
        print(f"  Edge: {e}")
    return None


def try_firefox_cookies():
    """从 Firefox 浏览器读取 Cookie"""
    try:
        import browser_cookie3
        cj = browser_cookie3.firefox(domain_name=THS_DOMAIN)
        cookies_dict = {c.name: c.value for c in cj}
        if cookies_dict:
            return "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
    except Exception as e:
        print(f"  Firefox: {e}")
    return None


def try_chrome_cookies():
    """从 Chrome 浏览器读取 Cookie"""
    try:
        import browser_cookie3
        cj = browser_cookie3.chrome(domain_name=THS_DOMAIN)
        cookies_dict = {c.name: c.value for c in cj}
        if cookies_dict:
            return "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
    except Exception as e:
        print(f"  Chrome: {e}")
    return None


def get_chrome_cookies_via_cdp():
    """通过 Chrome DevTools Protocol 获取 Cookie（需要 Chrome 以调试模式启动）"""
    import requests

    try:
        # 连接到 Chrome 调试端口
        response = requests.get("http://localhost:9222/json/version", timeout=2)
        if response.status_code != 200:
            return None

        # 获取 WebSocket 调试地址
        data = response.json()
        ws_url = data.get("webSocketDebuggerUrl", "")

        print("  检测到 Chrome 调试端口")
        print("  请确保 Chrome 已访问过 https://mncg.10jqka.com.cn/")

        return None  # 需要 WebSocket 支持，这里简化处理

    except:
        return None


def get_all_cookies():
    """尝试所有方式获取 Cookie"""
    print("正在尝试获取 Cookie...")

    print("\n尝试 Edge 浏览器...")
    cookie = try_edge_cookies()
    if cookie:
        print("  ✓ 从 Edge 获取成功")
        return cookie

    print("\n尝试 Firefox 浏览器...")
    cookie = try_firefox_cookies()
    if cookie:
        print("  ✓ 从 Firefox 获取成功")
        return cookie

    print("\n尝试 Chrome 浏览器...")
    cookie = try_chrome_cookies()
    if cookie:
        print("  ✓ 从 Chrome 获取成功")
        return cookie

    return None


def save_cookie(cookie_string):
    """保存 Cookie 到配置文件"""
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        "cookie": cookie_string,
        "source": "auto_extract",
        "save_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nCookie 已保存到: {COOKIE_FILE}")


def verify_cookie(cookie_string):
    """验证 Cookie 是否有效"""
    import requests

    headers = {
        "Cookie": cookie_string,
        "Host": "mncg.10jqka.com.cn",
        "Origin": "https://mncg.10jqka.com.cn",
        "Referer": "https://mncg.10jqka.com.cn/cgiwt/index/index",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    url = "https://mncg.10jqka.com.cn/cgiwt/delegate/updateclass"
    data = {
        "type": "cmd_wt_mairu",
        "updateClass": "qryzijin|"
    }

    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        result = response.json()

        if str(result.get("errorcode", "")) == "0":
            kyje = result["result"]["qryzijin"]["result"]["data"]["kyje"]
            print(f"\nCookie 验证成功!")
            print(f"  可用资金: {kyje} 元")
            return True
        else:
            print(f"\nCookie 验证失败: {result.get('errormsg', '未知错误')}")
            return False

    except Exception as e:
        print(f"\n验证请求失败: {e}")
        return False


def manual_save(cookie_string):
    """手动保存 Cookie"""
    print("=" * 50)
    print("手动保存 Cookie")
    print("=" * 50)

    print("\n正在验证 Cookie...")
    if verify_cookie(cookie_string):
        save_cookie(cookie_string)
        print("\n系统已准备好交易!")
    else:
        print("\nCookie 验证失败，请检查 Cookie 是否正确")


def main():
    print("=" * 50)
    print("同花顺模拟比赛 Cookie 获取工具")
    print("=" * 50)

    cookie_string = get_all_cookies()

    if not cookie_string:
        print("\n" + "=" * 50)
        print("自动获取失败！请使用手动方式:")
        print("=" * 50)
        print("""
方法一（推荐）: 关闭 Chrome 后重试
  1. 完全关闭 Chrome 浏览器
  2. 运行: python get_cookie.py
  3. 重新打开 Chrome

方法二: 手动复制 Cookie
  1. 打开 https://mncg.10jqka.com.cn/ 并登录
  2. 按 F12 → 点击 Network 标签
  3. 刷新页面，找到任意发往 mncg.10jqka.com.cn 的请求
  4. 点击该请求 → 找到 Request Headers → Cookie
  5. 复制整行 Cookie 值
  6. 运行: python get_cookie.py --manual '粘贴的cookie'

方法三: 使用 Edge 浏览器
  1. 用 Edge 打开 https://mncg.10jqka.com.cn/ 并登录
  2. 运行: python get_cookie.py
""")
        return

    print("\n正在验证 Cookie...")
    if verify_cookie(cookie_string):
        save_cookie(cookie_string)
        print("\n系统已准备好交易!")
    else:
        print("\n获取的 Cookie 无效，请重新登录")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--manual":
        if len(sys.argv) > 2:
            manual_save(sys.argv[2])
        else:
            print("用法: python get_cookie.py --manual '你的cookie字符串'")
    else:
        main()
