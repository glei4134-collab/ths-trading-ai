"""统一下单闸门单元测试。"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("ALLOW_INSECURE", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from order_guard import (
    TEAM_MAX_SINGLE_PCT,
    board_limit_pct,
    round_lot,
    validate_pre_trade,
)


def test_board_limits():
    assert board_limit_pct("600519") == 0.10
    assert board_limit_pct("000001") == 0.10
    assert board_limit_pct("300750") == 0.20
    assert board_limit_pct("688981") == 0.20


def test_round_lot():
    assert round_lot(150) == 100
    assert round_lot(99) == 0
    assert round_lot(200) == 200


def test_reject_odd_lot():
    r = validate_pre_trade("600519", "buy", 100, 50, skip_session_check=True)
    assert r.allowed is False
    assert r.code == "LOT_SIZE"


def test_clip_to_35_percent():
    r = validate_pre_trade(
        "600519",
        "buy",
        100.0,
        1000,
        total_assets=200000,
        available_cash=200000,
        existing_position_value=0,
        skip_session_check=True,
    )
    assert r.allowed is True
    assert r.amount * 100 <= 200000 * TEAM_MAX_SINGLE_PCT + 1e-6
    assert r.amount % 100 == 0


def test_reject_when_already_at_cap():
    r = validate_pre_trade(
        "600519",
        "buy",
        100.0,
        100,
        total_assets=200000,
        available_cash=200000,
        existing_position_value=70000,
        skip_session_check=True,
    )
    assert r.allowed is False
    assert r.code == "POSITION_CAP"


def test_limit_up_gem():
    r = validate_pre_trade(
        "300750",
        "buy",
        100.0,
        100,
        change_pct=19.96,
        skip_session_check=True,
        available_cash=50000,
        total_assets=200000,
    )
    assert r.allowed is False
    assert r.code == "LIMIT_UP"


def test_main_board_limit_still_blocks_10():
    r = validate_pre_trade(
        "600519",
        "buy",
        1800.0,
        100,
        change_pct=9.99,
        skip_session_check=True,
        available_cash=500000,
        total_assets=200000,
    )
    assert r.allowed is False
    assert r.code == "LIMIT_UP"


def test_t_plus_1():
    r = validate_pre_trade(
        "600519",
        "sell",
        1800.0,
        100,
        existing_shares=100,
        available_shares=0,
        skip_session_check=True,
    )
    assert r.allowed is False
    assert r.code == "T_PLUS_1"


def test_session_blocks_weekend():
    saturday = datetime(2026, 10, 31, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    r = validate_pre_trade("600519", "buy", 100, 100, now=saturday, available_cash=100000, total_assets=200000)
    assert r.allowed is False
    assert r.code == "OUT_OF_SESSION"


if __name__ == "__main__":
    import traceback
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("OK", name)
            except Exception:
                print("FAIL", name)
                traceback.print_exc()
