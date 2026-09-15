"""FastAPI 服务入口。

启动：uvicorn app.main:app --reload --port 8000

支持输入：
- POST /api/chat          JSON：文本 + 多图（URL/base64） + 文件文本
- POST /api/chat/upload   multipart：文本 + 多张图片文件 + 多个文档文件（一站式）
- POST /api/chat/stream   SSE：文本流式（多轮）
"""
import base64
import json
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.config import settings
from app.db.session import init_db
from app.graph.builder import build_graph
from app.logging import set_thread_id
from app.rag.loaders import SUPPORTED_EXTS, load_document
from app.security import get_sensitive_filter

# 图片扩展名 -> MIME（用于构造 base64 Data URL 交给视觉模型）
IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}
IMAGE_EXTS = set(IMAGE_MIME.keys())

# 敏感词命中时的统一拦截文案与返回体
BLOCKED_REPLY = "输入内容包含违规信息，已拦截。请勿发送违法、色情、暴力、赌博或政治敏感等内容。"


def _blocked_result() -> dict:
    return {"intent": "blocked", "answer": BLOCKED_REPLY, "chart": None}


def _is_blocked(text: str) -> bool:
    """统一入口：敏感词过滤总开关关闭时放行；开启时对用户输入做违禁词检测。"""
    if not settings.sensitive_filter_enabled:
        return False
    if not text:
        return False
    return get_sensitive_filter().is_blocked(text)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时幂等建表 + 轻量迁移（补 shift 等新增列 / 新建 V2 表）
    init_db()
    yield


app = FastAPI(title="玻璃检测部门智能体", version="0.1.0", lifespan=lifespan)
graph = build_graph()


class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"  # 多轮对话会话标识（按人/按群分配）
    image: str | None = None   # 现场图片 URL 或 base64 Data URL（单图，向后兼容）
    images: list[str] | None = None   # 多张图片 URL 或 base64 Data URL
    file_texts: list[dict] | None = None  # 上传文件的解析结果 [{"filename": str, "text": str}]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest):
    set_thread_id(req.thread_id)
    if _is_blocked(req.message):
        return _blocked_result()
    # 关键：image / images / file_texts 必须显式覆盖（哪怕为 None），
    # 否则 checkpointer 会恢复同一 thread_id 上一轮残留的图片/文件字段，
    # 导致"发过一次图后，后续纯文本消息也被 route_start 判为带图而一直走 vision 流"。
    invoke_input: dict = {
        "messages": [HumanMessage(content=req.message)],
        "image": req.image,
        "images": req.images,
        "file_texts": req.file_texts,
    }
    result = graph.invoke(
        invoke_input,
        {"configurable": {"thread_id": req.thread_id}},
    )
    return {
        "intent": result.get("intent"),
        "answer": result.get("final_answer", ""),
        "chart": result.get("chart_config"),  # ECharts option，大屏可直接渲染
    }


@app.post("/api/chat/upload")
async def chat_upload(
    message: str = Form(""),
    thread_id: str = Form("default"),
    images: Optional[List[UploadFile]] = File(default=None),
    files: Optional[List[UploadFile]] = File(default=None),
):
    """多图多文件一站式上传问答。

    - images：图片文件（jpg/png/webp...），转为 base64 Data URL 走视觉分析流；
    - files ：文档文件（pdf/docx/pptx/xlsx/md/txt），解析文字后走文件即时问答流。
    图片与文件可同时传：有图时优先走视觉流，否则走文件问答流。
    """
    set_thread_id(thread_id)
    if _is_blocked(message):
        return _blocked_result()
    # 同上：显式覆盖，避免 checkpointer 恢复上一轮残留的图片/文件字段
    invoke_input: dict = {
        "messages": [HumanMessage(content=message)],
        "image": None,
        "images": None,
        "file_texts": None,
    }

    image_list: list[str] = []
    for img in images or []:
        ext = os.path.splitext(img.filename or "")[1].lower()
        if ext not in IMAGE_EXTS:
            continue
        data = await img.read()
        b64 = base64.b64encode(data).decode("ascii")
        image_list.append(f"data:{IMAGE_MIME[ext]};base64,{b64}")

    file_texts: list[dict] = []
    tmpdir: Optional[str] = None
    try:
        for f in files or []:
            ext = os.path.splitext(f.filename or "")[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            data = await f.read()
            if tmpdir is None:
                tmpdir = tempfile.mkdtemp(prefix="upload_")
            safe_name = os.path.basename(f.filename or "upload")
            path = os.path.join(tmpdir, safe_name)
            with open(path, "wb") as fh:
                fh.write(data)
            try:
                text = load_document(path)
                file_texts.append({"filename": f.filename, "text": text})
            except ModuleNotFoundError as e:
                # 环境缺依赖（如 pypdf）≠ 文件损坏：给出明确指引，避免误导用户“转格式/重新上传”
                file_texts.append(
                    {
                        "filename": f.filename,
                        "text": f"（服务端解析失败：缺少依赖库 {e.name}，"
                        f"请在服务环境执行 pip install {e.name} 后重试，无需重新上传或转换格式）",
                    }
                )
            except Exception as e:
                file_texts.append({"filename": f.filename, "text": f"（解析失败：{e}）"})

        if image_list:
            invoke_input["images"] = image_list
        if file_texts:
            invoke_input["file_texts"] = file_texts

        result = graph.invoke(
            invoke_input,
            {"configurable": {"thread_id": thread_id}},
        )
        return {
            "intent": result.get("intent"),
            "answer": result.get("final_answer", ""),
            "chart": result.get("chart_config"),
        }
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


@app.get("/api/chat/stream")
def chat_stream(q: str, thread_id: str = "default"):
    """SSE 流式输出，逐节点推送状态更新。"""

    if _is_blocked(q):

        def _blocked_gen():
            payload = json.dumps(_blocked_result(), ensure_ascii=False, default=str)
            yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_blocked_gen(), media_type="text/event-stream")

    def gen():
        set_thread_id(thread_id)
        for update in graph.stream(
            {
                "messages": [HumanMessage(content=q)],
                # 流式入口同样显式覆盖，避免复用同一 thread_id 时残留上一轮的图片字段
                "image": None,
                "images": None,
                "file_texts": None,
            },
            {"configurable": {"thread_id": thread_id}},
        ):
            yield f"data: {json.dumps(update, ensure_ascii=False, default=str)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
