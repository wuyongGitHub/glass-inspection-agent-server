"""SPC 过程控制：单值控制图（I-MR 的 I 部分简化）+ Western Electric 判异规则。

工业场景用于判断过程是否受控，输出中心线 CL、上下控制限 UCL/LCL 及违规点，
供诊断流作为"异常/失控"的确定性证据。
"""
from __future__ import annotations

from app.tools.evidence import Evidence, fact
from app.tools.statistics import mean_std


def spc_control_chart(values, sigma: float = 3.0) -> dict:
    """单值控制图：均值 ± kσ 控制限。

    返回 dict：{center_line, ucl, lcl, sigma, points, violations}
    violations 命中 Western Electric 简化规则：单点越界 / 连续 8 点中心线同侧。
    """
    ms = mean_std(values)
    m, s, n = ms["mean"], ms["std"], ms["n"]
    if m is None or n < 2 or not s:
        return {"center_line": m, "ucl": None, "lcl": None, "sigma": sigma,
                "points": [float(v) for v in values], "violations": []}

    vals = [float(v) for v in values]
    ucl = m + sigma * s
    lcl = m - sigma * s
    violations: list[dict] = []

    # 规则1：单点越出控制限
    for idx, v in enumerate(vals):
        if v > ucl or v < lcl:
            violations.append({"index": idx, "value": v, "rule": f"越出{sigma}σ控制限"})

    # 规则2：连续 8 点在中心线同侧（趋势性偏移）
    run = 0
    side = 0
    for idx, v in enumerate(vals):
        s_ = 1 if v > m else (-1 if v < m else 0)
        if s_ == side and s_ != 0:
            run += 1
        else:
            side = s_
            run = 1 if s_ != 0 else 0
        if run >= 8:
            violations.append({"index": idx, "value": v, "rule": "连续8点在中心线同侧"})
            run = 0  # 重置，避免同一段重复标记

    return {
        "center_line": round(m, 4),
        "ucl": round(ucl, 4),
        "lcl": round(lcl, 4),
        "sigma": sigma,
        "points": vals,
        "violations": violations,
    }


def spc_evidence(values, metric: str = "defect_rate_pct") -> Evidence:
    """把控制图结果转成 Evidence（受控/失控结论）。"""
    chart = spc_control_chart(values)
    if chart["ucl"] is None:
        return fact("数据点不足，无法判定过程是否受控",
                    metric=metric, source="SPC", confidence=0.0)
    if chart["violations"]:
        rules = "、".join(sorted({v["rule"] for v in chart["violations"]}))
        return fact("过程失控：检出率超出统计控制限",
                    metric=metric, value=chart["center_line"],
                    baseline=f"{chart['lcl']}~{chart['ucl']}",
                    source="SPC", confidence=0.9,
                    calculation_method=f"均值±{chart['sigma']}σ；违规规则：{rules}")
    return fact("过程受控：检出率在统计控制限内波动",
                metric=metric, value=chart["center_line"],
                baseline=f"{chart['lcl']}~{chart['ucl']}",
                source="SPC", confidence=0.85,
                calculation_method=f"均值±{chart['sigma']}σ")
