# THS Trading AI - 项目记忆与工程交接档案

## 最后更新: 2026-10-30 (v2.5 团队赛多账户全自动座舱版)

---

## 零、最新交接核心纪要 (2026-10-30 Handover)

### 1. 生产环境与运维铁律
- **生产云服务器**: 
oot@81.70.51.173（SSH 密钥：~/.ssh/id_ed25519）
- **云端部署路径**: /opt/ths-trading-ai
- **Systemd 守护服务**: 	hs-trading.service（端口 5000，开机自启，自动重启）
  - 状态检查：systemctl status ths-trading
  - 重启服务：systemctl restart ths-trading
  - 实时日志：journalctl -u ths-trading -f
- **【绝对铁律】**: **严禁在本地电脑启动或常驻运行 python api.py / desktop_app.py（本地 5000 端口必须保持关闭）**，防止本地进程与云服务器同时轮询同花顺接口导致 Cookie 互踢失效或重复下单。本地仓库仅用于代码开发、Git 版本管理与向云服务器 scp 部署。

### 2. 多账户管理与数据隔离机制 (src/db_store.py, src/account_manager.py, pi.py)
- **账号自动编号**: 所有绑定的同花顺比赛账号按存入 Cookie 的先后顺序（数据库 ccounts.id 升序）自动命名为 **1、2、3...**（由 db_store.resequence_account_names() 在新增、删除及启动时自动维护）。
- **当前在库账号**（云端 /opt/ths-trading-ai/data/trading.db）：
  - 账号 **1** (id=8)、账号 **2** (id=9)、账号 **3** (id=10)。
- **开户本金与盈亏算法硬约束**:
  - 同花顺模拟比赛每个账号标准初始本金硬性锁定为 **200,000.0 元（20万）**，严禁将绑定时的剩余可用现金（ky_fund / zjye）误写入 initial_balance。
  - **累计盈亏 (	otal_profit)** = 	otal_assets - 200000.0；**累计收益率 (	otal_return)** = 	otal_profit / 200000.0 * 100%。
  - **今日盈亏 (	oday_profit)**: 由 db_store.get_today_pnl(account_id, current_total) 基于昨日收盘快照（若无则取今日首个快照 aseline_assets）独立计算，与累计盈亏彻底解耦。
- **数据隔离与共享边界**:
  - **完全隔离**: ccounts、positions（主键 ccount_id, symbol）、	rades、signals、ccount_snapshots 均按 ccount_id 严格隔离；每次同步及每小时定时任务均为全部已连接账号写入独立快照；集合竞价买入、盘中调仓、尾盘风控均遍历各有效账号独立计算仓位（单票上限 ≤35%）并独立委托。
  - **全局共享**: 5 大核心策略表决矩阵（src/strategies/team_ensemble.py）、全市场行情缓存（src/data_cache.py）、8 大时序流水账本（src/scheduler_ledger.py）为全局共享。

### 3. 同花顺接口字段映射关键修复 (src/ths_connector.py, src/auto_trader.py)
- 修复了 	hs_connector.py 中 get_positions() 解析同花顺 qryChicang 返回字段的错位问题：
  - d_2102 = 股票代码 (symbol)
  - d_2103 = 股票名称 (
ame)
  - d_2164 / d_2117 = 持仓总股数 (mount)
  - d_2121 = 可卖股数 (vailable)
  - d_2122 = 成本价 (cost_price / vg_price)
  - **d_2124 = 当前最新价 (current_price)**（此前漏提取）
  - **d_2125 = 最新市值 (market_value)**（此前误用 d_2117 股数当作市值，且 uto_trader.py 用 market_value / amount 导致算出 1.0 元异常现价，现已彻底根除）。

### 4. 前端全自动量化座舱升级 (static/index.html)
- 采用黑曜极光量化 HUD 视觉体系（#premium-quant-hud-styles），严格遵循 **A 股红涨（#ff4d4f）绿跌（#00e676）** 标准及正负号显式保留。
- 修复顶栏 .header（z-index: 99999）与多账号下拉面板 #accountSelectorPanel（z-index: 100001）层级，解决被下方毛玻璃面板遮挡问题。
- 隐藏顶栏 #aiSchedToggle 停止按钮，防止误触关停后台调度器。
- 切换账号（switchAccount）时自动静默触发 /api/accounts/sync/<id> 并即时刷新右侧四宫格资产看板与持仓列表，无需手动点同步。

