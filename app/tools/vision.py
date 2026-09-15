"""多模态视觉检测工具（V2）：把现场图片交给视觉大模型，识别玻璃缺陷。

输入支持：
- 图片 URL（http/https）
- base64 Data URL（data:image/...;base64,...）

返回结构化 VisionFinding：缺陷类型 / 严重度 / 置信度 / 描述 / 可能成因，
供诊断流作为第一手证据消费，与 SQL 统计、RAG、历史案例共同组成证据链。

说明：视觉模型走 OpenAI 兼容多模态接口（ChatOpenAI + image_url content），
若网关/模型不支持视觉输入，调用会失败并回退为友好提示，不影响文本链路。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import settings
from app.llm import get_vision_llm

_VISION_SYSTEM = """你是玻璃检测部门的多模态图片理解助手。先判断图片内容类型，再按类型提取信息。

第一步，判断 content_type（三选一）：
- glass_defect：玻璃表面/内部照片，可能含有气泡/划伤/崩边/结石/裂纹/污渍/色差等缺陷；
- document：图片主体是文档、报告、表格、文字、票据、单据、截图、书页等，核心信息是文字；
- other：与玻璃检测和文字文档都无关的图片（风景、人物、纯色、无关物体等）。

第二步，按类型输出一个 JSON 对象：
- content_type：上述三类之一。
- defect_type：仅 glass_defect 时填缺陷类型（气泡/划伤/崩边/结石/裂纹/污渍/色差/无缺陷 之一，无法判断则"未知"）
- severity：仅 glass_defect 时填严重度（轻微/一般/严重/未知）
- confidence：0~1 的置信度小数
- description：对图片的客观描述（50 字以内）
- possible_causes：仅 glass_defect 时填可能的工艺成因数组（1~3 条）
- text_content：仅 document 时填，尽量完整、逐行转录图片中的文字内容

不要编造图中不存在的内容；看不清就标"未知"。"""


class VisionFinding(BaseModel):
    content_type: str = Field(
        default="glass_defect",
        description="图片内容类型：glass_defect=玻璃缺陷图 / document=文档文字图 / other=其他无关图",
    )
    defect_type: Optional[str] = Field(default=None, description="缺陷类型")
    severity: Optional[str] = Field(default=None, description="严重度：轻微/一般/严重")
    confidence: Optional[float] = Field(default=None, description="置信度 0~1")
    description: str = Field(default="", description="缺陷描述")
    possible_causes: list[str] = Field(default_factory=list, description="可能成因")
    text_content: str = Field(default="", description="文档类图片中提取的文字内容")


def _is_data_url(image: str) -> bool:
    return image.strip().lower().startswith("data:")


def _extract_json(text: str) -> dict | None:
    """从 LLM 文本中稳健抽取 JSON 对象（兜底，防止结构化输出失败）。"""
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def analyze_defect_image(image: str, hint: Optional[str] = None) -> dict:
    """调用视觉大模型识别玻璃缺陷，返回 VisionFinding 字典。

    失败时返回 {"defect_type": None, "description": <错误提示>}，不抛异常。
    """
    if not image or not image.strip():
        return {"content_type": "other", "defect_type": None, "severity": None,
                "confidence": None, "description": "未提供图片。",
                "possible_causes": [], "text_content": ""}

    text = "请判断图片内容类型并提取信息。" + (f"补充线索：{hint}" if hint else "")
    content: list[dict] = [{"type": "text", "text": text}]
    content.append({"type": "image_url", "image_url": {"url": image.strip()}})

    try:
        llm = get_vision_llm(temperature=0)
        msg = HumanMessage(content=content)
        try:
            finding = (
                llm.with_structured_output(VisionFinding, method="json_mode")
                .invoke([SystemMessage(content=_VISION_SYSTEM), msg])
            )
            return finding.model_dump()
        except Exception:
            resp = llm.invoke([SystemMessage(content=_VISION_SYSTEM), msg])
            raw = resp.content if isinstance(resp.content, str) else str(resp.content)
            data = _extract_json(raw)
            if data:
                return VisionFinding(**data).model_dump()
            return {"content_type": "other", "defect_type": None, "severity": None,
                    "confidence": None, "description": raw[:200],
                    "possible_causes": [], "text_content": ""}
    except Exception as e:
        return {
            "content_type": "other", "defect_type": None, "severity": None,
            "confidence": None,
            "description": f"视觉识别不可用（{type(e).__name__}）：请确认模型支持图片输入。",
            "possible_causes": [], "text_content": "",
        }


def extract_image_text(image: str) -> str:
    """对文档类图片尽力 OCR 提取文字（可选增强）。

    视觉模型已能转录文字（text_content），本函数在需要更精确文本时兜底调用：
    把 data URL / http(s) URL 落盘后交给 app.rag.ocr.ocr_image；引擎未安装或
    OCR 关闭时返回空串，绝不抛异常。
    """
    import base64
    import os
    import re
    import tempfile
    import urllib.request

    s = image.strip()
    data: bytes | None = None
    suffix = ".png"
    if s.startswith("data:"):
        m = re.match(r"data:(image/[^;]+);base64,(.*)", s, re.DOTALL)
        if not m:
            return ""
        if "/" in m.group(1):
            suffix = "." + m.group(1).split("/")[-1]
        try:
            data = base64.b64decode(m.group(2))
        except Exception:
            return ""
    elif s.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(s, timeout=15) as resp:
                data = resp.read()
        except Exception:
            return ""

    if not data:
        return ""
    fd, path = tempfile.mkstemp(prefix="ocr_img_", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        from app.rag.ocr import ocr_image

        return ocr_image(path)
    except Exception:
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
