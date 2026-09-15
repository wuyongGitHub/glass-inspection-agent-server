"""视觉分析流：现场图片 -> 视觉大模型识别缺陷 -> 检索知识/案例 -> 输出诊断性回答。

与诊断流共享证据/案例检索能力，但入口是图片（state["images"] / state["image"]）而非文本数据查询。

支持单图与多图：多张图片视为同一批现场照片（如同一块玻璃的不同角度），
逐张识别后合并为一份诊断意见。
"""
from __future__ import annotations

from langchain_core.messages import SystemMessage

from app.cases.store import search_cases
from app.config import settings
from app.domain import DEFECT_GUIDANCE
from app.formatting import FORMAT_GUIDE
from app.graph.nodes.diagnosis import _retrieve_knowledge
from app.llm import get_llm
from app.state import AgentState
from app.tools.evidence import Evidence, fact, inference
from app.tools.vision import analyze_defect_image, extract_image_text

_VISION_REPORT = """你是玻璃检测诊断专家。根据视觉识别结果、缺陷工艺对照、知识库与历史案例，
输出一份简明的图片诊断意见。

材料：
- 视觉识别结果（JSON 列表，每项对应一张图）：{finding}
- 缺陷-工艺对照：{guidance}
- 知识库片段：{knowledge}
- 历史案例：{cases}

要求：
1. 用「<strong style="color:#1f6feb">🔎 识别结果</strong>」客观转述各图识别到的缺陷（类型/严重度/描述），标注视觉置信度；
2. 用「<strong style="color:#1f6feb">🧩 成因分析</strong>」结合对照表与知识库给出可能成因（标注"疑似/待确认"）；
3. 用「<strong style="color:#1f6feb">🛠️ 复核与改善</strong>」给出可执行的复核/改善动作；简体中文，300 字以内。

""" + FORMAT_GUIDE


def _collect_images(state: AgentState) -> list[str]:
    """合并单图 image 与多图 images 字段，去空。"""
    images: list[str] = []
    single = state.get("image")
    if single:
        images.append(single)
    images.extend(state.get("images") or [])
    return [img for img in images if img and img.strip()]


_DOCUMENT_PROMPT = """你是玻璃检测部门的智能助理。用户上传了一张文档/文字图片（并非玻璃缺陷照片）。
请基于下方识别出的文字内容，用简体中文简洁回应：
1. 先说明：这是一张文档/文字图片，已识别其中的文字；
2. 若文字与玻璃检测业务相关，做简要归纳或回答；
3. 若是其他报告（超声/体检/合同等），客观转述要点，并提醒这属于非玻璃检测内容。

识别文字：
{text}

""" + FORMAT_GUIDE


def _document_answer(images: list[str], doc_findings: list[dict]) -> dict:
    """文档/文字图片：优先视觉模型转录文字，不足时用本地 OCR 增强，再交由 LLM 归纳。"""
    parts = [(f.get("text_content") or "").strip() for f in doc_findings]
    text = "\n".join(p for p in parts if p)

    # 视觉转录过短/为空时，用本地 OCR 兜底（更精确，引擎未装则自动跳过）
    if len(text.strip()) < 20 and images:
        ocr_text = "\n".join(
            t.strip() for img in images if (t := extract_image_text(img)).strip()
        )
        if len(ocr_text) > len(text):
            text = ocr_text

    if not text.strip():
        return {
            "intent": "vision", "evidence": [], "cases": [], "citations": [],
            "final_answer": "这是一张文档/文字图片，但未能提取到文字内容。请确认图片清晰度后重新上传。",
            "chart_config": None,
        }

    prompt = _DOCUMENT_PROMPT.format(text=text[:4000])
    try:
        answer = get_llm(temperature=0.2).invoke(
            [SystemMessage(content=prompt), SystemMessage(content="请基于识别文字作答")]
        )
        body = answer.content
    except Exception:
        body = "这张图片为文档/文字图片，识别到的文字内容如下：\n\n" + text
    return {
        "intent": "vision", "evidence": [], "cases": [], "citations": [],
        "final_answer": body, "chart_config": None,
    }


