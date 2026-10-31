"""
市场行情、技术指标与舆情分析路由
"""

import os
import sys
import json
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Optional, Any

from flask import Blueprint, jsonify, request, Response, send_file

from api.context import (
    engine, broker, trader, _device_manager, trading_mode, trading_state,
    AKSHARE_AVAILABLE, API_TOKEN, _PUBLIC_PATHS,
    get_stock_data, calculate_indicators,
    config_manager, web_connector, get_active_connector, decrypt_cookie,
    ai_connector, enhanced_strategy, SignalType,
    auto_trader, OrderType, risk_manager, backtester,
    data_store, competition_manager, strategy_scanner,
    cache, get_realtime_quote, get_realtime_quotes_batch,
    get_kline, get_intraday, compute_indicators, get_kline_with_indicators,
    notification_manager, db_store, account_manager, auto_login_service
)

logger = logging.getLogger(__name__)

market_bp = Blueprint('market', __name__)

@market_bp.route('/api/market/overview', methods=['GET'])
def market_overview():
    """获取市场概况（全市场数据）"""
    try:
        up_count = down_count = flat_count = total = 0
        sh_index = sz_index = 0.0

        # 全市场涨跌统计（用原始数据，不过滤）
        try:
            df = get_realtime_quote()
            if df is not None and not df.empty:
                up_count = int((df['涨跌幅'] > 0).sum())
                down_count = int((df['涨跌幅'] < 0).sum())
                flat_count = int((df['涨跌幅'] == 0).sum())
                total = len(df)
        except Exception:
            pass

        # 上证指数 + 深证成指
        try:
            from fast_data import fetch_tencent_realtime
            idx = fetch_tencent_realtime(["000001", "399001"])
            if "000001" in idx:
                sh_index = idx["000001"]["price"]
            if "399001" in idx:
                sz_index = idx["399001"]["price"]
        except Exception:
            pass

        return jsonify({
            "success": True,
            "data": {
                "sh_index": sh_index,
                "sz_index": sz_index,
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "total": total
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/realtime/<symbol>', methods=['GET'])
def stock_realtime(symbol):
    """获取股票实时行情"""
    try:
        stock = get_stock_data(symbol)
        if stock:
            return jsonify({"success": True, "data": stock})
        return jsonify({"success": False, "message": f"未找到股票 {symbol}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/history/<symbol>', methods=['GET'])
def stock_history(symbol):
    """获取历史K线数据（使用 fast_data，带缓存）"""
    try:
        days = request.args.get('days', 30, type=int)

        data = get_kline(symbol, period="daily", days=days)
        if data:
            return jsonify({"success": True, "data": data})

        return jsonify({"success": False, "message": f"未找到 {symbol} K线数据"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/analyze/<symbol>', methods=['GET'])
def stock_analyze(symbol):
    """综合分析股票"""
    try:
        result = engine.analyze_stock(symbol)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/indicators/<symbol>', methods=['GET'])
def stock_indicators(symbol):
    """获取K线+技术指标（全缓存，含均线/MACD/RSI）"""
    try:
        days = request.args.get('days', 60, type=int)

        data = get_kline_with_indicators(symbol, days=days)
        if not data:
            return jsonify({"success": False, "message": f"未找到 {symbol} 数据"})

        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/intraday/<symbol>', methods=['GET'])
def stock_intraday(symbol):
    """获取分时数据（当日分钟级数据，带缓存）"""
    try:
        data = get_intraday(symbol)
        if data:
            return jsonify({"success": True, "data": data})

        return jsonify({"success": False, "message": f"未找到 {symbol} 分时数据"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/search', methods=['GET'])
def stock_search():
    """搜索股票（全市场）"""
    try:
        keyword = request.args.get('keyword', '').strip()
        limit = request.args.get('limit', 20, type=int)

        if not keyword:
            return jsonify({"success": True, "data": []})

        # 从全市场数据搜索
        try:
            df = get_realtime_quote()
            if df is not None and not df.empty:
                mask = df['代码'].str.contains(keyword, na=False) | df['名称'].str.contains(keyword, na=False)
                matched = df[mask].head(limit)
                result = []
                for _, row in matched.iterrows():
                    result.append({
                        "code": row['代码'],
                        "name": row['名称'],
                        "price": float(row['最新价']),
                        "change_pct": float(row['涨跌幅']),
                        "volume": int(row['成交量']),
                        "amount": float(row['成交额']),
                        "turnover": float(row['换手率']),
                    })
                return jsonify({"success": True, "data": result})
        except Exception:
            pass

        # fallback: 从比赛股票池搜索
        pool = competition_manager.get_stock_pool(limit=500)
        result = [s for s in pool if keyword in s['code'] or keyword in s['name']][:limit]
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/hot', methods=['GET'])
def hot_stocks():
    """获取比赛可交易股票池（按成交额排序）"""
    try:
        limit = request.args.get('limit', 50, type=int)
        sort_by = request.args.get('sort', 'amount', type=str)
        stocks = competition_manager.get_stock_pool(limit=limit, sort_by=sort_by)
        return jsonify({"success": True, "data": stocks})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/pool', methods=['GET'])
def stock_pool():
    """获取比赛可交易股票池"""
    try:
        limit = request.args.get('limit', 100, type=int)
        sort_by = request.args.get('sort', 'amount', type=str)
        stocks = competition_manager.get_stock_pool(limit=limit, sort_by=sort_by)
        return jsonify({"success": True, "data": stocks})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/pool/count', methods=['GET'])
def stock_pool_count():
    """获取股票池统计"""
    try:
        count = competition_manager.get_stock_pool_count()
        return jsonify({"success": True, "data": count})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/cache/status', methods=['GET'])
def cache_status():
    """获取缓存状态"""
    try:
        status = cache.status()
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/cache/clear', methods=['POST'])
def cache_clear():
    """清除缓存"""
    try:
        data = request.json or {}
        pattern = data.get("pattern", "")
        if pattern:
            cache.invalidate(pattern)
        else:
            cache.clear()
        return jsonify({"success": True, "message": "缓存已清除"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/market/sentiment', methods=['GET'])
def market_sentiment():
    """获取市场情绪指标（使用缓存数据）"""
    try:
        result = {"up_count": 0, "down_count": 0, "flat_count": 0, "total": 0}

        try:
            df = get_realtime_quote()
            if df is not None and not df.empty:
                result["up_count"] = int(len(df[df['涨跌幅'] > 0]))
                result["down_count"] = int(len(df[df['涨跌幅'] < 0]))
                result["flat_count"] = int(len(df[df['涨跌幅'] == 0]))
                result["total"] = len(df)

                # 涨停/跌停统计
                result["limit_up"] = int(len(df[df['涨跌幅'] >= 9.9]))
                result["limit_down"] = int(len(df[df['涨跌幅'] <= -9.9]))

                # 成交额统计（亿）
                result["total_amount"] = round(float(df['成交额'].sum()) / 1e8, 2)

                # 市场情绪评分 (-100 ~ 100)
                if result["total"] > 0:
                    ratio = (result["up_count"] - result["down_count"]) / result["total"]
                    result["sentiment_score"] = round(ratio * 100, 1)
                else:
                    result["sentiment_score"] = 0
        except Exception as e:
            logger.error(f"获取市场情绪失败: {e}")

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/sentiment/current', methods=['GET'])
def sentiment_current():
    """获取当前新闻情绪（market_sentiment.json）"""
    try:
        import json as _json
        from pathlib import Path
        f = Path(__file__).parent / "data" / "market_sentiment.json"
        if f.exists():
            data = _json.loads(f.read_text(encoding="utf-8"))
            return jsonify({"success": True, "data": data})
        return jsonify({"success": True, "data": None})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/sentiment/history', methods=['GET'])
def sentiment_history():
    """获取情绪历史数据（sentiment_daily表 + 情绪快照表）"""
    try:
        from db_store import db_store
        days = int(request.args.get('days', 7))
        symbol = request.args.get('symbol', None)

        # 每日情绪跟踪
        daily = []
        try:
            if symbol:
                rows = db_store.conn.execute(
                    "SELECT * FROM sentiment_daily WHERE symbol=? ORDER BY record_date DESC LIMIT ?",
                    (symbol, days * 10)
                ).fetchall()
            else:
                rows = db_store.conn.execute(
                    "SELECT * FROM sentiment_daily ORDER BY record_date DESC LIMIT ?",
                    (days * 50)
                ).fetchall()
            for r in rows:
                daily.append(dict(r))
        except Exception:
            pass

        # 情绪快照（交易时记录）
        snapshots = []
        try:
            snap_rows = db_store.conn.execute(
                "SELECT * FROM sentiment_history ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            for r in snap_rows:
                snapshots.append(dict(r))
        except Exception:
            pass

        # 后悔数据
        regrets = []
        try:
            regret_rows = db_store.conn.execute(
                "SELECT * FROM sentiment_regret ORDER BY record_date DESC LIMIT 20"
            ).fetchall()
            for r in regret_rows:
                regrets.append(dict(r))
        except Exception:
            pass

        return jsonify({
            "success": True,
            "data": {
                "daily": daily,
                "snapshots": snapshots,
                "regrets": regrets,
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/sentiment/scheduler-status', methods=['GET'])
def sentiment_scheduler_status():
    """获取情绪采集调度状态（盘前/午间/收盘后）"""
    try:
        from ai_scheduler import ai_scheduler
        import json as _json
        from pathlib import Path
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        daily_flags_path = Path(__file__).parent / "data" / f"daily_flags_{today_str}.json"
        timestamps = {}
        if daily_flags_path.exists():
            try:
                timestamps = _json.loads(daily_flags_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        data = {
            "pre_market_done": ai_scheduler._pre_market_done,
            "lunch_done": ai_scheduler._lunch_done,
            "after_hours_done": ai_scheduler._after_hours_done,
            "pre_market_result": ai_scheduler._pre_market_result,
            "lunch_sentiment": ai_scheduler._lunch_sentiment,
            "enabled": ai_scheduler.enabled,
            "running": ai_scheduler._running,
            "pre_market_time": timestamps.get("pre_market_time", ""),
            "lunch_time": timestamps.get("lunch_time", ""),
            "after_hours_time": timestamps.get("after_hours_time", ""),
        }
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/market/regime', methods=['GET'])
def market_regime():
    """获取当前市场环境判断"""
    try:
        from market_regime import market_regime_detector
        result = market_regime_detector.detect()
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@market_bp.route('/api/stock/multi-tf/<symbol>', methods=['GET'])
def stock_multi_tf(symbol):
    """获取多周期指标对比"""
    try:
        from multi_timeframe import multi_timeframe_analyzer
        daily = calculate_indicators(symbol)
        weekly = multi_timeframe_analyzer.get_weekly_indicators(symbol)
        hourly = multi_timeframe_analyzer.get_hourly_indicators(symbol)
        alignment = multi_timeframe_analyzer.analyze_alignment(daily, weekly, hourly)
        return jsonify({
            "success": True,
            "data": {
                "daily": daily,
                "weekly": weekly,
                "hourly": hourly,
                "alignment": alignment
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


