"""知识库入库：扫描 KB_DIR 下 .md/.txt/.pdf/.docx/.pptx/.xlsx 文档，切块、向量化、落盘。

V2：为每个 chunk 增加元数据 doc_type / glass_type / defect_type / authority，
供 reranker 做领域匹配与权威度加权。

用法：在项目根目录运行  python -m app.rag.ingest
"""
import os

from app.config import settings
from app.domain.terms import resolve_defect, resolve_glass_type
from app.llm import get_embeddings
from app.rag.loaders import SUPPORTED_EXTS, load_document
from app.rag.store import SimpleVectorStore

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

_DOC_TYPE_HINTS = {
    "standard": ("标准", "判级", "判定", "规范", "规格", "允收", "standard"),
    "sop": ("sop", "作业", "流程", "操作指引"),
    "equipment": ("设备", "相机", "光源", "选型", "镜头"),
    "case": ("案例", "复盘", "故障"),
}


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按空行分段聚合到 size 字符，超长段滑窗切分。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf}\n{p}".strip()
        else:
            if buf:
                chunks.append(buf)
            while len(p) > size:
                chunks.append(p[:size])
                p = p[size - overlap:]
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def _doc_type(fn: str, text: str) -> str:
    hay = (fn + " " + text[:400]).lower()
    for dtype, hints in _DOC_TYPE_HINTS.items():
        if any(h in hay for h in hints):
            return dtype
    return "knowledge"


def _meta_for(fn: str, text: str, i: int) -> dict:
    doc_type = _doc_type(fn, text)
    authority = 1.2 if doc_type in ("standard", "sop") else 1.0
    return {
        "source": fn,
        "chunk": i,
        "doc_type": doc_type,
        "glass_type": resolve_glass_type(fn + " " + text[:400]),
        "defect_type": resolve_defect(fn + " " + text[:400]),
        "authority": authority,
    }


def ingest() -> None:
    emb = get_embeddings()
    store = SimpleVectorStore(settings.vector_store_path)
    store.chunks = []  # 每次全量重建，避免陈旧索引

    if not os.path.isdir(settings.kb_dir):
        print(f"知识库目录不存在：{settings.kb_dir}")
        return

    for root, _, files in os.walk(settings.kb_dir):
        for fn in sorted(files):
            if not fn.endswith(SUPPORTED_EXTS):
                continue
            path = os.path.join(root, fn)
            try:
                text = load_document(path)
            except Exception as e:  # 解析失败不中断整体入库
                print(f"  [跳过] {fn}: 解析失败 {e}")
                continue
            if not text.strip():
                print(f"  [跳过] {fn}: 未提取到文本（扫描件/图片型 PDF 需 OCR）")
                continue
            chunks = chunk_text(text)
            vectors = emb.embed_documents(chunks)
            metas = [_meta_for(fn, c, i) for i, c in enumerate(chunks)]
            store.add(chunks, metas, vectors)
            print(f"  {fn}: {len(chunks)} 块")

    store.save()
    print(f"知识库构建完成，共 {len(store.chunks)} 块 -> {settings.vector_store_path}")


if __name__ == "__main__":
    ingest()
