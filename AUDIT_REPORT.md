# AI 自动交易系统全面审计报告

> 审计时间: 2026-05-03 | 审计范围: src/ 全部交易相关模块 + api.py + static/index.html

---

## 一、已修复的 Bug（本次会话）

### BUG-0: `_AUTO_TRADE_SYSTEM_PROMPT` 未定义 [Critical ✅ 已修复]
- **文件**: `src/ai_scheduler.py:161`
- **问题**: 引用 `_AUTO_TRADE_SYSTEM_PROMPT` 但从未定义。每次 `_run_analysis()` 都会 `NameError` 崩溃，调度器**从未成功执行过一次分析**
- **修复**: 在文件顶部添加了完整的系统提示定义

---

## 二、仍存在的 Critical 问题

### CRIT-1: 调度器交易绕过风控和持久化 [Critical]
- **文件**: `src/ai_scheduler.py:515` + `src/auto_trader.py:128`
- **问题**: `auto_trader.enabled` 默认 `False`。调度器的 `_execute_trades()` 在 `auto_trader.enabled=False` 时走 fallback 路径（直接调用 `conn.buy/sell()`），**完全跳过了**：
  - AutoTrader 风控检查（仓位限制、余额检查）
  - 手续费计算
  - 内存/SQLite 持仓更新
  - 策略信号记录（`strategy_tracker.record_signal()`）
  - 交易活动日志（`activity_log`）
- **影响**: 调度器执行的交易不会更新本地持仓数据，重启后持仓不一致
- **修复方案**:
  ```
  方案A（推荐）: 在 _execute_trades() 中，始终通过 auto_trader.execute_order() 执行
  前提: 启动时自动设置 auto_trader.enabled = True
  
  方案B: 在 fallback 路径中也加上持久化和信号记录
  ```

### CRIT-2: `_load_config()` 默认值与代码/JSON 不一致 [High]
- **文件**: `src/ai_scheduler.py:59-62`
- **问题**: `_load_config()` 的 fallback 值是旧版本的保守设置：
  ```python
  self.interval_minutes = cfg.get("interval_minutes", 15)  # 应为 10
  self.max_trades_per_day = cfg.get("max_trades_per_day", 5)  # 应为 999
  self.position_limit_pct = cfg.get("position_limit_pct", 0.3)  # 应为 1.0
  self.scan_top_n = cfg.get("scan_top_n", 10)  # 应为 30
  ```
- **影响**: 如果 JSON 配置文件损坏或不存在，会恢复到保守的旧默认值
- **修复**: 将 `_load_config` 的 fallback 值改为 `_DEFAULT_CONFIG` 中的值

### CRIT-3: 未成交委托检查调用两次 `get_orders()` [Medium]
- **文件**: `src/ai_scheduler.py:189` + `340`
- **问题**: `_handle_pending_orders()` 和 `_build_context()` 各调用一次 `conn.get_orders()`，浪费 API 请求且可能数据不一致
- **修复**: 在 `_handle_pending_orders()` 中缓存结果，`_build_context()` 直接使用缓存

---

## 三、策略系统集成缺口

### GAP-1: 调度器交易不记录策略信号 [High]
- **文件**: `src/ai_scheduler.py` `_execute_trades()`
- **问题**: 调度器做了 AI 决策并执行交易，但从未调用 `strategy_tracker.record_signal()`，导致：
  - 策略权重排行永远为空（"暂无历史数据"）
  - 策略系统无法学习和自我优化
- **修复**: 在 `_execute_trades()` 成功执行后，记录信号：
  ```python
  strategy_tracker.record_signal(
      strategy_name="ai_scheduler_autonomous",
      symbol=symbol, signal="BUY" if action=="buy" else "SELL",
      score=0, confidence=0.5, price=price
  )
  ```

### GAP-2: 策略评估未定时运行 [High]
- **文件**: `src/auto_trader.py:697` `evaluate_signals()`
- **问题**: `evaluate_signals()` 可以评估历史信号的准确性并更新策略表现数据，但**从未被任何地方调用**
- **修复**: 在调度器每个分析周期结束时，调用一次 `auto_trader.evaluate_signals()`

### GAP-3: AI Chat 手动对话无策略数据 [Medium]
- **文件**: `api.py` `_get_trading_context()`
- **问题**: 用户手动跟 AI 对话时，`_get_trading_context()` 只注入了持仓和资金，**没有策略信号和市场环境**
- **修复**: 在 `_get_trading_context()` 中增加市场环境和策略建议

