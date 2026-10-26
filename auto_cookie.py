"""
自动获取同花顺模拟炒股Cookie
使用Playwright浏览器自动化
"""

import json
import time
from pathlib import Path
from datetime import datetime

# Cookie存储路径
COOKIE_DIR = Path(__file__).parent / "config"
COOKIE_FILE = COOKIE_DIR / "ths_cookie.json"


def get_cookie_auto():
    """自动获取Cookie"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("请先安装 playwright: pip install playwright")
        print("然后运行: playwright install chromium")
        return None

    print("=" * 50)
    print("同花顺模拟炒股 - 自动获取Cookie")
    print("=" * 50)
    print("将打开浏览器，请手动登录同花顺账户")
    print("登录成功后，按回车键继续...")
    print("=" * 50)

    with sync_playwright() as p:
        # 启动浏览器（非无头模式，让用户看到界面）
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # 打开同花顺模拟炒股
        page.goto("https://moni.10jqka.com.cn/")
        print("\n浏览器已打开，请登录同花顺账户...")

        # 等待用户登录
        input("\n登录成功后，按回车键获取Cookie...")

        # 获取当前页面的所有Cookie
        cookies = context.cookies()

        # 筛选同花顺相关的Cookie
        ths_cookies = []
        for cookie in cookies:
            if '10jqka' in cookie.get('domain', '') or 'ths' in cookie.get('domain', ''):
                ths_cookies.append(cookie)

        if not ths_cookies:
            # 如果没有找到特定域名的Cookie，获取所有Cookie
            ths_cookies = cookies

        # 转换为字符串格式
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in ths_cookies])

        if cookie_str:
            # 保存Cookie
            COOKIE_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "cookie": cookie_str,
                "cookies_detail": ths_cookies,
                "save_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print("\n" + "=" * 50)
            print("Cookie获取成功！")
            print(f"保存位置: {COOKIE_FILE}")
            print(f"Cookie长度: {len(cookie_str)} 字符")
            print("=" * 50)

            browser.close()
            return cookie_str
        else:
            print("\n未获取到Cookie，请确保已登录同花顺账户")
            browser.close()
            return None


if __name__ == "__main__":
    cookie = get_cookie_auto()
    if cookie:
        print("\nCookie已保存，可以在Web界面使用了！")
