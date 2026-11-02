# -*- coding: utf-8 -*-
"""
Stage 3 单元测试：高可用容灾、无锁环形队列与原子快照自愈引擎
"""
import os
import sys
import time
import json
import sqlite3
import tempfile
import threading
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 设置模块检索路径
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from reliability.crash_recovery import (
    LockFreeRingBuffer,
    AtomicSnapshotRecovery,
    trigger_sqlite_wal_checkpoint,
    WalCheckpointSynchronizer,
    WalCheckpointSync
)


def test_lock_free_ring_buffer_basic():
    """测试 1: LockFreeRingBuffer 容量对齐、单调序号与基础读写"""
    rb = LockFreeRingBuffer(capacity=10)
    # 容量应自动向上对齐为 2 的幂次 (10 -> 16)
    assert rb.capacity == 16, f"预期容量 16，实际 {rb.capacity}"
    assert rb.cursor == -1, f"初始游标应为 -1，实际 {rb.cursor}"
    assert rb.size == 0

    # 写入 5 个事件
    seqs = rb.publish_many([{"id": i, "val": f"event_{i}"} for i in range(5)])
    assert seqs == [0, 1, 2, 3, 4], f"序列号分配错误: {seqs}"
    assert rb.cursor == 4
    assert rb.size == 5

    # 读取自 seq=1 之后的所有事件 (应返回 2, 3, 4)
    events, cur = rb.read_since(1)
    assert len(events) == 3, f"读取数量错误: {len(events)}"
    assert events[0]["id"] == 2
    assert events[-1]["id"] == 4
    assert cur == 4

    # 测试 get_latest_n
    latest_3 = rb.get_latest_n(3)
    assert len(latest_3) == 3
    assert [e["id"] for e in latest_3] == [2, 3, 4]


def test_lock_free_ring_buffer_wrap_around():
    """测试 2: 环形缓冲区满圈回绕覆盖与最旧可用槽位自对齐"""
    rb = LockFreeRingBuffer(capacity=8)  # 容量 = 8
    assert rb.capacity == 8

    # 写入 20 个事件 (超过容量 8)
    for i in range(20):
        rb.publish({"seq": i})

    assert rb.cursor == 19
    assert rb.size == 8

    # 消费者从很久之前的 seq=2 开始拉取，由于缓冲区仅保留最近 8 条 (12..19)
    # 读端应自动对齐到最早可用槽位 12，不会崩溃或拉取不存在的数据
    events, cur = rb.read_since(2)
    assert len(events) == 8, f"回绕后可用事件数应为 8，实际: {len(events)}"
    assert events[0]["seq"] == 12
    assert events[-1]["seq"] == 19
    assert cur == 19


