"""统一证据结构：工具返回可解释、可追溯的证据对象。

V2 目标：让 LLM 组织推理，数学/统计结果由代码产生，每条结论都携带
metric / value / baseline / change / source / confidence / calculation_method，
用于最终报告的"事实 / 推断 / 假设 / 建议"四级可信度区分。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

EvidenceKind = Literal["fact", "inference", "hypothesis", "recommendation"]


class Evidence(BaseModel):
    kind: EvidenceKind = "fact"
    claim: str = ""                          # 结论描述（人类可读）
    metric: Optional[str] = None             # 指标名，如 defect_rate_pct
    value: Any = None                        # 当前值
    baseline: Any = None                     # 基线/对比值
    change: Optional[float] = None           # 变化幅度（%），可空
    source: str = ""                         # 数据来源：SQL/RAG/规则/历史案例
    confidence: float = 0.0                  # 置信度 0~1
    calculation_method: str = ""             # 计算方法，如 mean+2σ
    supporting: list[str] = Field(default_factory=list)    # 支持证据
    contradicting: list[str] = Field(default_factory=list)  # 反对证据
    missing_data: list[str] = Field(default_factory=list)  # 缺失数据/待确认项

    def to_line(self) -> str:
        label = {"fact": "事实", "inference": "推断",
                 "hypothesis": "假设", "recommendation": "建议"}[self.kind]
        body = self.claim
        if self.metric is not None:
            body += f"（{self.metric}={self.value}" + (
                f"，基线 {self.baseline}" if self.baseline is not None else "") + "）"
        if self.missing_data:
            body += f"；尚缺：{'、'.join(self.missing_data)}"
        return f"- **[{label}]** {body}"


def fact(claim: str, **kw) -> Evidence:
    return Evidence(kind="fact", claim=claim, **kw)


def inference(claim: str, **kw) -> Evidence:
    return Evidence(kind="inference", claim=claim, **kw)


def hypothesis(claim: str, **kw) -> Evidence:
    return Evidence(kind="hypothesis", claim=claim, **kw)


def recommendation(claim: str, **kw) -> Evidence:
    return Evidence(kind="recommendation", claim=claim, **kw)
