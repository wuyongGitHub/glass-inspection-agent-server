"""轻量向量存储：JSON 落盘 + 余弦相似度检索。

部门知识库规模（数百~数千块）下足够；若后续文档规模显著增长，
可平滑替换为 FAISS / Chroma / 向量数据库，接口保持不变。

检索为「向量余弦 + 查询词法覆盖率」混合排序：本地 n-gram 兜底向量
在短查询 vs 长文本块场景下噪声较大，词法命中可显著改善精确召回；
远程语义 embedding 接入后词法项仍是有效的补充（利于 ΔE/50mm 等术语）。
"""
import json
import math
import os

from app.llm import LocalNGramEmbedding


def _gram_features(text: str) -> set[str]:
    """取文本的中文相邻字 bigram / 英文数字词片段特征集合（与向量生成一致）。"""
    return set(LocalNGramEmbedding._grams(text, 2))


class SimpleVectorStore:
    def __init__(self, path: str):
        self.path = path
        self.chunks: list[dict] = []  # {"text": str, "meta": dict, "vec": list[float]}
        self._feat_cache: dict[str, set[str]] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.chunks = json.load(f)

    def add(self, texts: list[str], metas: list[dict], vectors: list[list[float]]) -> None:
        for t, m, v in zip(texts, metas, vectors):
            self.chunks.append({"text": t, "meta": m, "vec": v})

    @staticmethod
    def _cos(a: list[float], b: list[float]) -> float:
        num = sum(x * y for x, y in zip(a, b))
        da = math.sqrt(sum(x * x for x in a))
        db = math.sqrt(sum(x * x for x in b))
        return num / (da * db) if da and db else 0.0

    def _df_weighted_hits(self, query_grams: set[str]):
        """查询特征 -> IDF 权重：越罕见的词权重越高，抑制“玻璃/要求”等泛词的稀释。"""
        n = max(len(self.chunks), 1)
        df = {g: 0 for g in query_grams}
        for c in self.chunks:
            cset = self._feat_cache.get(c["text"])
            if cset is None:
                cset = _gram_features(c["text"])
                self._feat_cache[c["text"]] = cset
            for g in df:
                if g in cset:
                    df[g] += 1
        return {g: math.log(1.0 + n / (1.0 + d)) for g, d in df.items()}

    def search(self, query_vec: list[float], k: int = 4, query_text: str = "") -> list[dict]:
        if not query_text:
            ranked = sorted(self.chunks, key=lambda c: -self._cos(query_vec, c["vec"]))
            return ranked[:k]
        # 混合排序：向量余弦（语义）+ 词法 IDF 加权覆盖率（术语精确命中）加权融合。
        # 语义项权重自适应：远程 embedding 语义强、本地 n-gram 语义弱，
        # 但两者都从词法命中受益（利于 ΔE/50mm/CS 等罕见术语精确召回）。
        query_grams = _gram_features(query_text)
        weights = self._df_weighted_hits(query_grams)
        denom = sum(weights.values()) or 1.0
        scored: list[tuple[float, dict]] = []
        cos_scores: list[float] = []
        for c in self.chunks:
            cos_scores.append(self._cos(query_vec, c["vec"]))
        cos_max = max(cos_scores) if cos_scores else 0.0
        for idx, c in enumerate(self.chunks):
            cset = self._feat_cache.get(c["text"])
            if cset is None:
                cset = _gram_features(c["text"])
                self._feat_cache[c["text"]] = cset
            lexical = sum(weights[g] for g in query_grams & cset) / denom
            # 余弦归一化到 [0,1]；若无语义信号则纯词法
            semantic = (cos_scores[idx] / cos_max) if cos_max > 0 else 0.0
            score = 0.55 * semantic + 0.45 * lexical
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:k]]

    def save(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.chunks, f, ensure_ascii=False)
