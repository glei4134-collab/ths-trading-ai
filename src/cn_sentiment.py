"""
轻量级中文情感分析模块（优化版 v2.1）
基于金融领域规则+词典，支持否定词前置反转、程度副词缩放、贝叶斯平滑与重大风险一票压制
"""

import re
from collections import OrderedDict
from typing import Dict, List, Tuple


class CnSentimentAnalyzer:
    """中文情感分析器（增强风控与正则DFA加速 v2.2）"""

    # 情感词典
    POSITIVE_WORDS = {
        # 强正面
        "暴涨": 2.0, "涨停": 1.8, "新高": 1.6, "创新高": 1.6,
        "大幅上涨": 1.5, "强势突破": 1.5, "飙升": 1.8,
        # 中等正面
        "利好": 1.2, "上涨": 1.0, "突破": 1.1, "增长": 1.1,
        "盈利": 1.1, "超预期": 1.2, "反弹": 1.0, "放量": 1.0,
        "金叉": 1.0, "龙头": 1.1, "热点": 1.0, "资金流入": 1.1,
        "业绩增长": 1.2, "净利润增长": 1.3, "营收增长": 1.1,
        "扭亏为盈": 1.4, "业绩大幅预增": 1.5,
        "政策利好": 1.2, "机构买入": 1.1, "北向资金买入": 1.0,
        "回购": 1.1, "增持": 1.1, "分红": 1.0, "高送转": 1.1,
        # 弱正面
        "牛市": 0.9, "强势": 0.9, "企稳": 0.8, "回暖": 0.8,
        "震荡上行": 0.8, "底部放量": 0.9, "突破压力": 1.0,
        "站上均线": 0.8, "多头排列": 0.9, "量价配合": 0.9,
    }

    NEGATIVE_WORDS = {
        # 重大财报与法律暴雷（强负面 2.0-2.5）
        "暴跌": 2.0, "崩盘": 2.2, "跌停": 1.8, "新低": 1.6,
        "大幅下跌": 1.5, "破位下跌": 1.5, "闪崩": 2.0,
        "立案调查": 2.5, "立案": 2.2, "财务造假": 2.5, "暴雷": 2.5,
        "退市": 2.5, "*ST": 2.0, "巨额亏损": 2.5, "净亏损": 2.2,
        "首亏": 2.0, "单季净利润同比下降": 2.5, "净利润同比下降": 2.2,
        "净利减少": 2.0, "业绩大幅下滑": 2.2, "业绩腰斩": 2.5,
        "毛利率下降": 1.5, "重组失败": 2.2, "违约": 2.0,
        "资金链断裂": 2.5, "清仓式减持": 2.0,

        # 中等负面
        "利空": 1.2, "下跌": 1.0, "破位": 1.1, "亏损": 1.2,
        "下滑": 1.0, "不及预期": 1.3, "业绩下滑": 1.2,
        "缩量": 0.9, "死叉": 1.0, "资金流出": 1.1, "机构卖出": 1.0,
        "政策收紧": 1.1, "减持": 1.1, "质押": 0.9, "商誉减值": 1.4,
        "诉讼": 1.1, "处罚": 1.3, "警示函": 1.2, "ST": 1.4,
        # 弱负面
        "熊市": 0.9, "弱势": 0.9, "低迷": 0.8, "疲软": 0.8,
        "震荡下行": 0.8, "跌破支撑": 0.9, "空头排列": 0.9,
        "缩量下跌": 0.8, "阴跌": 0.8, "套牢": 0.9, "承压": 0.9,
    }

    # 重大风险关键词（触发一票压制机制，得分强制截断 <= 38）
    CRITICAL_RISK_WORDS = {
        "立案调查", "财务造假", "暴雷", "退市", "巨额亏损",
        "单季净利润同比下降", "净亏损", "资金链断裂", "业绩腰斩",
        "重组失败", "违约", "清仓式减持"
    }

    # 否定词（会反转情感）
    NEGATION_WORDS = ("不", "没", "未", "无", "非", "别", "莫", "勿", "难")

    # 程度副词
    INTENSIFIERS = {"很": 1.3, "非常": 1.4, "极其": 1.5, "十分": 1.3, "特别": 1.4, "相当": 1.3, "极为": 1.5, "大幅": 1.4, "显著": 1.3}
    DIMINISHERS = {"稍": 0.7, "略": 0.7, "微": 0.7, "有点": 0.8, "稍微": 0.8, "小幅": 0.7}

    def __init__(self):
        # 预编译正则模式，将多重关键词循环降阶为单次正则扫描
        all_pos_sorted = sorted(self.POSITIVE_WORDS.keys(), key=len, reverse=True)
        all_neg_sorted = sorted(self.NEGATIVE_WORDS.keys(), key=len, reverse=True)
        self._pos_pattern = re.compile(r'(' + '|'.join(re.escape(w) for w in all_pos_sorted) + r')')
        self._neg_pattern = re.compile(r'(' + '|'.join(re.escape(w) for w in all_neg_sorted) + r')')
        # LRU 缓存，避免重复分析相同文本
        self._cache: OrderedDict[str, Dict] = OrderedDict()
        self._max_cache = 500

    def analyze(self, text: str) -> Dict:
        """
        分析文本情感（含贝叶斯平滑与重大风险一票压制）

        Returns: {score, positive_count, negative_count, keywords, confidence, critical_risk}
        """
        if not text:
            return {"score": 50, "positive_count": 0, "negative_count": 0,
                    "keywords": [], "confidence": 0, "critical_risk": False}

        cache_key = text[:200]
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        cleaned_text = self._clean_text(text)
        sentences = re.split(r'[，。！？、；：\n\r\t]+', cleaned_text)

        positive_score = 0.0
        negative_score = 0.0
        positive_keywords = []
        negative_keywords = []
        has_critical_risk = False
        critical_reasons = []

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            # 单次正则扫描负面词
            for m in self._neg_pattern.finditer(sent):
                word = m.group(0)
                idx = m.start()
                base_weight = self.NEGATIVE_WORDS[word]
                # 检查前置否定词（如“未亏损”）
                is_negated = False
                if idx > 0 and sent[idx - 1] in self.NEGATION_WORDS:
                    is_negated = True
                elif idx > 1 and sent[idx - 2:idx] in ("没有", "未能", "并非"):
                    is_negated = True

                # 检查程度副词
                intensity = 1.0
                for adv, mult in self.INTENSIFIERS.items():
                    if idx >= len(adv) and sent[idx - len(adv):idx] == adv:
                        intensity = mult
                        break
                for dim, mult in self.DIMINISHERS.items():
                    if idx >= len(dim) and sent[idx - len(dim):idx] == dim:
                        intensity = mult
                        break

                if is_negated:
                    positive_score += base_weight * 0.7 * intensity
                    positive_keywords.append(f"未{word}")
                else:
                    negative_score += base_weight * intensity
                    negative_keywords.append(word)
                    if word in self.CRITICAL_RISK_WORDS:
                        has_critical_risk = True
                        if word not in critical_reasons:
                            critical_reasons.append(word)

            # 单次正则扫描正面词
            for m in self._pos_pattern.finditer(sent):
                word = m.group(0)
                idx = m.start()
                base_weight = self.POSITIVE_WORDS[word]
                is_negated = False
                if idx > 0 and sent[idx - 1] in self.NEGATION_WORDS:
                    is_negated = True
                elif idx > 1 and sent[idx - 2:idx] in ("没有", "未能", "并非"):
                    is_negated = True

                intensity = 1.0
                for adv, mult in self.INTENSIFIERS.items():
                    if idx >= len(adv) and sent[idx - len(adv):idx] == adv:
                        intensity = mult
                        break
                for dim, mult in self.DIMINISHERS.items():
                    if idx >= len(dim) and sent[idx - len(dim):idx] == dim:
                        intensity = mult
                        break

                if is_negated:
                    negative_score += base_weight * 1.2 * intensity
                    negative_keywords.append(f"未{word}")
                else:
                    positive_score += base_weight * intensity
                    positive_keywords.append(word)

        # 贝叶斯收缩平滑：设置先验中性得分50，先验权重C=4.0
        # 防止单条或少量新闻即算出 0分 或 100分
        total = positive_score + negative_score
        prior_weight = 4.0
        if total > 0:
            bayesian_ratio = (positive_score + 0.5 * prior_weight) / (total + prior_weight)
            score = int(round(bayesian_ratio * 100.0))
        else:
            score = 50

        # 重大风险一票压制机制：命中立案/巨亏/暴雷等，强制压制在极度偏空区间（最高不超过38分）
        if has_critical_risk:
            score = min(score, 35)

        score = max(5, min(95, score))
        confidence = min(1.0, total / 6.0)

        result = {
            "score": score,
            "positive_count": len(positive_keywords),
            "negative_count": len(negative_keywords),
            "positive_score": round(positive_score, 2),
            "negative_score": round(negative_score, 2),
            "keywords": positive_keywords[:5] + negative_keywords[:5],
            "confidence": round(confidence, 2),
            "critical_risk": has_critical_risk,
            "critical_reasons": critical_reasons,
        }

        self._cache[cache_key] = result
        if len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)

        return result

    def _clean_text(self, text: str) -> str:
        """清洗文本"""
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[^\w\s一-鿿]', ' ', text)
        return text


# 全局实例
cn_sentiment = CnSentimentAnalyzer()
