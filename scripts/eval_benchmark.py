"""运行离线评测：python scripts/eval_benchmark.py

打印确定性路由准确率与实体抽取准确率，并列出失败样本。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.benchmark import evaluate  # noqa: E402
from app.evaluation.samples import SAMPLES  # noqa: E402


def main() -> None:
    report = evaluate(SAMPLES)
    r = report["router"]
    e = report["entity"]
    print("=" * 60)
    print("路由评测（确定性规则层，不含 LLM 兜底）")
    print(f"  规则命中 {r['resolved']} 条，正确 {r['correct']} 条，"
          f"准确率 {r['accuracy']}%，需 LLM 兜底 {r['needs_llm']} 条")
    print("实体抽取")
    print(f"  缺陷准确率 {e['defect_accuracy']}%，玻璃类型准确率 {e['glass_accuracy']}%")
    if r["failures"] or e["failures"]:
        print("失败样本：")
        for f in r["failures"] + e["failures"]:
            print(f"  {f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
