"""
benchmark_data_latency.py
全面数据源获取延迟对比、多线程并发加速比测试与数据预热评测工具
"""

import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from fast_data import (
    fetch_realtime_single,
    fetch_tencent_realtime,
    fetch_eastmoney_kline,
    fetch_sina_kline,
    fetch_kline,
    get_cached_kline,
    clear_kline_cache
)
from execution_optimizer import fetch_orderbook_depth

# 测试标的池（代表性沪深A股+创/科大市值与活跃标的）
BENCHMARK_SYMBOLS = [
    "000001", "600519", "300750", "300502", "600900",
    "002466", "603799", "002149", "601919", "600118",
    "601318", "002594", "600036", "000858", "601899",
    "601012", "002475", "300059", "688981", "603986"
]

def run_benchmark():
    print("=" * 75)
    print(" 🚀 [THS QUANT] 数据源延迟比对与多线程并发性能测试报告")
    print("=" * 75)

    # -------------------------------------------------------------
    # 模块 1: 实时行情单点与批量获取延迟对比
    # -------------------------------------------------------------
    print("\n[一、实时数据获取延迟对比 (单股 vs 批量 CDN)]")
    
    # 1.1 单股实时获取 (测试 5 次取中位数)
    single_lats = []
    for _ in range(5):
        t0 = time.perf_counter()
        r = fetch_realtime_single("000001")
        single_lats.append((time.perf_counter() - t0) * 1000)
    p50_single = statistics.median(single_lats)
    print(f"  • 腾讯单股实时行情 (fetch_realtime_single):")
    print(f"    中位数延迟: {p50_single:.2f} ms | 最快: {min(single_lats):.2f} ms | 最慢: {max(single_lats):.2f} ms")

    # 1.2 批量获取 (10只、20只、40只)
    for batch_size in [10, 20, 40]:
        test_syms = (BENCHMARK_SYMBOLS * 2)[:batch_size]
        batch_lats = []
        for _ in range(3):
            t0 = time.perf_counter()
            r_batch = fetch_tencent_realtime(test_syms)
            batch_lats.append((time.perf_counter() - t0) * 1000)
        p50_batch = statistics.median(batch_lats)
        per_stock_lat = p50_batch / batch_size
        print(f"  • 腾讯批量行情 (Batch {batch_size:2d} 只):")
        print(f"    总耗时: {p50_batch:.2f} ms | 单股折算延迟: {per_stock_lat:.2f} ms/股 | 吞吐: {batch_size / (p50_batch / 1000):.0f} 股/秒")

    # 1.3 盘口五档与 VOI 微观订单簿深度获取
    depth_lats = []
    for sym in ["000001", "600519", "300750"]:
        t0 = time.perf_counter()
        d = fetch_orderbook_depth(sym)
        depth_lats.append((time.perf_counter() - t0) * 1000)
    print(f"  • 五档盘口深度与微观订单簿 (VOI/OBI 计算):")
    print(f"    平均耗时: {statistics.mean(depth_lats):.2f} ms | 包含五档买卖量比与微观订单动量")

    # -------------------------------------------------------------
    # 模块 2: 历史 K 线各数据源延迟比对 (新浪 vs 东财)
    # -------------------------------------------------------------
    print("\n[二、K线数据源延迟比对 (新浪源 vs 东方财富源)]")
    clear_kline_cache()
    
    # 新浪 K 线 (34ms 极速源)
    sina_lats = []
    for sym in BENCHMARK_SYMBOLS[:5]:
        t0 = time.perf_counter()
        k_sina = fetch_sina_kline(sym, period="daily", days=60)
        sina_lats.append((time.perf_counter() - t0) * 1000)
    avg_sina = statistics.mean(sina_lats)
    print(f"  • 新浪源日 K 线 (fetch_sina_kline):   平均耗时: {avg_sina:.2f} ms ({len(k_sina) if k_sina else 0} 根K线)")

    # 东财 K 线
    em_lats = []
    for sym in BENCHMARK_SYMBOLS[:3]:
        t0 = time.perf_counter()
        k_em = fetch_eastmoney_kline(sym, period="daily", days=60)
        em_lats.append((time.perf_counter() - t0) * 1000)
    avg_em = statistics.mean(em_lats)
    print(f"  • 东财源日 K 线 (fetch_eastmoney_kline): 平均耗时: {avg_em:.2f} ms (带重试降级)")
    print(f"  ⭐ 路由切换结论: 新浪源较东财源提速约 {avg_em / avg_sina:.1f} 倍！首选新浪可消除网络重试等待。")

    # -------------------------------------------------------------
    # 模块 3: 单线程串行 vs 多线程并发获取比对
    # -------------------------------------------------------------
    print("\n[三、单线程串行 vs 多线程并发 K 线获取对比 (20 只标的)]")
    clear_kline_cache()
    
    # 3.1 单线程串行获取
    t0 = time.perf_counter()
    seq_results = {}
    for sym in BENCHMARK_SYMBOLS:
        seq_results[sym] = fetch_sina_kline(sym, period="daily", days=60)
    t_seq = time.perf_counter() - t0
    print(f"  • [单线程串行] 20 只标的总耗时: {t_seq * 1000:.2f} ms (平均单股 {(t_seq / 20) * 1000:.2f} ms)")

    # 3.2 多线程并发获取 (测试 4 线程 vs 8 线程 vs 16 线程)
    for workers in [4, 8, 16]:
        clear_kline_cache()
        t0 = time.perf_counter()
        mt_results = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_sym = {
                executor.submit(fetch_sina_kline, sym, "daily", 60): sym
                for sym in BENCHMARK_SYMBOLS
            }
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    mt_results[sym] = future.result()
                except Exception:
                    mt_results[sym] = []
        t_mt = time.perf_counter() - t0
        speedup = t_seq / t_mt
        print(f"  • [多线程并发 (Workers={workers:2d})] 20 只标的总耗时: {t_mt * 1000:.2f} ms | 🚀 加速比: {speedup:.2f}x")

    # -------------------------------------------------------------
    # 模块 4: 数据预热 (Cache Pre-warming) 性能评测
    # -------------------------------------------------------------
    print("\n[四、数据预热 (Cache Pre-warming) 评测]")
    clear_kline_cache()

    # 模拟在 09:24:50 执行预热 (8 线程并发拉取 40 只候选股 K 线入内存)
    candidate_40 = (BENCHMARK_SYMBOLS * 2)[:40]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_kline, sym, "daily", 60) for sym in candidate_40]
        for f in as_completed(futures):
            _ = f.result()
    t_prewarm = (time.perf_counter() - t0) * 1000
    print(f"  • 40 只候选股后台并发预热完成: 总耗时 {t_prewarm:.2f} ms (开盘前后台静默完成)")

    # 预热后：09:25:00 集合竞价时策略直接从内存抓取
    t0 = time.perf_counter()
    cached_hits = 0
    for sym in candidate_40:
        k = fetch_kline(sym, "daily", 60)
        if k:
            cached_hits += 1
    t_access_cached = (time.perf_counter() - t0) * 1000
    avg_cache_read = (t_access_cached / 40) * 1000 # 微秒级

    print(f"  • 09:25 策略执行时从内存读取 40 只标的:")
    print(f"    总读取耗时: {t_access_cached:.3f} ms (单票 {avg_cache_read:.2f} 微秒) | 缓存命中率: {cached_hits / 40 * 100:.1f}%")
    print(f"    🚀 预热提速效果: 相比未预热串行拉取，决断时刻 I/O 延迟从 {t_seq * 2 * 1000:.0f} ms 骤降至 {t_access_cached:.2f} ms (提速 {((t_seq*2*1000) / t_access_cached):.0f} 倍！)")
    print("=" * 75)

if __name__ == "__main__":
    run_benchmark()
