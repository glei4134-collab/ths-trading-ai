import time
from fast_data import fetch_realtime_single, fetch_tencent_realtime, fetch_eastmoney_kline, fetch_sina_kline
from execution_optimizer import fetch_orderbook_depth

# 1. 腾讯实时报价 (单股)
t0 = time.time()
r_single = fetch_realtime_single("000001")
lat_tencent_single = (time.time() - t0) * 1000

# 2. 腾讯实时报价 (40只核心池批量请求)
syms = ["000001", "600519", "300750", "300502", "600900", "002466", "603799", "002149", "601919", "600118"] * 4
t0 = time.time()
r_batch = fetch_tencent_realtime(syms)
lat_tencent_batch = (time.time() - t0) * 1000

# 3. 盘口五档与深度 (Level-1 / 五档盘口 + VOI)
t0 = time.time()
depth = fetch_orderbook_depth("000001")
lat_depth = (time.time() - t0) * 1000

# 4. 东财日K线
t0 = time.time()
kline = fetch_eastmoney_kline("000001", period="daily", days=60)
lat_kline = (time.time() - t0) * 1000

# 5. 新浪K线
t0 = time.time()
sina_kline = fetch_sina_kline("000001", period="daily", days=60)
lat_sina = (time.time() - t0) * 1000

print(f"LATENCY_TENCENT_SINGLE_MS={lat_tencent_single:.2f}")
print(f"LATENCY_TENCENT_BATCH_40_MS={lat_tencent_batch:.2f}")
print(f"LATENCY_ORDERBOOK_DEPTH_MS={lat_depth:.2f}")
print(f"LATENCY_EASTMONEY_KLINE_MS={lat_kline:.2f}")
print(f"LATENCY_SINA_KLINE_MS={lat_sina:.2f}")
