"""Bounded local extraction and original storage; activation always needs user confirmation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
import uuid
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from .knowledge_repository import (
    MAX_CONTENT_CHARACTERS,
    MAX_SPAN_CHARACTERS,
    MAX_SPANS,
    KnowledgeRepository,
    content_spans,
)
from .storage_governance import ensure_private_directory, harden_private_file


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_DOCX_ENTRIES = 1_000
MAX_DOCX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200
MAX_PDF_PAGES = 500
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CONTENT_TYPES = {
    ".txt": {"text/plain"},
    ".md": {"text/plain", "text/markdown", "text/x-markdown"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    ".pdf": {"application/pdf"},
}


@dataclass(frozen=True)
class DocumentExtraction:
    status: str
    content: str = ""
    spans: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None
    format: str = ""
    structure: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _InvalidDocument(ValueError):
    pass


def validate_upload(filename: str, data: bytes, content_type: str | None = None) -> str:
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 240
            or filename.endswith((" ", ".")) or any(character in filename for character in ("/", "\\", ":"))
            or any(ord(character) < 32 for character in filename)):
        raise ValueError("unsafe document filename")
    extension = Path(filename).suffix.casefold()
    if extension not in _CONTENT_TYPES:
        raise ValueError("unsupported document format")
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD_BYTES:
        raise ValueError("document must contain 1 byte to 20 MiB")
    if content_type is not None:
        if not isinstance(content_type, str) or len(content_type) > 200 or any(ord(c) < 32 for c in content_type):
            raise ValueError("invalid document content type")
        mime = content_type.split(";", 1)[0].strip().casefold()
        if mime and mime != "application/octet-stream" and mime not in _CONTENT_TYPES[extension]:
            raise ValueError("document content type does not match filename")
    return extension


def document_structure(spans: list[dict[str, Any]], format_name: str) -> dict[str, Any]:
    """Mechanical outline only; headings/page/table coordinates come from the source."""
    sections: list[dict[str, Any]] = []
    for ordinal, span in enumerate(spans):
        locator = span.get("locator") or {}
        heading = locator.get("heading")
        if format_name == "md" and not heading:
            match = re.match(r"^\s*#{1,6}\s+([^\n]+)", span["text"])
            heading = match.group(1).strip() if match else None
        group = ("heading", ordinal) if heading else ("page", locator["page"]) if "page" in locator else None
        if heading or not sections or (group is not None and sections[-1].get("group") != list(group)):
            title = str(heading)[:200] if heading else f"第 {locator['page']} 页" if "page" in locator else "正文"
            sections.append({"title": title, "span_ordinals": [], "locator": locator,
                             **({"group": list(group)} if group is not None else {})})
        sections[-1]["span_ordinals"].append(ordinal)
    for section in sections:
        section.pop("group", None)
    return {"format": format_name, "span_count": len(spans), "sections": sections}


def _result(content: str, spans: list[dict[str, Any]], format_name: str, *, status: str = "extracted",
            error_code: str | None = None, warnings: tuple[str, ...] = ()) -> DocumentExtraction:
    if len(content) > MAX_CONTENT_CHARACTERS or len(spans) > MAX_SPANS:
        return DocumentExtraction("failed", error_code="extracted_text_too_large", format=format_name)
    if not content.strip():
        return DocumentExtraction("failed", error_code="document_has_no_text", format=format_name)
    return DocumentExtraction(status, content, tuple(spans), error_code, format_name,
                              document_structure(spans, format_name), warnings)


def _text(data: bytes, format_name: str) -> DocumentExtraction:
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    content = None
    for encoding in encodings:
        try:
            content = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if content is None or any(ord(c) < 32 and c not in "\t\n\r" for c in content):
        return DocumentExtraction("failed", error_code="invalid_text_encoding", format=format_name)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    return _result(content, content_spans(content), format_name)


def _docx(data: bytes) -> DocumentExtraction:
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES or sum(item.file_size for item in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise _InvalidDocument("docx_archive_too_large")
            names: set[str] = set()
            for item in entries:
                path = PurePosixPath(item.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                        or item.filename in names or stat.S_ISLNK(item.external_attr >> 16)):
                    raise _InvalidDocument("unsafe_docx_archive_path")
                names.add(item.filename)
                if item.flag_bits & 1:
                    raise _InvalidDocument("encrypted_docx_not_supported")
                if (item.file_size > MAX_UPLOAD_BYTES
                        or item.file_size > max(item.compress_size, 1) * MAX_DOCX_COMPRESSION_RATIO):
                    raise _InvalidDocument("docx_archive_too_large")
            if "word/document.xml" not in names:
                raise _InvalidDocument("invalid_docx_document")
            xml = archive.read("word/document.xml")
        if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
            raise _InvalidDocument("unsafe_docx_xml")
        document = ElementTree.fromstring(xml)
        body = document.find(f"{_WORD}body")
        if body is None:
            raise _InvalidDocument("invalid_docx_document")
        blocks, spans, total = [], [], 0
        table_coordinates: dict[int, dict[str, int]] = {}
        for table_number, table in enumerate(body.iter(f"{_WORD}tbl"), 1):
            for row_number, row in enumerate(table.findall(f"{_WORD}tr"), 1):
                for column_number, cell in enumerate(row.findall(f"{_WORD}tc"), 1):
                    for paragraph_node in cell.findall(f"{_WORD}p"):
                        table_coordinates[id(paragraph_node)] = {"table": table_number, "row": row_number, "column": column_number}
        # XML document order also preserves table-cell paragraphs.
        for paragraph, element in enumerate(body.iter(f"{_WORD}p"), 1):
            pieces = []
            for node in element.iter():
                if node.tag == f"{_WORD}t":
                    pieces.append(node.text or "")
                elif node.tag == f"{_WORD}tab":
                    pieces.append("\t")
                elif node.tag in {f"{_WORD}br", f"{_WORD}cr"}:
                    pieces.append("\n")
            block = "".join(pieces).strip()
            if not block:
                continue
            blocks.append(block)
            total += len(block) + 2
            properties = element.find(f"{_WORD}pPr")
            style = properties.find(f"{_WORD}pStyle") if properties is not None else None
            style_name = style.get(f"{_WORD}val", "") if style is not None else ""
            outline = properties.find(f"{_WORD}outlineLvl") if properties is not None else None
            heading = bool(re.fullmatch(r"heading\s*[1-6]", style_name, re.IGNORECASE) or outline is not None)
            for offset in range(0, len(block), MAX_SPAN_CHARACTERS):
                coordinates = table_coordinates.get(id(element))
                locator = {"type": "docx_table_cell" if coordinates else "docx_paragraph",
                           "paragraph": paragraph, "offset": offset, **(coordinates or {})}
                if heading and offset == 0:
                    locator["heading"] = block[:200]
                spans.append({"text": block[offset:offset + MAX_SPAN_CHARACTERS],
                              "locator": locator})
            if total > MAX_CONTENT_CHARACTERS:
                raise _InvalidDocument("extracted_text_too_large")
        warnings = ("docx_images_headers_footers_not_extracted",) if (
            any(name.startswith(("word/header", "word/footer", "word/media/")) for name in names)
            or any(node.tag in {f"{_WORD}drawing", f"{_WORD}pict"} for node in body.iter())) else ()
        if any(node.tag in {f"{_WORD}gridSpan", f"{_WORD}vMerge", f"{_WORD}hMerge"} for node in body.iter()):
            warnings += ("docx_complex_tables_review_required",)
        elif any(child.tag == f"{_WORD}tbl" for table in body.iter(f"{_WORD}tbl") for child in table.iter() if child is not table):
            warnings += ("docx_complex_tables_review_required",)
        return _result("\n\n".join(blocks), spans, "docx", warnings=warnings)
    except _InvalidDocument as exc:
        return DocumentExtraction("failed", error_code=str(exc), format="docx")
    except (BadZipFile, ElementTree.ParseError, KeyError, RuntimeError, OSError, ValueError):
        return DocumentExtraction("failed", error_code="invalid_docx_document", format="docx")


def _pdf(data: bytes) -> DocumentExtraction:
    if not data.lstrip().startswith(b"%PDF-"):
        return DocumentExtraction("failed", error_code="invalid_pdf_document", format="pdf")
    try:
        from pypdf import PdfReader
    except ImportError:
        return DocumentExtraction("unavailable", error_code="pdf_parser_unavailable", format="pdf")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            return DocumentExtraction("failed", error_code="encrypted_pdf_not_supported", format="pdf")
        if len(reader.pages) > MAX_PDF_PAGES:
            return DocumentExtraction("failed", error_code="pdf_too_many_pages", format="pdf")
        blocks, spans, empty_pages, total = [], [], [], 0
        for page_number, page in enumerate(reader.pages, 1):
            # Reject large decoded streams before text traversal; parser isolation belongs to the job runner.
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > MAX_DOCX_UNCOMPRESSED_BYTES:
                return DocumentExtraction("failed", error_code="pdf_page_too_large", format="pdf")
            block = (page.extract_text() or "").strip()
            if not block:
                empty_pages.append(page_number)
                continue
            total += len(block)
            if total > MAX_CONTENT_CHARACTERS:
                return DocumentExtraction("failed", error_code="extracted_text_too_large", format="pdf")
            blocks.append(block)
            for offset in range(0, len(block), MAX_SPAN_CHARACTERS):
                spans.append({"text": block[offset:offset + MAX_SPAN_CHARACTERS],
                              "locator": {"type": "pdf_page", "page": page_number, "offset": offset}})
        if not blocks:
            return DocumentExtraction("needs_ocr", error_code="pdf_ocr_required", format="pdf")
        result = _result("\n\n".join(blocks), spans, "pdf", status="needs_ocr" if empty_pages else "extracted",
                         error_code="pdf_partial_ocr_required" if empty_pages else None,
                         warnings=("pdf_partial_ocr_required",) if empty_pages else ())
        return DocumentExtraction(result.status, result.content, result.spans, result.error_code, result.format,
                                  {**result.structure, "page_count": len(reader.pages), "missing_text_pages": empty_pages}, result.warnings)
    except Exception:
        # Parser messages may contain document contents; expose only a stable error code.
        return DocumentExtraction("failed", error_code="invalid_pdf_document", format="pdf")


def extract_document(filename: str, data: bytes, content_type: str | None = None) -> DocumentExtraction:
    extension = validate_upload(filename, data, content_type)
    if extension == ".docx":
        return _docx(data)
    if extension == ".pdf":
        return _pdf(data)
    return _text(data, extension[1:])


class DocumentImportService:
    """Persist an original plus conversion draft. No OCR/model calls or implicit confirmation."""

    def __init__(self, repository: KnowledgeRepository, data_dir: str | Path) -> None:
        self.repository = repository
        self.data_dir = ensure_private_directory(data_dir)
        self.assets_dir = ensure_private_directory(self.data_dir / "knowledge_assets")
        if self.assets_dir.parent != self.data_dir:
            raise ValueError("knowledge asset directory escapes managed storage")

    def stage_document(self, project_id: str, filename: str, data: bytes, *, folder_id: str | None = None,
                       title: str | None = None, content_type: str | None = None) -> dict[str, Any]:
        extension = validate_upload(filename, data, content_type)
        asset_id = uuid.uuid4().hex
        relative = f"knowledge_assets/{asset_id}/original{extension}"
        # Repository validates project/folder/metadata before any original is written.
        document = self.repository.create_document(project_id, title or Path(filename).stem, folder_id=folder_id,
                                                   filename=filename, content_type=content_type, file_size_bytes=len(data),
                                                   original_sha256=hashlib.sha256(data).hexdigest(),
                                                   original_relative_path=relative)
        try:
            directory = ensure_private_directory(self.assets_dir / asset_id)
            temporary = directory / "original.tmp"
            with temporary.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            harden_private_file(temporary)
            os.replace(temporary, self.data_dir / relative)
            harden_private_file(self.data_dir / relative)
        except OSError:
            return self.repository.set_import_status(document["id"], "failed", error_code="original_write_failed")
        return document

    def import_document(self, project_id: str, filename: str, data: bytes, *, folder_id: str | None = None,
                        title: str | None = None, content_type: str | None = None) -> dict[str, Any]:
        document = self.stage_document(project_id, filename, data, folder_id=folder_id, title=title, content_type=content_type)
        if document["import_status"] == "failed":
            return {"document": document, "version": None,
                    "extraction": DocumentExtraction("failed", error_code=document["error_code"]).to_dict()}
        extraction = extract_document(filename, data, content_type)
        version = None
        if extraction.content:
            version = self.repository.add_version(document["id"], extraction.content, spans=extraction.spans, origin="extracted")
        document = self.repository.set_import_status(document["id"], extraction.status, error_code=extraction.error_code)
        return {"document": document, "version": version, "extraction": extraction.to_dict()}

    def original_path(self, document_id: str) -> Path:
        """Resolve an original without trusting a database path or following a symlink."""
        document = self.repository.get_document(document_id)
        relative = document["original_relative_path"]
        if not isinstance(relative, str) or not re.fullmatch(r"knowledge_assets/[0-9a-f]{32}/original\.(txt|md|docx|pdf)", relative):
            raise ValueError("document has no safe original path")
        candidate = self.data_dir / relative
        for path in (self.assets_dir, candidate.parent, candidate):
            if path.is_symlink():
                raise ValueError("document original must not be a symbolic link")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.assets_dir) or not resolved.is_file():
            raise ValueError("document original is outside managed storage or missing")
        return resolved
