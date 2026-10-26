"""
GitHub 数据同步模块
本地服务器从GitHub仓库拉取云端交易记录
"""

import json
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
REPO = "glei4134-collab/ths-trading-ai"


class GitHubSync:
    """从GitHub仓库同步交易数据"""

    def __init__(self):
        self._token = self._load_token()

    def _load_token(self) -> str:
        """加载GitHub Token"""
        token_file = Path(__file__).parent.parent / "config" / "github_token.json"
        try:
            if token_file.exists():
                return json.loads(token_file.read_text(encoding="utf-8")).get("token", "")
        except Exception:
            pass
        return os.environ.get("GITHUB_TOKEN", "")

    def _github_get(self, path: str) -> dict:
        """调用GitHub API"""
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            r = requests.get(f"{GITHUB_API}/{path}", headers=headers, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.error(f"GitHub API error: {e}")
        return {}

    def pull_trade_log(self) -> list:
        """从GitHub拉取交易记录"""
        data = self._github_get(f"repos/{REPO}/contents/data/trade_log.json")
        if not data or "content" not in data:
            logger.debug("GitHub: 无交易记录")
            return []

        try:
            import base64
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content)
        except Exception as e:
            logger.error(f"解析交易记录失败: {e}")
            return []

    def pull_sentiment_data(self) -> dict:
        """从GitHub拉取情绪数据"""
        result = {}
        for filename in ["market_sentiment.json", "trade_log.json"]:
            data = self._github_get(f"repos/{REPO}/contents/data/{filename}")
            if data and "content" in data:
                try:
                    import base64
                    content = base64.b64decode(data["content"]).decode("utf-8")
                    result[filename] = json.loads(content)
                except Exception:
                    pass
        return result

    def sync_to_local(self):
        """同步GitHub数据到本地数据库"""
        try:
            from db_store import db_store

            # 拉取交易记录
            trades = self.pull_trade_log()
            if not trades:
                logger.info("GitHub同步: 无新交易记录")
                return

            # 找到最新的未同步记录
            local_latest = self._get_local_latest_time()
            new_trades = [t for t in trades if t.get("timestamp", "") > local_latest]

            if not new_trades:
                logger.info("GitHub同步: 数据已是最新")
                return

            # 同步到本地数据库
            synced = 0
            for entry in new_trades:
                for trade in entry.get("trades", []):
                    if trade.get("success"):
                        db_store.conn.execute(
                            """INSERT INTO trades (account_id, time, symbol, name, action, amount, price, total, mode, saved_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (1, entry.get("timestamp", ""),
                             trade.get("symbol", ""), trade.get("symbol", ""),
                             trade.get("action", ""), trade.get("amount", 0),
                             trade.get("price", 0), trade.get("amount", 0) * trade.get("price", 0),
                             "cloud", entry.get("timestamp", ""))
                        )
                        synced += 1
            db_store.conn.commit()

            logger.info(f"GitHub同步: 同步了 {synced} 笔交易记录")
        except Exception as e:
            logger.error(f"GitHub同步失败: {e}")

    def _get_local_latest_time(self) -> str:
        """获取本地最新交易时间"""
        try:
            from db_store import db_store
            row = db_store.conn.execute(
                "SELECT MAX(time) FROM trades WHERE mode='cloud'"
            ).fetchone()
            return row[0] or "2000-01-01 00:00:00"
        except Exception:
            return "2000-01-01 00:00:00"


# 全局实例
github_sync = GitHubSync()