---

## 四、交易安全缺口

### SAFE-1: 无整手验证 [Medium]
- **文件**: `src/ai_scheduler.py` `_parse_trade_tags()` + `_execute_trades()`
- **问题**: A 股要求买入 100 股整数倍（整手）。AI 可能生成 `[TRADE:BUY:600519:150]`，发送到 THS 会被拒绝
- **修复**: 在 `_execute_trades()` 中加入整手校验：
  ```python
  if action == "buy":
      amount = (amount // 100) * 100  # 强制取整到100股整数倍
  ```

### SAFE-2: 无重复交易冷却 [Medium]
- **文件**: `src/ai_scheduler.py`
- **问题**: 每 10 分钟一次分析，AI 可能对同一股票连续发出买入指令（尤其在股价持续上涨时）
- **修复**: 添加 `_recent_trades` 记录最近交易的股票代码，同一只股票在 30 分钟内不重复买入

### SAFE-3: 非交易时段买入委托检查 [Low]
- **文件**: `src/ai_scheduler.py` `_handle_pending_orders()`
- **问题**: 集合竞价时段（9:15-9:25）的委托可能在开盘瞬间被处理，但调度器在 `is_trading_hours()` 返回 False 时跳过
- **影响**: 较小，但 9:25 前的委托可能到 9:30 才成交

---

## 五、数据一致性缺口

### DATA-1: 重启后持仓不同步 [High]
- **文件**: `src/auto_trader.py`
- **问题**: AutoTrader 从本地 `data_store` 加载持仓，但 THS 账户可能有不同持仓。`sync_from_ths()` 已实现但**未被自动调用**
- **修复**: 在调度器启动时，调用一次 `auto_trader.sync_from_ths()`

### DATA-2: 调度器交易日志不持久 [Low]
- **文件**: `src/ai_scheduler.py`
- **问题**: `self._trade_log` 仅在内存中，重启后丢失
- **修复**: 每次追加日志后写入 `data/scheduler_trade_log.json`

---

## 六、性能优化建议

### PERF-1: 上下文过大风险 [Medium]
- **问题**: 扩展后的上下文（账户+市场+环境+未成交委托+3个排行榜+策略权重+策略目录）可能超过 4000 字符。使用小型模型时可能截断
- **建议**: 
  - 排行榜从 15/10/10 精简到 10/8/8
  - 策略权重只传 top 3 而非 top 5
  - 策略目录只传类别摘要，不传完整列表

### PERF-2: AI 调用频率优化 [Low]
- **问题**: 10 分钟间隔的 AI 调用中，很多上下文数据变化不大
- **建议**: 
  - 当市场状态和持仓无变化时，跳过本次 AI 分析
  - 添加"无变化跳过"逻辑

---

## 七、前端缺失功能

### UI-1: 未成交委托可视化 [Medium]
- **现状**: 调度器内部处理了未成交委托，但前端无展示
- **建议**: 在仪表盘新增"当日委托"区域，显示未成交/已成交/已撤单

### UI-2: 策略实时信号展示 [Low]
- **现状**: 前端有策略目录和表现排行页面，但不显示实时策略信号
- **建议**: 在 AI 分析完成后，显示各策略对当前持仓的信号方向

---

## 八、执行优先级排序

| 优先级 | 编号 | 任务 | 工作量 |
|--------|------|------|--------|
| P0 | CRIT-1 | 修复调度器绕过风控+持久化 | 小 |
| P0 | CRIT-2 | 修复 _load_config 默认值 | 极小 |
| P1 | GAP-1 | 调度器交易记录策略信号 | 小 |
| P1 | GAP-2 | 定时运行策略评估 | 小 |
| P1 | SAFE-1 | 整手验证 | 极小 |
| P1 | SAFE-2 | 重复交易冷却 | 小 |
| P1 | DATA-1 | 启动时同步 THS 持仓 | 小 |
| P2 | CRIT-3 | 去重 get_orders() 调用 | 极小 |
| P2 | GAP-3 | AI Chat 注入策略数据 | 中 |
| P2 | PERF-1 | 精简上下文大小 | 中 |
| P3 | DATA-2 | 交易日志持久化 | 极小 |
| P3 | PERF-2 | 无变化跳过分析 | 小 |
| P3 | UI-1 | 委托可视化 | 中 |
| P3 | UI-2 | 实时信号展示 | 中 |