---

## 一、系统架构

### 核心模块
```
src/
├── auto_trader.py        # 自动交易引擎（含移动止损）
├── ai_scheduler.py       # AI调度器（支持无AI模式）
├── ai_brain.py           # AI决策大脑（规则+NLP混合）
├── risk_manager.py       # 风控系统（动态止损）
├── sentiment_analyzer.py # 情感分析（规则80% + NLP 20%）
├── cn_sentiment.py       # 中文NLP情感分析
├── nlp_sentiment.py      # NLP模型接口
├── sector_analysis.py    # 板块轮动分析
├── stock_keyword_db.py   # 股票关键词数据库
├── strategy_optimizer.py # 策略优化（Optuna）
├── strategy_health.py    # 策略健康监控
├── strategy_tracker.py   # 策略信号追踪
├── ths_connector.py      # 同花顺连接器
├── fast_data.py          # 数据获取（腾讯/东方财富）
└── strategies/           # 34个交易策略
```

### 前端
```
static/
├── index.html            # 主界面（决策/执行/监控三标签）
└── mobile/
    ├── index.html        # PWA移动端
    └── manifest.json     # PWA配置
```

---

## 二、已完成功能

### v1.0 (2026-05-03)
- 34个交易策略
- AI决策引擎
- 自动交易系统
- 风控系统
- 前端界面

### v1.1 (2026-05-04)
- 策略优化系统 (Optuna)
- 策略健康监控
- 批量选股 + 动态分散
- 移动止损功能
- PWA移动端
- 桌面应用打包
- 比赛规则校验
- 10个前端Bug修复
- 情感分析系统（规则+NLP混合）
- 股票关键词数据库

---

## 三、关键配置

### 比赛规则
```json
// config/competition_rules.json
{
  "initial_funds": 1000000,
  "trading_rules": {
    "market": ["SH", "SZ"],
    "t_plus_1": true,
    "min_order_unit": 100,
    "price_limits": {
      "main_board": {"up": 0.10, "down": -0.10},
      "gem": {"up": 0.20, "down": -0.20},
      "star": {"up": 0.20, "down": -0.20}
    }
  }
}
```

### 风控参数
```python
# 自动交易配置
stop_loss_ratio = 0.08      # 止损8%
take_profit_ratio = 0.15    # 止盈15%
trailing_stop_pct = 0.05    # 移动止损5%
max_position_ratio = 0.30   # 单只最大30%
max_total_position = 0.80   # 总仓位最大80%
```

### 情感分析配置
```python
# 混合方案权重
rule_weight = 0.80          # 规则权重80%
nlp_weight = 0.20           # NLP权重20%

# 关键词数量
positive_keywords = 56      # 正面关键词
negative_keywords = 51      # 负面关键词
stock_specific = 10         # 股票特定关键词
```

---

## 四、未来规划

### 短期 (1-2周)
- [ ] 股票关键词扩展到100+只
- [ ] 关键词库扩展
- [ ] 噪音过滤技术
- [ ] 数据获取基量提升

### 中期 (1个月)
- [ ] 接入云服务器
- [ ] 微信小程序版本
- [ ] 更多数据源（龙虎榜、融资融券）
- [ ] 策略参数自动调优

### 长期 (3个月)
- [ ] 实盘交易
- [ ] 多市场支持
- [ ] 移动端原生App
- [ ] 社区策略分享

---

## 五、已知问题

### 已修复
- 科创板mkcode错误
- 仓位计算用总资产
- K线数据无验证
- 东方财富API只返回100只
- 市场概况数据源错误
- 10个前端Bug
- 5个后端逻辑Bug

### 待优化
- 情感分析准确度 (当前60%)
- NLP模型对中文金融文本效果有限
- 个股情绪分析需要更多数据源

---

## 六、测试结果

### 回测表现
```
120天回测: +8.48%
胜率: 89%
平均盈利: +5.5%
最大回撤: -12.3%
```

### 情感分析
```
准确度: 60% (混合方案)
规则权重: 80%
NLP权重: 20%
```

---

## 七、部署信息

### 本地运行
```bash
python api.py          # Web服务器
python desktop_app.py  # 桌面应用
```

### 打包版
```
dist/THS_Trading_AI/THS_Trading_AI.exe
```

### 移动端
```
http://电脑IP:5000/mobile
```
