"""多格式文档解析：把 .md/.txt/.pdf/.docx/.pptx/.xlsx 提取为纯文本。

每种格式返回纯文本，统一交给 ingest 切块、向量化。
- 文本类：md / txt 直接读取
- PDF  ：pypdf 逐页提取（扫描件/图片型 PDF 无文字，需 OCR，见 README）
- Word ：python-docx 提取段落 + 表格
- PPT  ：python-pptx 提取每页文本 + 表格
- Excel：openpyxl 逐 sheet 按行拼接为表格文本
"""
import os

# 装饰性符号：分隔线 / 表格框线 / 制表线等，用于识别并清理“纯符号行”
_DECOR_CHARS = set("-=═╔╚╗╝║│┌┬┐├┼┤└┴┘─━~·…—*_#")


def _clean_decor_lines(text: str) -> str:
    """删除纯装饰行（横线/框线等无正文的符号行）。

    问答类文档常用 `----`、`╔══╗`、`====` 等分隔线排版，若不清理，
    切块时会被粘连进正文，产生纯符号噪音块、稀释语义向量。这里仅删除
    “整行几乎全是装饰符号”的行，凡含中文/字母/数字的行一律保留。
    """
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if s and all(ch in _DECOR_CHARS or ch.isspace() for ch in s):
            continue
        kept.append(line)
    return "\n".join(kept)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    page_texts = [page.extract_text() or "" for page in reader.pages]

    # 识别「图片型页面」：文字层稀薄（多为标题）且页面含图片，正文大概率在图片里。
    # PPT 转 PDF 很常见，仅靠 pypdf 会丢失正文，需对这些页 OCR。
    ocr_ids = set()
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(path)
        try:
            for i, page in enumerate(doc):
                if len(page_texts[i].strip()) < 40 and len(page.get_images(full=True)) > 0:
                    ocr_ids.add(i)
        finally:
            doc.close()
    except Exception:
        pass

    # 无图片型页面：纯文字 PDF，直接返回 pypdf 提取结果
    if not ocr_ids:
        text = "\n\n".join(t for t in page_texts if t.strip())
        if text.strip():
            return text
        # 完全无文字层：全量 OCR 兜底
        try:
            from app.rag.ocr import pdf_pages_to_text

            return pdf_pages_to_text(path)
        except Exception:
            return ""

    # 混合型：对图片页 OCR，按页码与文字层合并（图片页以 OCR 结果为准）
    try:
        from app.rag.ocr import ocr_pdf_pages

        ocr_results = ocr_pdf_pages(path, ocr_ids)
    except Exception:
        ocr_results = {}

    parts = []
    for i, t in enumerate(page_texts):
        ocr_text = ocr_results.get(i, "").strip()
        if ocr_text:
            parts.append(ocr_text)
        elif t.strip():
            parts.append(t)
    return "\n\n".join(parts)


def _load_docx(path: str) -> str:
    import docx

    doc = docx.Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def _load_pptx(path: str) -> str:
    from pptx import Presentation

    prs = Presentation(path)
    parts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs).strip()
                    if text:
                        parts.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def _load_xlsx(path: str) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"## {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if any(cells):
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts)


# 支持的扩展名 -> 解析函数。老式 .xls 需另存为 .xlsx（xlrd 已停止维护 .xlsx 支持）。
LOADERS = {
    ".md": _read_text,
    ".txt": _read_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
    ".pptx": _load_pptx,
    ".xlsx": _load_xlsx,
}

SUPPORTED_EXTS = tuple(LOADERS.keys())


def load_document(path: str) -> str:
    """按扩展名分发给对应解析器，返回清理装饰行后的纯文本；不支持的格式抛 ValueError。"""
    ext = os.path.splitext(path)[1].lower()
    loader = LOADERS.get(ext)
    if loader is None:
        raise ValueError(f"不支持的文档格式：{ext}")
    return _clean_decor_lines(loader(path))
