"""LLM / Embedding 工厂：统一走 OpenAI 兼容接口，按需惰性创建。

EMBED_BACKEND=local 时使用内置离线 n-gram 哈希向量（网关无 embedding 模型时的兜底），
否则走 OpenAI 兼容 embeddings 接口。
"""
import hashlib
import math
import os
import re

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings


def get_llm(temperature: float = 0.2) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=temperature,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )


def get_vision_llm(temperature: float = 0) -> ChatOpenAI:
    """视觉模型工厂：用于图片缺陷识别。默认与主模型一致，可用 LLM_VISION_MODEL 单独指定多模态模型。"""
    return ChatOpenAI(
        model=settings.llm_vision_model or settings.llm_model,
        temperature=temperature,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )


def _local_backend_enabled() -> bool:
    return os.getenv("EMBED_BACKEND", "remote").strip().lower() == "local"


class LocalNGramEmbedding:
    """离线字符 n-gram 哈希向量，支持 embed_documents / embed_query，无外部 API 依赖。

    面向小型中文知识库（数百~数千块）演示兜底：按中文字符与英文/数字词做
    n-gram 哈希 + 词频加权并归一化，配合余弦相似度即可完成字面级相关检索。
    """

    def __init__(self, dim: int = 512, n: int = 2):
        self.dim = dim
        self.n = n

    @staticmethod
    def _grams(text: str, n: int):
        norm = text.lower()
        # 连续中文段按“相邻字”滑动产出 n-gram（如 玻璃/色差），英数词按字符滑窗；
        # 长度不超过 n 的词（含单字）整体作为特征。
        for m in re.finditer(r"[a-z0-9]+|[\u4e00-\u9fff]+", norm):
            tok = m.group(0)
            if len(tok) <= n:
                yield tok
            else:
                for i in range(len(tok) - n + 1):
                    yield tok[i : i + n]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for g in self._grams(text, self.n):
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16) % self.dim
            v[h] += 1.0
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v] if norm else v

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def get_embeddings():
    if _local_backend_enabled():
        return LocalNGramEmbedding()
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
