"""
NLP情感分析模块
使用预训练的中文情感分析模型
"""

import logging
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
        """加载预训练模型"""
        if self._loaded:
            return
        if self._load_failed:
            return  # 已经失败过，不再重试

        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch

            # 尝试多个模型，按优先级排序
            models = [
                "lxyuan/distilbert-base-multilingual-cased-sentiments-student",  # 多语言情感
                "nlptown/bert-base-multilingual-uncased-sentiment",  # 多语言情感
                "cardiffnlp/twitter-roberta-base-sentiment-latest",  # 社交媒体
            ]

            for model_name in models:
                try:
                    logger.info(f"尝试加载模型: {model_name}")
                    self._tokenizer = AutoTokenizer.from_pretrained(model_name)
                    self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
                    self._model.eval()
                    self._loaded = True
                    logger.info(f"成功加载模型: {model_name}")
                    return
                except Exception as e:
                    logger.warning(f"模型 {model_name} 加载失败: {e}")
                    continue

            logger.error("所有模型加载失败")
        except Exception as e:
            logger.error(f"加载情感分析模型失败: {e}")
            self._loaded = False
            self._load_failed = True  # 标记失败，避免反复重试

    def analyze(self, text: str) -> Dict:
        """
        分析文本情感

        Returns: {score, confidence, label, details}
        """
        # 优先用 snownlp（轻量，无需 GPU）
        try:
            from snownlp import SnowNLP
            s = SnowNLP(text)
            score = int(s.sentiments * 100)  # 0-100
            label = "positive" if score > 60 else "negative" if score < 40 else "neutral"
            return {"score": score, "confidence": 0.6, "label": label, "details": "snownlp"}
        except Exception:
            pass

        # 备用：transformers（需要 GPU）
        if not self._loaded:
            self._load_model()

        if not self._loaded:
            return {"score": 50, "confidence": 0, "label": "neutral", "details": "模型未加载"}

        try:
            import torch

            # 分割长文本（模型限制512 tokens）
            texts = self._split_text(text, max_length=500)

            # 分析每段文本
            scores = []
            for t in texts:
                inputs = self._tokenizer(t, return_tensors="pt", truncation=True, max_length=512)
                with torch.no_grad():
                    outputs = self._model(**inputs)
                    probs = torch.softmax(outputs.logits, dim=1)

                # 获取正面/负面/中性概率
                # 模型输出: [negative, neutral, positive]
                neg_prob = probs[0][0].item()
                neu_prob = probs[0][1].item()
                pos_prob = probs[0][2].item()

                # 计算分数 (0-100)
                score = int(pos_prob * 100)
                scores.append(score)

            # 平均分数
            avg_score = sum(scores) // len(scores) if scores else 50

            # 获取置信度
            max_prob = max(neg_prob, neu_prob, pos_prob)
            confidence = round(max_prob, 2)

            # 获取标签
            if avg_score > 60:
                label = "positive"
            elif avg_score < 40:
                label = "negative"
            else:
                label = "neutral"

            return {
                "score": avg_score,
                "confidence": confidence,
                "label": label,
                "details": {
                    "positive_prob": round(pos_prob, 3),
                    "negative_prob": round(neg_prob, 3),
                    "neutral_prob": round(neu_prob, 3),
                }
            }
        except Exception as e:
            logger.error(f"NLP分析失败: {e}")
            return {"score": 50, "confidence": 0, "label": "neutral", "details": str(e)}

    def _split_text(self, text: str, max_length: int = 500) -> List[str]:
        """分割长文本"""
        if len(text) <= max_length:
            return [text]

        # 按句子分割
        import re
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

        return texts[:5]  # 最多5段


# 全局实例
nlp_sentiment = NLPSentimentAnalyzer()
