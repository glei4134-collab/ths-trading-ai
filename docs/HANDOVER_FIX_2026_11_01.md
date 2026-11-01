# 交接：下单闸门 / 入口收敛 / 鉴权（2026-11-01）

本文记录本次修复的**行为变化、文件、调用约定**。部署前请通读「启动方式」和「绑定 Cookie」。

---

## 1. 必须改的启动方式

| 场景 | 旧 | 新 |
|------|----|----|
| 本地 Web | `python api.py` | `python run_api.py` |
| 生产 systemd | `gunicorn ... api:app` | **不变**（走 `api` 包） |
| Railway | `python api.py` | `python run_api.py` |
| 桌面托盘 | `from api import app` | **不变**（导入的是 `api/` 包） |

原 `api.py`（约 6400 行单体）已改名为 `legacy_api_monolith.py`，并加入 `.gitignore`，**禁止再部署、禁止再往里面加路由**。

本地调试关闭鉴权：

```bash
set ALLOW_INSECURE=1
python run_api.py
```

生产不要设 `ALLOW_INSECURE`。令牌文件：`config/api_token.json`（已 gitignore），或环境变量 `API_TOKEN`。座舱沿用 `localStorage.api_token` + `Authorization: Bearer`。

---

## 2. 下单唯一闸门 `src/order_guard.py`

所有买卖应先 `validate_pre_trade(...)`。拒绝则 **不得** `db_store.add_trade`。

硬规则：

- 整手 100 股（零股清仓卖出除外）
- 报单时段 **09:15–11:30 / 13:00–15:00**（含集合竞价）
- 涨跌停按板块：主板 10%，创业板/科创 20%
- 单票仓位硬顶 **35%**（超出则下修到整手，不够 1 手则拒绝）
- 卖出不超过可卖（T+1 `available=0` 拒绝）

常量：

```python
TEAM_MAX_SINGLE_PCT = 0.35
TEAM_INITIAL_BALANCE = 200000.0
LOT_SIZE = 100
```

`config/competition_rules.json` 的 `initial_funds` 已改为 **200000**，与账户盈亏口径一致。  
`config/ai_scheduler.json` 的 `position_limit_pct` 已从 `1.0` 改为 **0.35**。

---

## 3. 账户下单入口 `AccountManager.place_order`

```python
account_manager.place_order(
    account_id, symbol, "buy"|"sell", price, amount,
    name="...", dry_run=False, persist=True, skip_optimizer=False,
)
```

返回字段：`success` / `message` / `code` / `amount` / `recorded`。

- 闸门失败：`success=False, recorded=False`，不写库
- 券商失败：同样不写库
- 仅 `success=True` 时 `add_trade` + `sync_from_ths`

调度器集合竞价、尾盘、AI `_execute_trades` 多账户路径已改为调用 `place_order`，删除了「无论成败都 add_trade」。

单账户兜底仍走 `auto_trader.execute_order`，其 `check_risk` 已接入同一闸门。

---

## 4. Cookie 绑定（一次性票据）

`/api/accounts/quick-bind` **不再免认证**。

流程：

1. 座舱已登录（Bearer = API_TOKEN）
2. `POST /api/accounts/bind-ticket` → `{ ticket, ttl_seconds: 600, bind_url }`
3. 书签/控制台把 Cookie 打到  
   `/api/accounts/quick-bind?ticket=<ticket>&cookie=...`
4. 票据一次性消费，10 分钟过期

实现：`src/bind_ticket.py`，路由在 `api/routes/account.py`。

---

## 5. 主要改动文件

| 文件 | 作用 |
|------|------|
| `src/order_guard.py` | 新建，硬风控 |
| `src/bind_ticket.py` | 新建，绑定票据 |
| `src/account_manager.py` | `place_order` |
| `src/auto_trader.py` | `check_risk` 走闸门 |
| `src/ai_scheduler.py` | 多账户下单改入口，去掉失败记账 |
| `src/competition.py` | 报单窗口 + 分板块涨跌停 |
| `src/risk_manager.py` | 阶段仓位 `min(..., 0.35)` |
| `api/context.py` | 默认鉴权 + quick-bind 验票 |
| `api/__init__.py` | `create_app` + 后台服务一次启动 |
| `run_api.py` | 本地启动 |
| `tests/test_order_guard.py` | 闸门单测 |

---

## 6. 部署检查清单

- [ ] 服务器只跑 `gunicorn api:app` 或 `python run_api.py`，确认没有第二个 `python api.py`
- [ ] `config/api_token.json` 已生成，座舱能登录
- [ ] 绑定账户改走 bind-ticket
- [ ] 用非交易时段或 `dry_run` 打一笔，确认闸门拒绝不会出现在 `trades` 表
- [ ] 单票加仓试算不超过总资产 35%

---

## 7. 未在本轮处理（后续）

- 回测随机游走与对外收益数字
- `ai_brain` 策略分随机偏移
- 砍掉 `broker.AutoTrader` / MCP `main.py`
- Cookie XOR 换正规密钥
- 前端 430KB 单文件拆分
