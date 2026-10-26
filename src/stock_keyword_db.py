"""
股票关键词数据库
基于akshare自动构建，无需手动维护
"""

import logging
from typing import Dict, List, Set
from pathlib import Path
import json

logger = logging.getLogger(__name__)

# 配置目录
from paths import CONFIG_DIR
KEYWORD_DB_FILE = CONFIG_DIR / "stock_keywords.json"


class StockKeywordDB:
    """股票关键词数据库"""

    # 板块类型关键词映射
    CONCEPT_KEYWORDS = {
        "人工智能": ["AI", "算法", "算力", "大模型", "智能", "深度学习"],
        "芯片": ["芯片", "半导体", "集成电路", "晶圆", "光刻"],
        "新能源": ["电池", "储能", "光伏", "风电", "新能源"],
        "锂电池": ["电池", "锂电", "正极", "负极", "电解液"],
        "白酒": ["白酒", "茅台", "五粮液", "消费升级", "提价"],
        "医药": ["医药", "创新药", "医保", "集采", "研发"],
        "军工": ["军工", "国防", "航天", "导弹", "航空"],
        "银行": ["银行", "信贷", "存款", "贷款", "息差"],
        "证券": ["券商", "经纪", "投行", "资管", "两融"],
        "房地产": ["地产", "房价", "土地", "销售", "竣工"],
        "汽车": ["汽车", "新能源车", "自动驾驶", "充电桩"],
        "消费": ["消费", "零售", "电商", "品牌", "渠道"],
        "科技": ["科技", "互联网", "软件", "云计算", "大数据"],
        "5G": ["5G", "通信", "基站", "光纤", "物联网"],
    }

    def __init__(self):
        self._stock_concepts: Dict[str, List[str]] = {}  # 股票 → 概念板块
        self._stock_industries: Dict[str, str] = {}     # 股票 → 行业
        self._stock_keywords: Dict[str, Dict] = {}      # 股票 → 关键词
        self._concept_stocks: Dict[str, List[str]] = {} # 概念 → 股票列表
        self._sentiment_lexicon: Dict = {}              # 情感词库
        self._load()
        self._load_sentiment_lexicon()

    def _load(self):
        """加载缓存"""
        try:
            if KEYWORD_DB_FILE.exists():
                with open(KEYWORD_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._stock_concepts = data.get("stock_concepts", {})
                    self._stock_industries = data.get("stock_industries", {})
                    self._stock_keywords = data.get("stock_keywords", {})
                    self._concept_stocks = data.get("concept_stocks", {})
                logger.info(f"加载关键词数据库: {len(self._stock_keywords)} 只股票")
        except Exception as e:
            logger.error(f"加载关键词数据库失败: {e}")

    def _load_sentiment_lexicon(self):
        """加载情感词库的股票特定关键词"""
        try:
            from paths import CONFIG_DIR
            lexicon_file = CONFIG_DIR / "sentiment_lexicon.json"
            if lexicon_file.exists():
                with open(lexicon_file, "r", encoding="utf-8") as f:
                    self._sentiment_lexicon = json.load(f)
                    # 合并股票特定关键词
                    for code, kws in self._sentiment_lexicon.get("stock_specific", {}).items():
                        if code not in self._stock_keywords:
                            self._stock_keywords[code] = {
                                "concepts": [],
                                "industry": "",
                                "keywords": kws
                            }
                        else:
                            self._stock_keywords[code]["keywords"] = kws
                logger.info(f"加载情感词库股票关键词: {len(self._sentiment_lexicon.get('stock_specific', {}))} 只股票")
        except Exception as e:
            logger.error(f"加载情感词库失败: {e}")

    def _save(self):
        """保存缓存"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(KEYWORD_DB_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "stock_concepts": self._stock_concepts,
                    "stock_industries": self._stock_industries,
                    "stock_keywords": self._stock_keywords,
                    "concept_stocks": self._concept_stocks,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存关键词数据库失败: {e}")

    def build_database(self, stock_codes: List[str] = None):
        """
        构建关键词数据库

        Args:
            stock_codes: 股票代码列表（None=从概念板块自动获取）
        """
        logger.info("开始构建关键词数据库...")

        # 1. 获取概念板块
        self._build_concept_data()

        # 2. 获取行业板块
        self._build_industry_data()

        # 3. 为每只股票生成关键词（从概念板块获取股票列表）
        all_codes = set()
        for codes in self._concept_stocks.values():
            all_codes.update(codes)
        if stock_codes:
            all_codes.update(stock_codes)

        logger.info(f"为 {len(all_codes)} 只股票生成关键词...")
        for code in list(all_codes)[:500]:  # 限制500只
            self._generate_keywords(code)

        self._save()
        logger.info(f"关键词数据库构建完成: {len(self._stock_keywords)} 只股票")

    def _build_concept_data(self):
        """构建概念板块数据"""
        try:
            import akshare as ak

            # 获取概念板块列表
            concepts = ak.stock_board_concept_name_em()
            logger.info(f"获取概念板块: {len(concepts)} 个")

            # 获取每个概念的成分股（只取前20个热门概念）
            for _, row in concepts.head(20).iterrows():
                concept_name = row.get("板块名称", "")
                if not concept_name:
                    continue

                try:
                    stocks = ak.stock_board_concept_cons_em(symbol=concept_name)
                    if stocks is not None and not stocks.empty:
                        stock_codes = stocks["代码"].tolist()
                        self._concept_stocks[concept_name] = stock_codes

                        # 为每个股票添加概念
                        for code in stock_codes:
                            if code not in self._stock_concepts:
                                self._stock_concepts[code] = []
                            if concept_name not in self._stock_concepts[code]:
                                self._stock_concepts[code].append(concept_name)
                except Exception:
                    continue

            logger.info(f"概念板块数据: {len(self._concept_stocks)} 个概念")
        except Exception as e:
            logger.error(f"构建概念板块数据失败: {e}")

    def _build_industry_data(self):
        """构建行业板块数据"""
        try:
            import akshare as ak

            # 获取行业板块列表
            industries = ak.stock_board_industry_name_em()
            logger.info(f"获取行业板块: {len(industries)} 个")

            # 获取每个行业的成分股（只取前30个行业）
            for _, row in industries.head(30).iterrows():
                industry_name = row.get("板块名称", "")
                if not industry_name:
                    continue

                try:
                    stocks = ak.stock_board_industry_cons_em(symbol=industry_name)
                    if stocks is not None and not stocks.empty:
                        for _, stock_row in stocks.iterrows():
                            code = stock_row.get("代码", "")
                            if code:
                                self._stock_industries[code] = industry_name
                except Exception:
                    continue

            logger.info(f"行业板块数据: {len(self._stock_industries)} 只股票")
        except Exception as e:
            logger.error(f"构建行业板块数据失败: {e}")

    def _generate_keywords(self, code: str):
        """为股票生成关键词"""
        keywords = {"positive": [], "negative": [], "neutral": []}

        # 从概念板块获取关键词（模糊匹配）
        concepts = self._stock_concepts.get(code, [])
        for concept in concepts:
            # 精确匹配
            concept_kws = self.CONCEPT_KEYWORDS.get(concept, [])
            # 模糊匹配：概念名包含关键词
            for kw_name, kw_list in self.CONCEPT_KEYWORDS.items():
                if kw_name in concept or concept in kw_name:
                    concept_kws.extend(kw_list)
            keywords["positive"].extend(concept_kws)

        # 从行业获取关键词（模糊匹配）
        industry = self._stock_industries.get(code, "")
        if industry:
            for kw_name, kw_list in self.CONCEPT_KEYWORDS.items():
                if kw_name in industry or industry in kw_name:
                    keywords["positive"].extend(kw_list)

        # 去重
        keywords["positive"] = list(set(keywords["positive"]))
        keywords["negative"] = list(set(keywords["negative"]))
        keywords["neutral"] = list(set(keywords["neutral"]))

        self._stock_keywords[code] = {
            "concepts": concepts,
            "industry": industry,
            "keywords": keywords,
        }

    def get_stock_keywords(self, code: str) -> Dict:
        """获取股票关键词"""
        if code in self._stock_keywords:
            return self._stock_keywords[code]["keywords"]

        # 如果没有缓存，尝试生成
        self._generate_keywords(code)
        return self._stock_keywords.get(code, {}).get("keywords", {"positive": [], "negative": [], "neutral": []})

    def get_stock_concepts(self, code: str) -> List[str]:
        """获取股票所属概念"""
        return self._stock_concepts.get(code, [])

    def get_stock_industry(self, code: str) -> str:
        """获取股票所属行业"""
        return self._stock_industries.get(code, "")

    def get_concept_stocks(self, concept: str) -> List[str]:
        """获取概念板块的成分股"""
        return self._concept_stocks.get(concept, [])

    def search_stocks_by_keyword(self, keyword: str) -> List[str]:
        """根据关键词搜索股票"""
        results = []
        for code, data in self._stock_keywords.items():
            keywords = data.get("keywords", {})
            all_kws = keywords.get("positive", []) + keywords.get("negative", [])
            if keyword in all_kws:
                results.append(code)
        return results

    def get_database_stats(self) -> Dict:
        """获取数据库统计"""
        return {
            "total_stocks": len(self._stock_keywords),
            "total_concepts": len(self._concept_stocks),
            "total_industries": len(self._stock_industries),
            "concept_samples": list(self._concept_stocks.keys())[:10],
            "industry_samples": list(set(self._stock_industries.values()))[:10],
        }


# 全局实例
stock_keyword_db = StockKeywordDB()
