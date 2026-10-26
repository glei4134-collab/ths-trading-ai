"""
SQLite 数据存储模块（多账户版）
支持多账户隔离存储，每个账户独立的交易、持仓、信号记录
"""

import json
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import timedelta
from typing import Dict, List, Optional
from ths_connector import encrypt_cookie, decrypt_cookie

logger = logging.getLogger(__name__)

from paths import BASE_DIR, DATA_DIR

DB_PATH = DATA_DIR / "trading.db"


class DBStore:
    """SQLite 数据存储（多账户，线程安全）"""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 启用 WAL 模式，允许并发读
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        self._migrate_json_if_needed()
        self._ensure_account_id_columns()
        self._init_sentiment_tables()

    def _init_tables(self):
        """创建表结构"""
        c = self.conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                cookie TEXT DEFAULT '',
                ths_gdzh TEXT DEFAULT '',
                ths_competition_id TEXT DEFAULT '',
                balance REAL DEFAULT 1000000,
                initial_balance REAL DEFAULT 1000000,
                ky_fund REAL DEFAULT 0,
                market_value REAL DEFAULT 0,
                total_assets REAL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                last_sync_at TEXT DEFAULT ''
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                time TEXT,
                symbol TEXT,
                name TEXT,
                action TEXT,
                amount INTEGER,
                price REAL,
                total REAL,
                profit REAL DEFAULT 0,
                balance REAL,
                mode TEXT DEFAULT 'simulation',
                saved_at TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                account_id INTEGER DEFAULT 1,
                symbol TEXT,
                name TEXT,
                amount INTEGER,
                total_cost REAL,
                avg_price REAL,
                current_price REAL,
                PRIMARY KEY (account_id, symbol)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                strategy_name TEXT,
                symbol TEXT,
                signal TEXT,
                score REAL,
                confidence REAL,
                price_at_signal REAL,
                timestamp TEXT,
                result TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                timestamp TEXT,
                balance REAL,
                market_value REAL,
                total_assets REAL,
                total_profit REAL,
                position_count INTEGER
            )
        """)

        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy_name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_time ON signals(timestamp)")

        self.conn.commit()

    def _ensure_account_id_columns(self):
        """确保旧表有 account_id 列（兼容升级）"""
        c = self.conn.cursor()
        for table in ['trades', 'signals', 'account_snapshots', 'positions']:
            try:
                c.execute(f"SELECT account_id FROM {table} LIMIT 1")
            except sqlite3.OperationalError:
                c.execute(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER DEFAULT 1")
                logger.info(f"为 {table} 表添加 account_id 列")
        # 确保 accounts 表有新列
        for col, default in [('cookie', ''), ('ths_gdzh', ''), ('ths_competition_id', ''),
                              ('ky_fund', 0), ('market_value', 0), ('total_assets', 0),
                              ('last_sync_at', '')]:
            try:
                c.execute(f"SELECT {col} FROM accounts LIMIT 1")
            except sqlite3.OperationalError:
                c.execute(f"ALTER TABLE accounts ADD COLUMN {col} DEFAULT '{default}'" if isinstance(default, str)
                          else f"ALTER TABLE accounts ADD COLUMN {col} DEFAULT {default}")
        # active_account_id: 追踪当前活跃账户
        try:
            c.execute("SELECT active_account_id FROM accounts LIMIT 1")
        except sqlite3.OperationalError:
            c.execute("ALTER TABLE accounts ADD COLUMN active_account_id INTEGER DEFAULT 0")
            logger.info("为 accounts 表添加 active_account_id 列")
        # 创建索引
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_signals_account ON signals(account_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_account ON account_snapshots(account_id)")
        self.conn.commit()

    def _init_devices_table(self):
        """创建设备绑定表"""
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT UNIQUE NOT NULL,
                device_name TEXT DEFAULT '',
                bound_at TEXT,
                last_seen_at TEXT,
                pc_ip TEXT DEFAULT ''
            )
        """)
        self.conn.commit()

    def _init_sentiment_tables(self):
        """创建情绪分析历史表"""
        c = self.conn.cursor()
        # 交易时的情绪快照
        c.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                action TEXT NOT NULL,
                price REAL DEFAULT 0,
                amount INTEGER DEFAULT 0,
                sentiment INTEGER DEFAULT 50,
                keywords TEXT DEFAULT '[]',
                news_titles TEXT DEFAULT '[]',
                strategy TEXT DEFAULT '',
                regime TEXT DEFAULT '',
                account_id INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
        # 每日情绪跟踪（持仓期间）
        c.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                sentiment INTEGER DEFAULT 50,
                price REAL DEFAULT 0,
                change_pct REAL DEFAULT 0,
                keywords TEXT DEFAULT '[]',
                regime TEXT DEFAULT '',
                record_date TEXT,
                account_id INTEGER DEFAULT 1
            )
        """)
        # 后悔数据（没买但涨了）
        c.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_regret (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                signal TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                sentiment INTEGER DEFAULT 50,
                price_at_signal REAL DEFAULT 0,
                price_after_5d REAL DEFAULT 0,
                change_5d REAL DEFAULT 0,
                regime TEXT DEFAULT '',
                record_date TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_sentiment_history_symbol ON sentiment_history(symbol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sentiment_daily_symbol ON sentiment_daily(symbol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sentiment_daily_date ON sentiment_daily(record_date)")
        self.conn.commit()

    # ========== 情绪数据 CRUD ==========

    def save_sentiment_snapshot(self, symbol: str, stock_name: str, action: str,
                                price: float, amount: int, sentiment: int,
                                keywords: list = None, news_titles: list = None,
                                strategy: str = "", regime: str = "", account_id: int = 1):
        """保存交易时的情绪快照"""
        now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """INSERT INTO sentiment_history
               (symbol, stock_name, action, price, amount, sentiment, keywords, news_titles, strategy, regime, account_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, stock_name, action, price, amount, sentiment,
             json.dumps(keywords or []), json.dumps(news_titles or []),
             strategy, regime, account_id, now)
        )
        self.conn.commit()

    def save_sentiment_daily(self, symbol: str, sentiment: int, price: float,
                             change_pct: float = 0, keywords: list = None,
                             regime: str = "", account_id: int = 1):
        """保存每日情绪跟踪"""
        today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")
        # 检查今天是否已记录
        existing = self.conn.execute(
            "SELECT id FROM sentiment_daily WHERE symbol=? AND record_date=? AND account_id=?",
            (symbol, today, account_id)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE sentiment_daily SET sentiment=?, price=?, change_pct=?, keywords=?, regime=? WHERE id=?",
                (sentiment, price, change_pct, json.dumps(keywords or []), regime, existing["id"])
            )
        else:
            self.conn.execute(
                """INSERT INTO sentiment_daily
                   (symbol, sentiment, price, change_pct, keywords, regime, record_date, account_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, sentiment, price, change_pct, json.dumps(keywords or []), regime, today, account_id)
            )
        self.conn.commit()

    def save_sentiment_regret(self, symbol: str, stock_name: str, signal: str,
                              confidence: float, sentiment: int, price: float,
                              regime: str = ""):
        """保存后悔数据（有信号但没买）"""
        today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")
        self.conn.execute(
            """INSERT INTO sentiment_regret
               (symbol, stock_name, signal, confidence, sentiment, price_at_signal, regime, record_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, stock_name, signal, confidence, sentiment, price, regime, today)
        )
        self.conn.commit()

    def get_sentiment_accuracy(self, symbol: str = None, min_sentiment: int = 50, days: int = 30) -> Dict:
        """查询情绪准确率"""
        where = "WHERE sentiment >= ?"
        params = [min_sentiment]
        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)
        if days:
            where += f" AND created_at >= date('now', '-{days} days')"

        # 情绪>阈值时买入的胜率
        rows = self.conn.execute(
            f"SELECT sh.*, so.profit_pct, so.hold_days FROM sentiment_history sh "
            f"LEFT JOIN sentiment_outcomes so ON sh.id = so.buy_id "
            f"{where} AND sh.action = 'buy'", params
        ).fetchall()

        if not rows:
            return {"count": 0, "win_rate": 0, "avg_profit": 0}

        total = len(rows)
        wins = sum(1 for r in rows if (r["profit_pct"] or 0) > 0)
        avg_profit = sum(r["profit_pct"] or 0 for r in rows) / total

        return {
            "count": total,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "avg_profit": round(avg_profit, 2),
            "sentiment_threshold": min_sentiment
        }

    def get_sentiment_by_regime(self, regime: str, days: int = 30) -> Dict:
        """按市场环境查询情绪准确率"""
        rows = self.conn.execute(
            "SELECT sh.*, so.profit_pct FROM sentiment_history sh "
            "LEFT JOIN sentiment_outcomes so ON sh.id = so.buy_id "
            "WHERE sh.regime = ? AND sh.action = 'buy' "
            f"AND sh.created_at >= date('now', '-{days} days')",
            (regime,)
        ).fetchall()

        if not rows:
            return {"regime": regime, "count": 0, "win_rate": 0, "avg_profit": 0}

        total = len(rows)
        wins = sum(1 for r in rows if (r["profit_pct"] or 0) > 0)
        avg_profit = sum(r["profit_pct"] or 0 for r in rows) / total

        return {
            "regime": regime,
            "count": total,
            "win_rate": round(wins / total * 100, 1),
            "avg_profit": round(avg_profit, 2)
        }

    def get_stock_sentiment_history(self, symbol: str, limit: int = 10) -> List[Dict]:
        """获取个股情绪历史"""
        rows = self.conn.execute(
            "SELECT * FROM sentiment_history WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_regret_stats(self, days: int = 30) -> Dict:
        """获取后悔数据统计"""
        rows = self.conn.execute(
            f"SELECT * FROM sentiment_regret WHERE record_date >= date('now', '-{days} days')"
        ).fetchall()
        if not rows:
            return {"count": 0, "avg_change_5d": 0}
        return {"count": len(rows), "avg_change_5d": 0}  # 后续填充5日涨跌

    def _migrate_json_if_needed(self):
        """从 JSON 文件迁移旧数据（仅在有真实数据时才创建账户）"""
        try:
            c = self.conn.cursor()
            c.execute("SELECT COUNT(*) FROM accounts")
            if c.fetchone()[0] > 0:
                return

            account_file = DATA_DIR / "account.json"
            trades_file = DATA_DIR / "trades.json"
            positions_file = DATA_DIR / "positions.json"

            # 只有存在旧 JSON 数据时才迁移，不再创建默认账户
            if not account_file.exists() and not trades_file.exists():
                return

            now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            account_id = 1

            if account_file.exists():
                with open(account_file, 'r', encoding='utf-8') as f:
                    account = json.load(f)
                c.execute("""
                    INSERT INTO accounts (name, balance, initial_balance, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (account.get("name", "迁移账户"), account.get("balance", 1000000), account.get("initial_balance", 1000000),
                      account.get("created_at", now), account.get("updated_at", now)))
                account_id = c.lastrowid

            if trades_file.exists():
                with open(trades_file, 'r', encoding='utf-8') as f:
                    trades = json.load(f)
                for t in trades:
                    c.execute("""
                        INSERT INTO trades (account_id, time, symbol, name, action, amount, price, total, profit, balance, mode, saved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (account_id, t.get("time", ""), t.get("symbol", ""), t.get("name", ""),
                          t.get("action", ""), t.get("amount", 0), t.get("price", 0),
                          t.get("total", 0), t.get("profit", 0), t.get("balance", 0),
                          t.get("mode", "simulation"), t.get("saved_at", "")))

            if positions_file.exists():
                with open(positions_file, 'r', encoding='utf-8') as f:
                    positions = json.load(f)
                for symbol, pos in positions.items():
                    c.execute("""
                        INSERT OR REPLACE INTO positions (account_id, symbol, name, amount, total_cost, avg_price, current_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (account_id, symbol, pos.get("name", ""), pos.get("amount", 0),
                          pos.get("total_cost", 0), pos.get("avg_price", 0),
                          pos.get("current_price", 0)))

            self.conn.commit()
            logger.info("JSON 数据迁移完成")
        except Exception as e:
            logger.error(f"JSON 迁移失败: {e}")

    # ==================== 账户管理 ====================

    def create_account(self, name: str, balance: float = 1000000,
                       cookie: str = '', ths_gdzh: str = '',
                       ths_competition_id: str = '') -> Dict:
        """创建新账户（绑定同花顺Cookie）"""
        with self._lock:
            c = self.conn.cursor()
            now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""
                INSERT INTO accounts (name, cookie, ths_gdzh, ths_competition_id,
                                      balance, initial_balance, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, encrypt_cookie(cookie), ths_gdzh, ths_competition_id, balance, balance, now, now))
            self.conn.commit()
            account_id = c.lastrowid
            return {"id": account_id, "name": name, "balance": balance,
                    "initial_balance": balance, "created_at": now, "updated_at": now}

    def list_accounts(self) -> List[Dict]:
        """列出所有账户（不含cookie明文）"""
        c = self.conn.cursor()
        c.execute("SELECT * FROM accounts ORDER BY id")
        result = []
        for row in c.fetchall():
            d = dict(row)
            d.pop("cookie", None)  # 不返回cookie明文
            result.append(d)
        return result

    def get_account(self, account_id: int = 1) -> Dict:
        """获取指定账户，不存在时返回 None（不再返回幽灵数据）"""
        c = self.conn.cursor()
        c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = c.fetchone()
        if row:
            d = dict(row)
            d.pop("cookie", None)
            return d
        return None

    def get_active_account_id(self) -> int:
        """获取当前活跃账户 ID"""
        c = self.conn.cursor()
        c.execute("SELECT active_account_id FROM accounts WHERE active_account_id != 0 LIMIT 1")
        row = c.fetchone()
        if row:
            return row["active_account_id"]
        # 没有设置过活跃账户，用第一个账户
        c.execute("SELECT id FROM accounts ORDER BY id LIMIT 1")
        row = c.fetchone()
        return row["id"] if row else 1

    def set_active_account_id(self, account_id: int):
        """设置活跃账户 ID（清除其他账户的标记）"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("UPDATE accounts SET active_account_id = 0")
            c.execute("UPDATE accounts SET active_account_id = ? WHERE id = ?", (account_id, account_id))
            self.conn.commit()
            logger.info(f"切换活跃账户到 id={account_id}")

    def get_account_cookie(self, account_id: int) -> str:
        """获取账户Cookie（内部使用，自动解密）"""
        c = self.conn.cursor()
        c.execute("SELECT cookie FROM accounts WHERE id = ?", (account_id,))
        row = c.fetchone()
        return decrypt_cookie(row["cookie"]) if row and row["cookie"] else ""

    def update_account_sync(self, account_id: int, balance: float, ky_fund: float,
                            market_value: float, total_assets: float):
        """更新账户同步数据（从THS拉取后）"""
        with self._lock:
            c = self.conn.cursor()
            now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""
                UPDATE accounts SET balance=?, ky_fund=?, market_value=?, total_assets=?,
                       updated_at=?, last_sync_at=? WHERE id=?
            """, (balance, ky_fund, market_value, total_assets, now, now, account_id))
            self.conn.commit()

    def update_balance(self, balance: float, account_id: int = 1):
        """更新余额"""
        with self._lock:
            c = self.conn.cursor()
            now = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("UPDATE accounts SET balance = ?, updated_at = ? WHERE id = ?",
                      (balance, now, account_id))
            self.conn.commit()

    def delete_account(self, account_id: int) -> bool:
        """删除账户"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            c.execute("DELETE FROM trades WHERE account_id = ?", (account_id,))
            c.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
            c.execute("DELETE FROM signals WHERE account_id = ?", (account_id,))
            c.execute("DELETE FROM account_snapshots WHERE account_id = ?", (account_id,))
            self.conn.commit()
            return True

    def rename_account(self, account_id: int, name: str):
        """重命名账户"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))
            self.conn.commit()

    # ==================== 交易记录（多账户） ====================

    def add_trade(self, trade: Dict, account_id: int = 1):
        """写入交易记录"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("""
                INSERT INTO trades (account_id, time, symbol, name, action, amount, price, total, profit, balance, mode, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, trade.get("time", ""), trade.get("symbol", ""), trade.get("name", ""),
                  trade.get("action", ""), trade.get("amount", 0), trade.get("price", 0),
                  trade.get("total", 0), trade.get("profit", 0), trade.get("balance", 0),
                  trade.get("mode", "simulation"),
                  datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")))
            self.conn.commit()

    def get_trades(self, limit: int = 50, symbol: str = None,
                   start_date: str = None, end_date: str = None,
                   account_id: int = None) -> List[Dict]:
        """查询交易记录"""
        c = self.conn.cursor()
        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if start_date:
            query += " AND time >= ?"
            params.append(start_date)
        if end_date:
            query += " AND time <= ?"
            params.append(end_date)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        c.execute(query, params)
        return [dict(row) for row in c.fetchall()]

    # ==================== 持仓（多账户） ====================

    def save_positions(self, positions: Dict, account_id: int = 1):
        """保存所有持仓"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
            for symbol, pos in positions.items():
                c.execute("""
                    INSERT INTO positions (account_id, symbol, name, amount, total_cost, avg_price, current_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (account_id, symbol, pos.get("name", ""), pos.get("amount", 0),
                      pos.get("total_cost", 0), pos.get("avg_price", 0),
                      pos.get("current_price", 0)))
            self.conn.commit()

    def get_positions(self, account_id: int = None) -> Dict:
        """获取持仓，key=symbol"""
        c = self.conn.cursor()
        if account_id is not None:
            c.execute("SELECT * FROM positions WHERE account_id = ?", (account_id,))
        else:
            c.execute("SELECT * FROM positions")
        positions = {}
        for row in c.fetchall():
            row_dict = dict(row)
            positions[row_dict["symbol"]] = row_dict
        return positions

    # ==================== 策略信号（多账户） ====================

    def add_signal(self, signal: Dict, account_id: int = 1):
        """写入策略信号"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("""
                INSERT INTO signals (account_id, strategy_name, symbol, signal, score, confidence, price_at_signal, timestamp, result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (account_id, signal.get("strategy_name", ""), signal.get("symbol", ""),
                  signal.get("signal", ""), signal.get("score", 0),
                  signal.get("confidence", 0), signal.get("price_at_signal", 0),
                  signal.get("timestamp", datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")),
                  json.dumps(signal.get("result")) if signal.get("result") else None))
            self.conn.commit()

    def get_signals(self, strategy: str = None, symbol: str = None,
                    days: int = 30, limit: int = 100,
                    account_id: int = None) -> List[Dict]:
        """查询策略信号"""
        c = self.conn.cursor()
        cutoff = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y-%m-%d")
        query = "SELECT * FROM signals WHERE timestamp >= ?"
        params = [cutoff]
        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)
        if strategy:
            query += " AND strategy_name = ?"
            params.append(strategy)
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        c.execute(query, params)
        rows = [dict(row) for row in c.fetchall()]
        for row in rows:
            if row.get("result"):
                try:
                    row["result"] = json.loads(row["result"])
                except Exception:
                    row["result"] = None
        return rows

    # ==================== 账户快照 ====================

    def save_snapshot(self, balance: float, market_value: float,
                      total_assets: float, total_profit: float, position_count: int,
                      account_id: int = 1):
        """保存账户快照"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("""
                INSERT INTO account_snapshots (account_id, timestamp, balance, market_value, total_assets, total_profit, position_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (account_id, datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S"),
                  balance, market_value, total_assets, total_profit, position_count))
            self.conn.commit()

    def get_equity_curve(self, days: int = 30, account_id: int = None) -> List[Dict]:
        """获取收益曲线"""
        c = self.conn.cursor()
        cutoff = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=days)).strftime("%Y-%m-%d")
        query = "SELECT * FROM account_snapshots WHERE timestamp >= ?"
        params = [cutoff]
        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)
        query += " ORDER BY timestamp"
        c.execute(query, params)
        return [dict(row) for row in c.fetchall()]

    # ==================== 统计 ====================

    def get_trade_stats(self, account_id: int = None) -> Dict:
        """获取交易统计"""
        c = self.conn.cursor()
        query = "SELECT COUNT(*) as total, SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins FROM trades WHERE profit != 0"
        params = []
        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)
        c.execute(query, params)
        row = c.fetchone()
        total = row["total"] or 0
        wins = row["wins"] or 0
        return {
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": total - wins,
            "win_rate": round(wins / total * 100, 2) if total > 0 else 0
        }

    def get_account_summary(self, account_id: int) -> Dict:
        """获取单个账户摘要"""
        account = self.get_account(account_id)
        if not account:
            return {"id": account_id, "name": "未知", "balance": 0, "market_value": 0,
                    "total_assets": 0, "total_profit": 0, "total_profit_rate": 0,
                    "position_count": 0, "created_at": ""}
        positions = self.get_positions(account_id)
        total_market_value = sum(
            pos.get("current_price", pos.get("avg_price", 0)) * pos.get("amount", 0)
            for pos in positions.values()
        )
        total_profit = sum(pos.get("profit", 0) for pos in positions.values())
        initial = account.get("initial_balance", 1000000)
        # total_assets 从数据库读取（含冻结金额），不等于 balance
        total_assets = account.get("total_assets", 0) or account.get("balance", 0)
        balance = account.get("balance", 0)

        return {
            "id": account_id,
            "name": account.get("name", ""),
            "balance": round(balance, 2),
            "market_value": round(total_market_value, 2),
            "total_assets": round(total_assets, 2),
            "total_profit": round(total_profit, 2),
            "total_profit_rate": round((total_assets - initial) / initial * 100, 2) if initial else 0,
            "position_count": len(positions),
            "created_at": account.get("created_at", "")
        }

    def get_all_accounts_summary(self) -> List[Dict]:
        """获取所有账户摘要"""
        accounts = self.list_accounts()
        return [self.get_account_summary(acc["id"]) for acc in accounts]

    def get_global_summary(self) -> Dict:
        """获取全局汇总（所有账户合计）"""
        summaries = self.get_all_accounts_summary()
        return {
            "total_accounts": len(summaries),
            "total_balance": round(sum(s["balance"] for s in summaries), 2),
            "total_market_value": round(sum(s["market_value"] for s in summaries), 2),
            "total_assets": round(sum(s["total_assets"] for s in summaries), 2),
            "total_profit": round(sum(s["total_profit"] for s in summaries), 2),
            "accounts": summaries
        }

    def export_all(self, account_id: int = None) -> Dict:
        """导出数据"""
        return {
            "accounts": self.list_accounts(),
            "account": self.get_account(account_id or 1),
            "trades": self.get_trades(limit=10000, account_id=account_id),
            "positions": self.get_positions(account_id),
            "signals": self.get_signals(days=365, limit=10000, account_id=account_id),
            "exported_at": datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d %H:%M:%S")
        }


# 全局实例
db_store = DBStore()
