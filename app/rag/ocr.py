"""OCR 兜底：扫描件/图片型 PDF 无文字层时，渲染页面为图片后识别中文文字。

设计：
- 入库时 pypdf 优先提取"文字层"；提取为空（扫描件/图片型）才走本模块 OCR。
- 用 PyMuPDF(fitz) 把 PDF 每页渲染成 PNG，再交给 OCR 引擎识别。

依赖（可选，未安装时自动跳过 OCR，不影响文字型 PDF 入库）：
- PyMuPDF(fitz)   ：PDF 页 -> 图片（纯 pip，无需 poppler）
- OCR 引擎按优先级自动探测（装哪个用哪个）：
    1. PaddleOCR  ：中文识别强、纯 pip 安装，推荐
    2. pytesseract：需系统安装 tesseract-ocr 及中文包 chi_sim

开关：.env 中 OCR_ENABLED=false 关闭兜底。
"""
import os

# 禁用 Paddle 的 MKLDNN(oneDNN) 推理引擎。
# paddlepaddle 3.3.x 在 Windows CPU 上启用 oneDNN 时会触发
#   NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
# 导致 OCR 识别失败，这里默认关闭（必须在 import paddleocr 之前设置）。
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")

OCR_ENABLED = os.getenv("OCR_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

_paddle = None  # 引擎实例缓存，避免每页重复加载模型


def _extract_texts(obj) -> list:
    """从 PaddleOCR 2.x/3.x 结果结构中递归抽取文本，兼容多种返回格式。"""
    texts = []
    if isinstance(obj, str):
        texts.append(obj)
    elif isinstance(obj, (list, tuple)):
        # 识别 (text, score) 元组
        if len(obj) == 2 and isinstance(obj[0], str) and isinstance(obj[1], (int, float)):
            texts.append(obj[0])
        else:
            for item in obj:
                texts.extend(_extract_texts(item))
    elif isinstance(obj, dict):
        # 只提取文本类字段，跳过 input_path / page_index / rec_scores 等元数据
        for key in ("rec_texts", "texts", "text", "rec_text"):
            if key in obj:
                texts.extend(_extract_texts(obj[key]))
    else:
        for attr in ("rec_texts", "texts", "text", "rec_text"):
            if hasattr(obj, attr):
                texts.extend(_extract_texts(getattr(obj, attr)))
    return texts


def _get_paddle():
    global _paddle
    if _paddle is None:
        from paddleocr import PaddleOCR

        try:
            # PaddleOCR 3.x
            _paddle = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                lang="ch",
                enable_mkldnn=False,
            )
        except TypeError:
            # PaddleOCR 2.x 旧参数
            _paddle = PaddleOCR(use_angle_cls=True, lang="ch")
    return _paddle


def _ocr_paddle(img_path: str) -> str:
    ocr = _get_paddle()
    try:
        result = ocr.predict(img_path)  # 3.x
    except AttributeError:
        result = ocr.ocr(img_path, cls=True)  # 2.x
    lines = [t.strip() for t in _extract_texts(result) if t.strip()]
    return "\n".join(lines)


def _ocr_tesseract(img_path: str) -> str:
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(img_path), lang="chi_sim")


def ocr_image(img_path: str) -> str:
    """对单张图片 OCR；引擎都不可用或 OCR 关闭时返回空串。"""
    if not OCR_ENABLED:
        return ""
    for engine in (_ocr_paddle, _ocr_tesseract):
        try:
            text = engine(img_path)
            if text.strip():
                return text
        except Exception:
            continue
    return ""


def ocr_pdf_pages(path: str, page_ids=None) -> dict:
    """对 PDF 指定页（page_ids 为 None 时全部页）渲染为图片并 OCR，返回 {页码: 文本}。"""
    import shutil
    import tempfile

    import fitz  # PyMuPDF

    doc = fitz.open(path)
    tmpdir = tempfile.mkdtemp(prefix="ocr_")
    results: dict = {}
    try:
        for i, page in enumerate(doc):
            if page_ids is not None and i not in page_ids:
                continue
            pix = page.get_pixmap(dpi=200)
            img_path = os.path.join(tmpdir, f"page_{i}.png")
            pix.save(img_path)
            text = ocr_image(img_path)
            if text.strip():
                results[i] = text
    finally:
        doc.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
    return results


def pdf_pages_to_text(path: str) -> str:
    """把（扫描件/图片型）PDF 逐页渲染为图片并 OCR，返回拼接文本。"""
    results = ocr_pdf_pages(path)
    return "\n\n".join(results[i] for i in sorted(results))