def vision_analyze(state: AgentState) -> dict:
    images = _collect_images(state)
    hint = ""
    for m in reversed(state.get("messages") or []):
        if getattr(m, "type", "") == "human":
            c = getattr(m, "content", "")
            if isinstance(c, str):
                hint = c
                break

    # 逐张识别（含内容类型分类：glass_defect / document / other）
    findings = [analyze_defect_image(img, hint) for img in images] if images else []
    if not findings:
        findings = [analyze_defect_image("", hint)]

    defect_findings = [f for f in findings if f.get("content_type") == "glass_defect"]
    doc_findings = [f for f in findings if f.get("content_type") == "document"]

    # 文档/文字图（且无玻璃缺陷图）→ 提取文字，不套缺陷诊断
    if not defect_findings and doc_findings:
        return _document_answer(images, doc_findings)

    # 既非缺陷也非文档（无关图/空图）→ 友好提示
    if not defect_findings:
        desc = (findings[0].get("description") or "").strip()
        return {
            "intent": "vision", "evidence": [], "cases": [], "citations": [],
            "final_answer": (
                "这张图片看起来既不是玻璃缺陷照片，也不是文字文档"
                + (f"（{desc}）。" if desc else "。")
                + "请上传玻璃表面/内部照片，或含文字的文档图片。"
            ),
            "chart_config": None,
        }

    # 玻璃缺陷图 → 走原缺陷诊断流程（仅用 defect_findings）
    defects: list[str] = []
    for f in defect_findings:
        d = f.get("defect_type")
        if d and d not in ("未知", "无缺陷", None) and d not in defects:
            defects.append(d)
    primary = defects[0] if defects else None

    evidence: list[Evidence] = []
    if defects:
        for d in defects:
            # 找到该缺陷对应的置信度（取第一个命中者）
            conf = next(
                (f.get("confidence") for f in defect_findings if f.get("defect_type") == d),
                None,
            )
            evidence.append(fact(
                f"视觉识别检出「{d}」缺陷，置信度 {conf}",
                metric="vision_defect", value=d, source="视觉模型",
                confidence=float(conf or 0.5),
                calculation_method="多模态大模型识别",
            ))
            if d in DEFECT_GUIDANCE:
                evidence.append(inference(
                    f"「{d}」典型工艺成因：{DEFECT_GUIDANCE[d]}",
                    source="领域对照表", confidence=0.6,
                ))
    else:
        evidence.append(fact(
            defect_findings[0].get("description") or "视觉识别未给出明确缺陷结论。",
            source="视觉模型", confidence=0.3,
        ))

    knowledge, citations = _retrieve_knowledge(primary)
    cases = search_cases(defect=primary, top_k=3)
    if cases:
        top = cases[0]
        evidence.append(fact(
            f"匹配相似历史案例「{top.get('defect_type')}·{top.get('factory')}」，"
            f"根因：{top.get('root_cause')}",
            source="案例库", confidence=0.7,
        ))

    cases_text = "\n".join(
        f"- 「{c.get('defect_type')}·{c.get('factory')}」根因：{c.get('root_cause')}"
        for c in cases[:3]
    )
    prompt = _VISION_REPORT.format(
        finding=str(defect_findings),
        guidance=DEFECT_GUIDANCE.get(primary, "（未命中具体缺陷）") if primary else "（未命中具体缺陷）",
        knowledge="\n".join(knowledge[:3]) or "（无）",
        cases=cases_text or "（无相似历史案例）",
    )
    try:
        answer = get_llm(temperature=0.3).invoke(
            [SystemMessage(content=prompt), SystemMessage(content="请输出图片诊断意见")]
        )
        body = answer.content
    except Exception:
        body = "\n".join(e.to_line() for e in evidence) or "（模型服务不可用，未能生成诊断意见）"

    if citations:
        body += "\n\n**引用来源**：" + "、".join(sorted(set(citations)))
    return {
        "intent": "vision",
        "evidence": [e.model_dump() for e in evidence],
        "cases": cases,
        "citations": citations,
        "final_answer": body,
        "chart_config": None,
    }
