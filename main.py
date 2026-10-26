"""
同花顺模拟比赛AI交易系统
主程序入口
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# 添加src目录到Python路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import config_manager
from server import mcp
from strategy import TradingEngine
from broker import THSBroker, AutoTrader


def setup_logging():
    """配置日志"""
    config = config_manager.config
    log_level = getattr(logging, config.mcp.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('trading.log', encoding='utf-8')
        ]
    )


def print_banner():
    """打印启动横幅"""
    banner = """
╔════════════════════════════════════════════════════════════╗
║           同花顺模拟比赛AI交易系统 v1.0                   ║
║        基于MCP协议的智能股票交易助手                      ║
╠════════════════════════════════════════════════════════════╣
║  功能: 行情查询 | 策略分析 | 自动交易 | 风险控制          ║
║  策略: 技术分析 | 动量策略 | 价值投资                      ║
╚════════════════════════════════════════════════════════════╝
"""
    print(banner)


def show_menu():
    """显示主菜单"""
    print("\n" + "=" * 50)
    print("主菜单")
    print("=" * 50)
    print("1. 启动MCP Server（供Claude调用）")
    print("2. 直接分析股票")
    print("3. 查看当前持仓")
    print("4. 查看账户余额")
    print("5. 查看交易日志")
    print("6. 修改配置")
    print("7. 连接同花顺客户端")
    print("0. 退出系统")
    print("=" * 50)


def analyze_stock_direct():
    """直接分析股票"""
    symbol = input("\n请输入股票代码（如 000001）: ").strip()
    if not symbol:
        print("股票代码不能为空")
        return

    print(f"\n正在分析 {symbol} ...")
    engine = TradingEngine()

    try:
        result = engine.analyze_stock(symbol)

        print("\n" + "=" * 50)
        print(f"分析结果: {symbol}")
        print("=" * 50)
        print(f"综合信号: {result['signal']}")
        print(f"综合评分: {result['score']}")
        print("\n触发信号:")
        for signal in result.get('signals', []):
            print(f"  - {signal}")

        print("\n策略详情:")
        for strategy_name, strategy_result in result.get('strategy_results', {}).items():
            print(f"\n  [{strategy_name}]")
            print(f"    评分: {strategy_result.get('score', 0)}")
            for sig in strategy_result.get('signals', []):
                print(f"    - {sig}")

        print("=" * 50)

    except Exception as e:
        print(f"分析失败: {e}")


def show_positions():
    """显示持仓"""
    broker = THSBroker(mode="simulation")
    positions = broker.get_positions()

    print("\n" + "=" * 60)
    print("当前持仓")
    print("=" * 60)

    if not positions:
        print("暂无持仓")
    else:
        print(f"{'股票代码':<10} {'股票名称':<10} {'持仓数量':<10} {'成本价':<10} {'当前价':<10} {'盈亏':<10}")
        print("-" * 60)
        for pos in positions:
            code = pos.get('证券代码', '')
            name = pos.get('股票名称', '')
            amount = pos.get('持仓数量', 0)
            cost = pos.get('成本价', 0)
            current = pos.get('当前价', 0)
            profit = (current - cost) * amount if current and cost else 0

            print(f"{code:<10} {name:<10} {amount:<10} {cost:<10.2f} {current:<10.2f} {profit:<10.2f}")

    print("=" * 60)


def show_balance():
    """显示余额"""
    broker = THSBroker(mode="simulation")
    balance = broker.get_balance()

    print("\n" + "=" * 40)
    print("账户余额")
    print("=" * 40)
    print(f"资产金额: {balance.get('资产金额', 0):,.2f} 元")
    print(f"可用金额: {balance.get('可用金额', 0):,.2f} 元")
    print(f"冻结金额: {balance.get('冻结金额', 0):,.2f} 元")
    print(f"持仓市值: {balance.get('市值', 0):,.2f} 元")
    print("=" * 40)


def show_trade_log():
    """显示交易日志"""
    log_file = Path("trading.log")
    if not log_file.exists():
        print("\n暂无交易日志")
        return

    print("\n" + "=" * 60)
    print("最近交易日志")
    print("=" * 60)

    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        # 显示最后20行
        for line in lines[-20:]:
            print(line.strip())

    print("=" * 60)


def modify_config():
    """修改配置"""
    print("\n" + "=" * 40)
    print("配置修改")
    print("=" * 40)
    print("1. 交易参数")
    print("2. 策略参数")
    print("3. 券商配置")
    print("4. MCP Server配置")
    print("0. 返回")
    print("=" * 40)

    choice = input("请选择: ").strip()

    if choice == "1":
        print("\n交易参数:")
        balance = input(f"初始资金 [{config_manager.config.trading.initial_balance}]: ").strip()
        if balance:
            config_manager.update('trading', initial_balance=float(balance))
            print("已更新")
    elif choice == "2":
        print("\n策略参数:")
        tw = input(f"技术分析权重 [{config_manager.config.strategy.technical_weight}]: ").strip()
        if tw:
            config_manager.update('strategy', technical_weight=float(tw))
            print("已更新")
    elif choice == "3":
        print("\n券商配置:")
        mode = input(f"交易模式 [{config_manager.config.broker.mode}] (simulation/real): ").strip()
        if mode:
            config_manager.update('broker', mode=mode)
            print("已更新")
    elif choice == "4":
        print("\nMCP Server配置:")
        port = input(f"端口 [{config_manager.config.mcp.port}]: ").strip()
        if port:
            config_manager.update('mcp', port=int(port))
            print("已更新")


def connect_broker():
    """连接同花顺客户端"""
    print("\n" + "=" * 40)
    print("连接同花顺客户端")
    print("=" * 40)
    print("注意: 需要先启动同花顺交易客户端")
    print()

    exe_path = input("同花顺客户端路径: ").strip()
    user = input("用户名: ").strip()
    password = input("密码: ").strip()

    if not exe_path:
        print("客户端路径不能为空")
        return

    broker = THSBroker(mode="real")
    success = broker.connect(exe_path, user, password)

    if success:
        print("连接成功!")
    else:
        print("连接失败，请检查路径和账号密码")


def run_mcp_server():
    """运行MCP Server"""
    config = config_manager.config.mcp
    print(f"\n启动MCP Server...")
    print(f"地址: {config.host}:{config.port}")
    print("等待Claude连接...")
    print("按 Ctrl+C 停止服务器")

    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("\n服务器已停止")


def main():
    """主函数"""
    setup_logging()
    print_banner()

    while True:
        show_menu()
        choice = input("\n请选择操作: ").strip()

        if choice == "1":
            run_mcp_server()
        elif choice == "2":
            analyze_stock_direct()
        elif choice == "3":
            show_positions()
        elif choice == "4":
            show_balance()
        elif choice == "5":
            show_trade_log()
        elif choice == "6":
            modify_config()
        elif choice == "7":
            connect_broker()
        elif choice == "0":
            print("\n感谢使用，再见!")
            sys.exit(0)
        else:
            print("\n无效选择，请重新输入")


if __name__ == "__main__":
    main()
