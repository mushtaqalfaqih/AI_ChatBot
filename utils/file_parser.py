import logging
from pathlib import Path

import pandas as pd
from docx import Document
from PyPDF2 import PdfReader

try:
    from PIL import Image
    import pytesseract
except Exception:
    Image = None
    pytesseract = None

logger = logging.getLogger("app.file_parser")

# حدود دفاعية: تمنع استهلاك موارد مفرط عند رفع ملفات ضخمة الصفحات/الصفوف
# (حتى لو حجم الملف بالبايت صغيرًا، عدد الصفحات/الصفوف قد يكون كبيرًا جدًا).
MAX_PDF_PAGES = 200
MAX_SPREADSHEET_ROWS = 5000


def extract_text_from_file(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == '.txt':
        return path.read_text(encoding='utf-8', errors='ignore')
    if suffix == '.docx':
        return extract_text_from_docx(path)
    if suffix == '.pdf':
        return extract_text_from_pdf(path)
    if suffix in {'.xlsx', '.xls', '.csv'}:
        return extract_text_from_spreadsheet(path)
    if suffix in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff'}:
        return extract_text_from_image(path)

    raise ValueError(f'نوع الملف غير مدعوم: {suffix}')


def extract_text_from_docx(path: Path) -> str:
    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return '\n'.join(paragraphs)


def extract_text_from_pdf(path: Path) -> str:
    reader = PdfReader(path)
    text_parts = []

    total_pages = len(reader.pages)
    if total_pages > MAX_PDF_PAGES:
        logger.warning("pdf_truncated", extra={"total_pages": total_pages, "limit": MAX_PDF_PAGES})

    for page in reader.pages[:MAX_PDF_PAGES]:
        text = page.extract_text() or ''
        if text.strip():
            text_parts.append(text)

    result = '\n\n'.join(text_parts)
    if total_pages > MAX_PDF_PAGES:
        result += f"\n\n[...تم الاكتفاء بأول {MAX_PDF_PAGES} صفحة من أصل {total_pages}]"
    return result


def extract_text_from_image(path: Path) -> str:
    if Image is None or pytesseract is None:
        return "نعتذر، لا يدعم هذا الإصدار قراءة الصور مباشرة. يرجى تثبيت Tesseract و pillow إذا كنت تريد دعم OCR للصور."

    image = Image.open(path)
    text = pytesseract.image_to_string(image, lang='eng+ara').strip()
    if text:
        return text
    return "صورة مرفقة بدون نص واضح. يرجى سؤال الذكاء الاصطناعي عن محتوى الصورة بصياغة عامة."


def extract_text_from_spreadsheet(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == '.csv':
        # الإصلاح: pandas.read_csv لا يملك معامل اسمه "errors" (كان يرمي TypeError دومًا).
        # المعامل الصحيح لتجاهل بايتات غير صالحة في الترميز هو encoding_errors.
        df = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            encoding='utf-8',
            encoding_errors='ignore',
            nrows=MAX_SPREADSHEET_ROWS,
        )
    else:
        # الإصلاح: openpyxl يدعم .xlsx فقط. ملفات .xls القديمة تحتاج محرك xlrd،
        # وإلا يفشل رفع أي ملف .xls رغم أنه مدرج ضمن الامتدادات المسموحة.
        engine = 'xlrd' if suffix == '.xls' else 'openpyxl'
        try:
            df = pd.read_excel(path, dtype=str, engine=engine, nrows=MAX_SPREADSHEET_ROWS)
        except ImportError as e:
            # غالبًا xlrd غير مثبت (لا يأتي افتراضيًا مع pandas الحديث)
            raise ValueError(
                "قراءة ملفات .xls القديمة تتطلب مكتبة 'xlrd'. ثبّتها عبر: pip install xlrd"
            ) from e

    if df.empty:
        return ''

    rows = []
    # itertuples أسرع بكثير من iterrows على الجداول الكبيرة
    for row in df.itertuples(index=False):
        cells = [str(value) for value in row if str(value).strip()]
        if cells:
            rows.append('\t'.join(cells))

    result = '\n'.join(rows)
    if len(df) >= MAX_SPREADSHEET_ROWS:
        result += f"\n\n[...تم الاكتفاء بأول {MAX_SPREADSHEET_ROWS} صف]"
    return result