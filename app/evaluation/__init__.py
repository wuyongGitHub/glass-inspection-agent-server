"""评估与回归测试基线（P0）。

目标：不靠人工感觉判断升级是否有效。提供确定性路由与实体抽取的离线评测，
后续再扩展 RAG Recall@K、rerank NDCG、诊断证据充分率、幻觉率等指标。
"""
from app.evaluation.benchmark import evaluate  # noqa: F401
from app.evaluation.samples import SAMPLES  # noqa: F401
