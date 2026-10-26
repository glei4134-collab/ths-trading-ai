# THS Trading AI - 项目记忆

## 最后更新: 2026-05-04

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
