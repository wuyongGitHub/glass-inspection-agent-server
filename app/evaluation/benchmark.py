"""离线评测：确定性路由准确率 + 实体抽取准确率。

不依赖 LLM / 数据库（厂家名缺失时规则自然退化），可本地秒级回归。
"""
from __future__ import annotations

from app.domain.terms import resolve_defect, resolve_glass_type
from app.graph.nodes.router import deterministic_route


def run_router_benchmark(samples: list[dict]) -> dict:
    resolved = correct = 0
    failures: list[dict] = []
    needs_llm: list[dict] = []
    for s in samples:
        got = deterministic_route(s["text"])
        if got is None:
            needs_llm.append({"id": s["id"], "text": s["text"], "expected": s["intent"]})
            continue
        resolved += 1
        if got == s["intent"]:
            correct += 1
        else:
            failures.append({"id": s["id"], "text": s["text"],
                             "expected": s["intent"], "got": got})
    return {
        "resolved": resolved,
        "correct": correct,
        "accuracy": round(correct / resolved * 100, 1) if resolved else 0.0,
        "needs_llm": len(needs_llm),
        "failures": failures,
    }


def run_entity_benchmark(samples: list[dict]) -> dict:
    def_ok = def_total = glass_ok = glass_total = 0
    failures: list[dict] = []
    for s in samples:
        if "defect" not in s:
            continue
        def_total += 1
        got = resolve_defect(s["text"])
        if got == s["defect"]:
            def_ok += 1
        else:
            failures.append({"id": s["id"], "field": "defect",
                             "expected": s["defect"], "got": got})
        if "glass" in s:
            glass_total += 1
            g = resolve_glass_type(s["text"])
            if g == s["glass"]:
                glass_ok += 1
            else:
                failures.append({"id": s["id"], "field": "glass",
                                 "expected": s["glass"], "got": g})
    return {
        "defect_accuracy": round(def_ok / def_total * 100, 1) if def_total else None,
        "glass_accuracy": round(glass_ok / glass_total * 100, 1) if glass_total else None,
        "failures": failures,
    }


def evaluate(samples: list[dict]) -> dict:
    return {"router": run_router_benchmark(samples),
            "entity": run_entity_benchmark(samples)}