def test_lock_free_ring_buffer_concurrency():
    """测试 3: 单写多读并发压力测试 (无死锁、无字典并发修改异常)"""
    rb = LockFreeRingBuffer(capacity=256)
    stop_event = threading.Event()
    read_counts = [0, 0, 0]
    errors = []

    def writer_thread():
        try:
            for i in range(1000):
                rb.publish({"step": i, "data": "payload"})
                time.sleep(0.0001)
        except Exception as e:
            errors.append(f"Writer error: {e}")
        finally:
            stop_event.set()

    def reader_thread(reader_idx):
        last_seq = -1
        try:
            while not stop_event.is_set() or last_seq < rb.cursor:
                evs, cur = rb.read_since(last_seq)
                if evs:
                    read_counts[reader_idx] += len(evs)
                    last_seq = cur
                time.sleep(0.0002)
        except Exception as e:
            errors.append(f"Reader {reader_idx} error: {e}")

    threads = [
        threading.Thread(target=writer_thread),
        threading.Thread(target=reader_thread, args=(0,)),
        threading.Thread(target=reader_thread, args=(1,)),
        threading.Thread(target=reader_thread, args=(2,)),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"并发读写异常: {errors}"
    assert rb.cursor == 999, f"写端最终序列应为 999，实际: {rb.cursor}"
    for idx, c in enumerate(read_counts):
        assert c > 0, f"读者 {idx} 读取计数应大于 0，实际: {c}"


def test_atomic_snapshot_recovery_integrity():
    """测试 4: 原子状态快照保存、SHA-256 完整性验证与篡改自动回退 .bak"""
    with tempfile.TemporaryDirectory() as tmpdir:
        snap_path = os.path.join(tmpdir, "trading_state.json")
        recovery = AtomicSnapshotRecovery(snap_path)

        # 初始无快照
        state, source = recovery.load_snapshot()
        assert state is None
        assert source == "NO_VALID_SNAPSHOT"

        # 1. 保存第一份快照 (V1)
        v1_data = {"version": 1, "cash": 50000.0, "positions": {"000001": 1000}}
        saved = recovery.save_snapshot(v1_data, last_seq=10)
        assert saved is True
        assert os.path.exists(snap_path)

        loaded_v1, src1 = recovery.load_snapshot()
        assert src1 == "PRIMARY_OK"
        assert loaded_v1["cash"] == 50000.0
        assert loaded_v1["_last_seq"] == 10

        # 2. 保存第二份快照 (V2)，此时 V1 会自动转移至 .bak 副本
        v2_data = {"version": 2, "cash": 80000.0, "positions": {"600519": 200}}
        saved_v2 = recovery.save_snapshot(v2_data, last_seq=20)
        assert saved_v2 is True
        assert os.path.exists(recovery.backup_path)

        loaded_v2, src2 = recovery.load_snapshot()
        assert src2 == "PRIMARY_OK"
        assert loaded_v2["version"] == 2
        assert loaded_v2["cash"] == 80000.0

        # 3. 模拟异常故障：主快照文件遭遇静默数据损坏 (篡改其中一个字节)
        with open(snap_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
        # 破坏内容使 sha256 校验不通过
        corrupted_content = raw_content.replace("80000.0", "999999.0")
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write(corrupted_content)

        # 4. 加载快照：应能检测出 SHA-256 摘要不匹配，并无缝自动回退到 .bak 备份快照 (V1)
        fallback_state, src_fallback = recovery.load_snapshot()
        assert src_fallback == "RECOVERED_FROM_BACKUP", f"未能从备份自愈恢复: {src_fallback}"
        assert fallback_state["version"] == 1
        assert fallback_state["cash"] == 50000.0


def test_crash_recovery_with_replay_and_sla():
    """测试 5: 崩溃自愈重放全链路与 < 500ms SLA 达标验证"""
    with tempfile.TemporaryDirectory() as tmpdir:
        snap_path = os.path.join(tmpdir, "portfolio_snapshot.json")
        recovery = AtomicSnapshotRecovery(snap_path)
        rb = LockFreeRingBuffer(capacity=512)

        # 1. 模拟系统盘中定期快照 (at seq 50)
        initial_state = {
            "account_id": "ACC_001",
            "cash": 100000.0,
            "positions": {"600519": 500},
            "active_orders": ["ORD_001"]
        }
        recovery.save_snapshot(initial_state, last_seq=50)

        # 2. 模拟快照之后新产生但尚未写入磁盘快照的实时事件 (51..60)
        def mock_apply_event(state: dict, event: dict):
            ev_type = event.get("type")
            if ev_type == "BUY_FILL":
                cost = event["price"] * event["volume"]
                state["cash"] -= cost
                sym = event["symbol"]
                state["positions"][sym] = state["positions"].get(sym, 0) + event["volume"]
            elif ev_type == "ORDER_CANCEL":
                ord_id = event["order_id"]
                if ord_id in state["active_orders"]:
                    state["active_orders"].remove(ord_id)

        # 发布 50 个普通事件以填充序号到 50
        for _ in range(51):
            rb.publish({"type": "NOOP"})

        # 发布快照之后的增量业务事件
        rb.publish({"type": "BUY_FILL", "symbol": "000001", "price": 10.0, "volume": 2000})  # 消耗 20000 现金
        rb.publish({"type": "ORDER_CANCEL", "order_id": "ORD_001"})

        # 3. 执行崩溃恢复与增量重放
        result = recovery.recover_with_replay(ring_buffer=rb, event_apply_fn=mock_apply_event)

        assert result["success"] is True
        assert result["replayed_events_count"] == 2, f"增量重放事件数错误: {result['replayed_events_count']}"
        # 验证 500ms SLA
        assert result["recovery_time_ms"] < 500.0, f"恢复耗时超时 SLA 500ms: {result['recovery_time_ms']}ms"

        # 验证最终账本状态 100% 还原
        final_state = result["state"]
        assert final_state["cash"] == 80000.0, f"现金计算偏差: {final_state['cash']}"
        assert final_state["positions"]["000001"] == 2000, "持仓增量重放偏差"
        assert "ORD_001" not in final_state["active_orders"], "撤单状态未正确重放"


def test_sqlite_wal_checkpoint_synchronizer():
    """测试 6: SQLite WAL 检查点触发与 WalCheckpointSynchronizer"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_trading.db")

        # 1. 初始化 WAL 模式数据库并写入数据
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE test_orders (id INTEGER PRIMARY KEY, symbol TEXT, amount REAL);")
        for i in range(200):
            conn.execute("INSERT INTO test_orders (symbol, amount) VALUES (?, ?);", (f"sym_{i}", i * 100.0))
        conn.commit()
        conn.close()

        # 2. 直接触发 trigger_sqlite_wal_checkpoint
        res_passive = trigger_sqlite_wal_checkpoint(db_path, mode="PASSIVE")
        assert res_passive["ok"] is True
        assert res_passive["mode"] == "PASSIVE"

        res_truncate = trigger_sqlite_wal_checkpoint(db_path, mode="TRUNCATE")
        assert res_truncate["ok"] is True
        assert res_truncate["mode"] == "TRUNCATE"

        # 3. 测试 WalCheckpointSynchronizer
        sync = WalCheckpointSynchronizer(db_path, default_mode="PASSIVE", wal_size_threshold_mb=0.0001)
        res_sync = sync.checkpoint()
        assert res_sync["ok"] is True

        # 测试自动截断
        auto_res = sync.auto_sync_if_exceeded()
        # 由于写入了数据且阈值设置极低，应触发自动 TRUNCATE
        if auto_res is not None:
            assert auto_res["ok"] is True

        # 4. 测试无效数据库路径处理
        invalid_res = trigger_sqlite_wal_checkpoint("/non/existent/path/db.db")
        assert invalid_res["ok"] is False
        assert invalid_res["reason"] == "db_file_not_found"


import unittest


class TestCrashRecoveryStage3(unittest.TestCase):
    def test_1_lock_free_ring_buffer_basic(self):
        test_lock_free_ring_buffer_basic()

    def test_2_lock_free_ring_buffer_wrap_around(self):
        test_lock_free_ring_buffer_wrap_around()

    def test_3_lock_free_ring_buffer_concurrency(self):
        test_lock_free_ring_buffer_concurrency()

    def test_4_atomic_snapshot_recovery_integrity(self):
        test_atomic_snapshot_recovery_integrity()

    def test_5_crash_recovery_with_replay_and_sla(self):
        test_crash_recovery_with_replay_and_sla()

    def test_6_sqlite_wal_checkpoint_synchronizer(self):
        test_sqlite_wal_checkpoint_synchronizer()


if __name__ == "__main__":
    unittest.main()
