#!/usr/bin/env python3
"""
交易时段发布拦截熔断门禁 (Trading Hours Release Gatekeeper)
A股交易时段（工作日 09:15-11:30, 13:00-15:05）严禁任何停机重启或破坏性更替。
版本发布与更新自动识别敏感交易时段并拦截，推迟至 15:05 盘后平滑更替。
"""

import sys
import time
import argparse
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

# 确保能引用项目根目录模块
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

logger = logging.getLogger("ReleaseGatekeeper")


class ReleaseGatekeeper:
    """发布门禁核心逻辑"""

    # 保护时段设定 (Asia/Shanghai)
    MORNING_START = (9, 15, 0)   # 09:15 集合竞价盘前
    MORNING_END   = (11, 30, 0)  # 11:30 早盘收盘
    AFTERNOON_START = (13, 0, 0) # 13:00 午盘开盘
    AFTERNOON_END   = (15, 5, 0) # 15:05 收盘结算缓冲期完成

    @classmethod
    def get_shanghai_now(cls) -> datetime:
        return datetime.now(ZoneInfo("Asia/Shanghai"))

    @classmethod
    def is_trading_day(cls, target_date: Optional[date] = None) -> bool:
        """判断是否为A股交易日"""
        if target_date is None:
            target_date = cls.get_shanghai_now().date()

        # 周末排除
        if target_date.weekday() >= 5:
            return False

        try:
            from competition import competition_manager
            return competition_manager.is_trading_day(target_date)
        except Exception:
            # 回退规则：默认工作日为交易日
            return True

    @classmethod
    def check_release_window(cls, now: Optional[datetime] = None) -> Tuple[bool, str, int]:
        """
        检查当前是否被门禁拦截
        返回: (is_blocked: bool, reason: str, wait_seconds: int)
        """
        if now is None:
            now = cls.get_shanghai_now()

        # 1. 检查是否为交易日
        if not cls.is_trading_day(now.date()):
            return False, "当前非交易日 (周末或法定节假日)，允许执行发布部署", 0

        curr_time = (now.hour, now.minute, now.second)

        # 2. 检查早盘时段 (09:15 - 11:30)
        in_morning = (cls.MORNING_START <= curr_time <= cls.MORNING_END)
        # 3. 检查午盘至收盘时段 (13:00 - 15:05)
        in_afternoon = (cls.AFTERNOON_START <= curr_time <= cls.AFTERNOON_END)

        if in_morning or in_afternoon:
            # 计算距离今日 15:05:00 的剩余秒数
            target_release = now.replace(hour=cls.AFTERNOON_END[0],
                                         minute=cls.AFTERNOON_END[1],
                                         second=cls.AFTERNOON_END[2],
                                         microsecond=0)
            if target_release > now:
                wait_sec = int((target_release - now).total_seconds())
            else:
                wait_sec = 0

            session_name = "早盘竞价与连续交易敏感期 (09:15-11:30)" if in_morning else "午后连续交易与收盘清算敏感期 (13:00-15:05)"
            reason = (f"当前正值A股实盘交易时段 [{session_name}]，"
                      f"为保障实盘报单连续性与资金安全，已触发发布拦截熔断门禁！"
                      f"预计在 15:05 盘后自动解锁（剩余 {wait_sec} 秒 / {wait_sec // 60} 分钟）。")
            return True, reason, wait_sec

        return False, "当前处于盘后安全维护窗口 (15:05 之后至次日 09:15 之前或午间休市)，允许执行平滑发布", 0

    @classmethod
    def audit_log(cls, action: str, details: Dict[str, Any]):
        """记录发布审计日志"""
        try:
            from paths import DATA_DIR
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            log_file = DATA_DIR / "deploy_gate.log"
            record = {
                "timestamp": cls.get_shanghai_now().strftime("%Y-%m-%d %H:%M:%S"),
                "action": action,
                **details
            }
            import json
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="交易时段发布拦截熔断门禁 (Trading Hours Release Gatekeeper)")
    parser.add_argument("--action", choices=["check", "wait", "status"], default="check",
                        help="check: 检查并以退出码阻断; wait: 若遇交易时段则自动挂起倒计时至15:05平滑更替; status: 输出状态JSON")
    parser.add_argument("--force", action="store_true", help="【紧急特权】强制跳过交易时段门禁拦截 (记录审计日志)")
    parser.add_argument("--reason", type=str, default="", help="强制发布时的原因说明")

    args = parser.parse_args()

    # 1. 紧急特权通道处理
    if args.force:
        print("🚨 [WARNING] 检测到 --force 紧急特权发布指令！已绕过交易时段门禁。")
        ReleaseGatekeeper.audit_log("EMERGENCY_BYPASS", {
            "status": "bypassed",
            "reason": args.reason or "Emergency hotfix by operator",
            "operator_time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        })
        sys.exit(0)

    # 2. 正常门禁检查
    is_blocked, reason, wait_sec = ReleaseGatekeeper.check_release_window()

    if args.action == "status":
        import json
        res = {
            "is_blocked": is_blocked,
            "reason": reason,
            "wait_seconds": wait_sec,
            "server_time": ReleaseGatekeeper.get_shanghai_now().strftime("%Y-%m-%d %H:%M:%S")
        }
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0)

    if not is_blocked:
        print(f"✅ [Gatekeeper Passed] {reason}")
        ReleaseGatekeeper.audit_log("RELEASE_ALLOWED", {"reason": reason})
        sys.exit(0)

    # 遇到交易时段被阻断
    print("=" * 70)
    print("🛑 [Release Gatekeeper 交易时段发布熔断门禁已触发]")
    print(f"   -> 原因: {reason}")
    print("=" * 70)

    if args.action == "check":
        ReleaseGatekeeper.audit_log("RELEASE_BLOCKED", {"reason": reason, "wait_seconds": wait_sec})
        print("❌ 发布被拦截中断。若确需紧急热更，请添加 --force 参数。")
        sys.exit(2)

    elif args.action == "wait":
        ReleaseGatekeeper.audit_log("RELEASE_SUSPENDED_WAITING", {"reason": reason, "wait_seconds": wait_sec})
        print(f"⏳ 发布进程自动挂起中，将在 15:05 盘后安全时间窗口自动恢复发布...")
        while True:
            is_blocked, curr_reason, rem_sec = ReleaseGatekeeper.check_release_window()
            if not is_blocked or rem_sec <= 0:
                print("\n🔔 15:05 盘后安全窗口已到达！门禁自动解锁，继续执行发布流程...")
                ReleaseGatekeeper.audit_log("RELEASE_RESUMED", {"reason": "15:05 post-market reached"})
                sys.exit(0)
            
            mins = rem_sec // 60
            secs = rem_sec % 60
            print(f"\r⏳ [挂起等待中] 距离盘后 15:05 解锁还有: {mins}分{secs}秒... ", end="", flush=True)
            sleep_step = min(30, rem_sec) if rem_sec > 5 else 1
            time.sleep(sleep_step)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
