"""
轻量级中文情感分析模块
基于规则+词典，无需大模型
"""

import re
from typing import Dict, List, Tuple


class CnSentimentAnalyzer:
    """中文情感分析器"""

    # 情感词典
    POSITIVE_WORDS = {
        # 强正面
        "暴涨": 2.0, "涨停": 1.8, "新高": 1.6, "创新高": 1.6,
        "大幅上涨": 1.5, "强势突破": 1.5, "飙升": 1.8,
        # 中等正面
        "利好": 1.2, "上涨": 1.0, "突破": 1.1, "增长": 1.1,
        "盈利": 1.1, "超预期": 1.2, "反弹": 1.0, "放量": 1.0,
        "金叉": 1.0, "龙头": 1.1, "热点": 1.0, "资金流入": 1.1,
        "业绩增长": 1.1, "净利润增长": 1.2, "营收增长": 1.1,
        "政策利好": 1.2, "机构买入": 1.1, "北向资金买入": 1.0,
        "回购": 1.1, "增持": 1.1, "分红": 1.0, "高送转": 1.1,
        # 弱正面
        "牛市": 0.9, "强势": 0.9, "企稳": 0.8, "回暖": 0.8,
        "震荡上行": 0.8, "底部放量": 0.9, "突破压力": 1.0,
        "站上均线": 0.8, "多头排列": 0.9, "量价配合": 0.9,
    }

    NEGATIVE_WORDS = {
        # 强负面
        "暴跌": 2.0, "崩盘": 2.0, "跌停": 1.8, "新低": 1.6,
        "大幅下跌": 1.5, "破位下跌": 1.5, "闪崩": 1.8,
        # 中等负面
        "利空": 1.2, "下跌": 1.0, "破位": 1.1, "亏损": 1.1,
        "下滑": 1.0, "不及预期": 1.2, "业绩下滑": 1.2,
        "缩量": 0.9, "死叉": 1.0, "资金流出": 1.1, "机构卖出": 1.0,
        "政策收紧": 1.1, "减持": 1.1, "质押": 0.9, "商誉减值": 1.1,
        "诉讼": 1.0, "处罚": 1.1, "退市": 1.5, "ST": 1.3,
        # 弱负面
        "熊市": 0.9, "弱势": 0.9, "低迷": 0.8, "疲软": 0.8,
        "震荡下行": 0.8, "跌破支撑": 0.9, "空头排列": 0.9,
        "缩量下跌": 0.8, "阴跌": 0.8, "套牢": 0.9,
    }

    # 否定词（会反转情感）
    NEGATION_WORDS = ["不", "没", "未", "无", "非", "别", "莫", "勿"]

    # 程度副词（会加强情感）
    INTENSIFIERS = ["很", "非常", "极其", "十分", "特别", "相当", "极为"]
    DIMINISHERS = ["稍", "略", "微", "有点", "稍微"]

    def analyze(self, text: str) -> Dict:
        """
        分析文本情感

        Returns: {score, positive_count, negative_count, keywords, confidence}
        """
        if not text:
            return {"score": 50, "positive_count": 0, "negative_count": 0,
                    "keywords": [], "confidence": 0}

        # 清洗文本
        text = self._clean_text(text)

        # 分词（简单按标点和空格分割）
        words = self._tokenize(text)

        positive_score = 0
        negative_score = 0
        positive_keywords = []
        negative_keywords = []

        i = 0
        while i < len(words):
            word = words[i]

            # 检查否定词
            negated = False
            if i > 0 and words[i-1] in self.NEGATION_WORDS:
                negated = True

            # 检查程度副词
            intensity = 1.0
            if i > 0 and words[i-1] in self.INTENSIFIERS:
                intensity = 1.5
            elif i > 0 and words[i-1] in self.DIMINISHERS:
                intensity = 0.5

            # 检查情感词
            if word in self.POSITIVE_WORDS:
                score = self.POSITIVE_WORDS[word] * intensity
                if negated:
                    negative_score += score
                    negative_keywords.append(f"不{word}")
                else:
                    positive_score += score
                    positive_keywords.append(word)

            elif word in self.NEGATIVE_WORDS:
                score = self.NEGATIVE_WORDS[word] * intensity
                if negated:
                    positive_score += score
                    positive_keywords.append(f"不{word}")
                else:
                    negative_score += score
                    negative_keywords.append(word)

            i += 1

        # 计算分数
        total = positive_score + negative_score
        if total > 0:
            score = int((positive_score / total) * 100)
        else:
            score = 50

        # 计算置信度
        confidence = min(1.0, total / 5)  # 5分以上置信度较高

        return {
            "score": score,
            "positive_count": len(positive_keywords),
            "negative_count": len(negative_keywords),
            "positive_score": round(positive_score, 2),
            "negative_score": round(negative_score, 2),
            "keywords": positive_keywords[:5] + negative_keywords[:5],
            "confidence": round(confidence, 2),
        }

    def _clean_text(self, text: str) -> str:
        """清洗文本"""
        # 去除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        # 去除特殊字符
        text = re.sub(r'[^\w\s一-鿿]', ' ', text)
        return text

    def _tokenize(self, text: str) -> List[str]:
        """中文分词（滑动窗口匹配）"""
        tokens = []
        # 1. 先按标点分割成句子
        sentences = re.split(r'[，。！？、；：]+', text)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # 2. 对每个句子进行滑动窗口匹配
            # 检查所有情感词
            for word in list(self.POSITIVE_WORDS.keys()) + list(self.NEGATIVE_WORDS.keys()):
                if word in sentence:
                    tokens.append(word)

            # 3. 按空格分割（英文单词）
            words = sentence.split()
            for word in words:
                if word in self.POSITIVE_WORDS or word in self.NEGATIVE_WORDS:
                    tokens.append(word)

        return tokens


# 全局实例
cn_sentiment = CnSentimentAnalyzer()
