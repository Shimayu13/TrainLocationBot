"""Prepare bounded Discord attachments for local inference; delete temp files."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
import tempfile
import warnings
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps
from pypdf import PdfReader, apply_configuration

from .ai import AIError

MAX_FILES = 3
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_TEXT = 2000
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_TYPES = {".txt", ".md", ".csv", ".tsv", ".json", ".py", ".js", ".ts", ".swift", ".c", ".cpp", ".h", ".css", ".html", ".xml", ".yaml", ".yml", ".log", ".toml", ".ini", ".sh"}
_PREPARATION_SLOTS = asyncio.Semaphore(2)


@dataclass
class PreparedAttachments:
    images: list = field(default_factory=list)
    text: str = ""
    notices: list = field(default_factory=list)


def extract_text(data, suffix, limit):
    with apply_configuration(
        maximum_declared_stream_length=8 * 1024 * 1024,
        zlib_maximum_output_length=8 * 1024 * 1024,
        lzw_maximum_output_length=8 * 1024 * 1024,
        run_length_maximum_output_length=8 * 1024 * 1024,
        array_based_stream_maximum_output_length=8 * 1024 * 1024,
        stream_decoding_work_maximum_length=16 * 1024 * 1024,
        page_tree_maximum_entries=5000,
        xform_maximum_invocations_per_extraction=1000,
    ):
        return _extract_text(data, suffix, limit)


def _extract_text(data, suffix, limit):
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise AIError("暗号化されたPDFには対応していません。")
        parts = []
        length = 0
        truncated = len(reader.pages) > 10
        for page in reader.pages[:10]:
            # Bound decompression before invoking the PDF text extractor.
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 8 * 1024 * 1024:
                raise AIError("PDFの展開後のページが大きすぎます。ページを分けて添付してください。")
            text = page.extract_text() or ""
            parts.append(text[:limit + 1 - length])
            length += len(parts[-1]) + 1
            if length > limit:
                truncated = True
                break
        text = "\n".join(parts)
        if not text.strip():
            raise AIError("PDFから文字を抽出できませんでした。スキャンPDFはページを画像として添付してください。")
        return text[:limit], truncated or len(text) > limit
    if suffix == ".docx":
        with ZipFile(BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024:
                raise AIError("DOCXの展開後の本文が大きすぎます。")
            root = ET.fromstring(archive.read(info))
        paragraphs = []
        length = 0
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        for paragraph in root.iter(ns + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(ns + "t"))
            paragraphs.append(text[:max(0, limit + 1 - length)])
            length += len(paragraphs[-1]) + 1
            if length > limit:
                break
        text = "\n".join(paragraphs)
        return text[:limit], len(text) > limit
    encodings = ["utf-8-sig", "cp932"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\x00" in text:
                raise AIError("テキストとして読み取れないファイルです。")
            return text[:limit], len(text) > limit
        except UnicodeDecodeError:
            continue
    raise AIError("文字コードを読み取れません。UTF-8のテキストで添付してください。")


def prepare_image(data, destination):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise AIError("画像は1600万画素以下にしてください。")
            if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise AIError("対応画像形式はPNG・JPEG・WebP・GIFです。")
            animated = getattr(image, "is_animated", False)
            image.seek(0)
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1024, 1024))
            image.save(destination, "PNG")
    return animated


@asynccontextmanager
async def prepare_attachments(files):
    async with _PREPARATION_SLOTS:
        async with _prepare_attachments(files) as result:
            yield result


@asynccontextmanager
async def _prepare_attachments(files):
    if len(files) > MAX_FILES:
        raise AIError("添付は1回につき3ファイルまでです。")
    if any(file.size > MAX_FILE_BYTES for file in files) or sum(file.size for file in files) > MAX_TOTAL_BYTES:
        raise AIError("添付は1ファイル4MB以下、合計8MB以下にしてください。")
    for file in files:
        if Path(file.filename).suffix.lower() not in IMAGE_TYPES | TEXT_TYPES | {".pdf", ".docx"}:
            raise AIError("対応していない添付形式です。画像・テキスト・CSV・コード・PDF・DOCXを添付してください。")
    result = PreparedAttachments()
    with tempfile.TemporaryDirectory(prefix="trainbot-attachments-") as directory:
        remaining = MAX_TEXT
        downloaded = 0
        for index, file in enumerate(files):
            suffix = Path(file.filename).suffix.lower()
            name = Path(file.filename).name[:100]
            try:
                data = await asyncio.wait_for(file.read(), timeout=30)
                if len(data) > MAX_FILE_BYTES:
                    raise AIError("ダウンロードした添付が4MBを超えています。")
                downloaded += len(data)
                if downloaded > MAX_TOTAL_BYTES:
                    raise AIError("ダウンロードした添付の合計が8MBを超えています。")
                if suffix in IMAGE_TYPES:
                    destination = Path(directory) / f"image-{index}.png"
                    animated = await asyncio.to_thread(prepare_image, data, destination)
                    result.images.append((destination, name))
                    if animated:
                        result.notices.append(f"{name}: アニメーションの最初のフレームのみを解析します。")
                elif remaining:
                    text, truncated = await asyncio.to_thread(extract_text, data, suffix, remaining)
                    if not text.strip():
                        raise AIError("添付ファイルに読み取れる本文がありません。")
                    result.text += f"\nファイル名: {name}\n本文:\n{text}\n"
                    remaining -= len(text)
                    if truncated:
                        result.notices.append(f"{name}: 長いため先頭部分のみを解析します（添付本文は合計2000文字、PDFは最大10ページ）。")
                else:
                    result.notices.append(f"{name}: 添付本文の合計上限に達したため、今回は読み込んでいません。")
            except AIError:
                raise
            except Exception:
                raise AIError(f"{name}: 添付の取得または読み取りに失敗しました。形式・破損・容量を確認してください。") from None
        yield result
