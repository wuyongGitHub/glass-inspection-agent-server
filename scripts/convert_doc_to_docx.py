"""批量把 data/docs/ 下的老 .doc 转成 .docx。

依赖：本机 Microsoft Word + pywin32（win32com）。

用法：在项目根目录运行  python scripts/convert_doc_to_docx.py

说明：
- 转换后保留原 .doc 文件，仅新增同名 .docx；已存在 .docx 时自动跳过。
- 使用隐藏的 Word 实例（Visible=False）+ 禁用宏 + 抑制提示框，无需人工干预。
"""
import os
import sys

import win32com.client

# 项目根目录 / data/docs
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_DIR = os.path.join(BASE_DIR, "data", "docs")

# 保存为 .docx 的 Word 文件格式常量：
#   wdFormatXMLDocument = 12（Word 2007 起）
#   wdFormatDocumentDefault = 16（Word 2010+ 默认，更通用）
DOCX_FORMAT = 16

# msoAutomationSecurityForceDisable：强制禁用宏，避免打开时弹宏安全提示
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


def main() -> int:
    if not os.path.isdir(DOC_DIR):
        print(f"目录不存在：{DOC_DIR}")
        return 1

    files = [
        f for f in os.listdir(DOC_DIR)
        if f.lower().endswith(".doc") and not f.lower().endswith(".docx")
    ]
    if not files:
        print("没有找到 .doc 文件")
        return 0

    print(f"共 {len(files)} 个 .doc 待转换")

    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0  # wdAlertsNone：抑制提示框
    try:
        word.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    except Exception:
        pass

    success, skipped, failed = [], [], []
    try:
        for fn in sorted(files):
            src = os.path.join(DOC_DIR, fn)
            dst = os.path.splitext(src)[0] + ".docx"
            if os.path.exists(dst):
                print(f"[跳过] 已存在 {fn}")
                skipped.append(fn)
                continue
            try:
                doc = word.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False)
                try:
                    doc.SaveAs2(dst, FileFormat=DOCX_FORMAT)
                except AttributeError:
                    doc.SaveAs(dst, FileFormat=DOCX_FORMAT)
                doc.Close(False)
                success.append(fn)
                print(f"[OK] {fn}")
            except Exception as e:
                failed.append((fn, str(e)))
                print(f"[FAIL] {fn}: {e}")
    finally:
        try:
            word.Quit()
        except Exception:
            pass

    print("-" * 50)
    print(f"成功 {len(success)}，跳过 {len(skipped)}，失败 {len(failed)}")
    if failed:
        print("失败明细：")
        for fn, err in failed:
            print(f"  {fn}: {err}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
