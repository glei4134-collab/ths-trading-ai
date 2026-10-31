"""
NLP情感分析模块
使用轻量级中文情感分析与否定词规则融合，毫秒级响应，避免外网模型阻塞
"""

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


class NLPSentimentAnalyzer:
    """NLP情感分析器"""

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._load_failed = False

    def _load_model(self):
        """加载本地缓存的预训练模型（仅限本地缓存，不触发外网下载阻塞）"""
        if self._loaded or self._load_failed:
            return

        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification

            models = [
                "lxyuan/distilbert-base-multilingual-cased-sentiments-student",
                "nlptown/bert-base-multilingual-uncased-sentiment",
                "cardiffnlp/twitter-roberta-base-sentiment-latest",
            ]

            for model_name in models:
                try:
                    self._tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
                    self._model = AutoModelForSequenceClassification.from_pretrained(model_name, local_files_only=True)
                    self._model.eval()
                    self._loaded = True
                    logger.info(f"成功加载本地NLP模型: {model_name}")
                    return
                except Exception:
                    continue

            self._load_failed = True
        except Exception:
            self._loaded = False
            self._load_failed = True

    def analyze(self, text: str) -> Dict:
        """
        分析文本情感
        Returns: {score, confidence, label, details}
        """
        if not text or not text.strip():
            return {"score": 50, "confidence": 0, "label": "neutral", "details": "empty"}

        # 1. 中文金融否定词与程度副词情感引擎（毫秒级）
        critical_risk = False
        try:
            from cn_sentiment import cn_sentiment
            cn_res = cn_sentiment.analyze(text)
            cn_score = cn_res.get("score", 50)
            critical_risk = cn_res.get("critical_risk", False)
        except Exception:
            cn_score = 50

        # 2. 优先结合 SnowNLP 逐句情感均值（防止长文本连乘下溢）
        try:
            from snownlp import SnowNLP
            parts = [p.strip() for p in re.split(r'[\n|；;。！？]+', text) if len(p.strip()) >= 4]
            if not parts:
                parts = [text[:200]]
            snow_scores = [SnowNLP(p).sentiments * 100 for p in parts[:15]]
            snow_avg = sum(snow_scores) / len(snow_scores) if snow_scores else 50.0
            score = int(snow_avg * 0.5 + cn_score * 0.5)
            if critical_risk:
                score = min(score, 35)
            label = "positive" if score > 58 else "negative" if score < 42 else "neutral"
            return {"score": score, "confidence": 0.7, "label": label, "details": "snownlp+cn_sentiment", "critical_risk": critical_risk}
        except Exception:
            score = int(cn_score)
            if critical_risk:
                score = min(score, 35)
            label = "positive" if score > 58 else "negative" if score < 42 else "neutral"
            return {"score": score, "confidence": 0.65, "label": label, "details": "cn_sentiment", "critical_risk": critical_risk}

    def _split_text(self, text: str, max_length: int = 500) -> List[str]:
        """分割长文本"""
        if len(text) <= max_length:
            return [text]

        sentences = re.split(r'[。！？\n]+', text)
        texts = []
        current = ""

        for s in sentences:
            if len(current) + len(s) < max_length:
                current += s + "。"
            else:
                if current:
                    texts.append(current)
                current = s + "。"

        if current:
            texts.append(current)

        return texts[:5]


# 全局实例
nlp_sentiment = NLPSentimentAnalyzer()
