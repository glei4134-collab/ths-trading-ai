"""一次性账户绑定票据：公开 quick-bind 必须携带未使用且未过期的 ticket。"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Optional

_lock = threading.Lock()
_tickets: Dict[str, float] = {}
TTL_SECONDS = 600


def issue_bind_ticket() -> str:
    token = secrets.token_urlsafe(18)
    with _lock:
        _tickets[token] = time.time() + TTL_SECONDS
        _purge_locked()
    return token


def consume_bind_ticket(token: Optional[str]) -> bool:
    if not token:
        return False
    now = time.time()
    with _lock:
        _purge_locked(now)
        exp = _tickets.pop(token, None)
        return bool(exp and exp >= now)


def peek_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    now = time.time()
    with _lock:
        _purge_locked(now)
        exp = _tickets.get(token)
        return bool(exp and exp >= now)


def _purge_locked(now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    dead = [k for k, exp in _tickets.items() if exp < now]
    for k in dead:
        _tickets.pop(k, None)
