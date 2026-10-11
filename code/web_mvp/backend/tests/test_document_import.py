from __future__ import annotations

from io import BytesIO
import sys
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from meeting_copilot_web_mvp.document_import import DocumentImportService, extract_document
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository, KnowledgeScopeError


def _docx(xml: bytes, extra: list[tuple[str, bytes]] | None = None) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
        for name, data in extra or []:
            archive.writestr(name, data)
    return output.getvalue()


def _word(*paragraphs: str) -> bytes:
    content = "".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
    return ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + content + '</w:body></w:document>').encode()


def test_text_markdown_chinese_and_utf16_conversion_preserves_content():
    for filename, data in (("材料.txt", "中文预算\r\n\r\n第二段".encode()),
                           ("材料.txt", "中文预算\n\n第二段".encode("gb18030")),
                           ("材料.txt", "中文预算\n\n第二段".encode("utf-16"))):
        extraction = extract_document(filename, data)
        assert extraction.status == "extracted"
        assert extraction.content == "中文预算\n\n第二段"
        assert [item["text"] for item in extraction.spans] == ["中文预算", "第二段"]
    markdown = "# 产品方案\n\n- 预算 30 万\n- 交付 15 日"
    assert extract_document("方案.md", markdown.encode(), "text/markdown").content == markdown
    assert extract_document("empty.txt", b" \n").error_code == "document_has_no_text"
    assert extract_document("binary.txt", b"\x00\x01\x02").error_code == "invalid_text_encoding"


@pytest.mark.parametrize("filename,data,mime", [
    ("../escape.md", b"abc", None), ("C:\\escape.txt", b"abc", None), ("bad\n.txt", b"abc", None),
    ("file.txt ", b"abc", None), ("file.exe", b"abc", None), ("file.txt", b"", None),
    ("file.txt", b"a" * (20 * 1024 * 1024 + 1), None), ("file.pdf", b"abc", "text/html"),
    ("file.txt", b"abc", "text/plain\r\nX: y"),
], ids=["traversal", "absolute", "control", "trailing-space", "format", "empty", "size", "mime", "header"])
def test_unsafe_upload_is_rejected(filename, data, mime):
    with pytest.raises(ValueError):
        extract_document(filename, data, mime)


def test_docx_extracts_table_paragraphs_and_real_paragraph_locators():
    xml = _word("中立追问", "预算数字为30000元")
    xml = xml.replace(b"</w:body>", b"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body>")
    result = extract_document("访谈.docx", _docx(xml))
    assert result.status == "extracted"
    assert result.content == "中立追问\n\n预算数字为30000元\n\nTable cell"
    assert result.spans[-1]["locator"] == {"type": "docx_table_cell", "paragraph": 3, "offset": 0,
                                           "table": 1, "row": 1, "column": 1}


@pytest.mark.parametrize("xml,extra,error", [
    (_word("安全"), [("../../escape.txt", b"abc")], "unsafe_docx_archive_path"),
    (_word("安全"), [("C:/escape.txt", b"abc")], "unsafe_docx_archive_path"),
    (b'<!DOCTYPE doc [<!ENTITY x "entity">]><doc>&x;</doc>', None, "unsafe_docx_xml"),
    (_word("x" * 100_000), None, "docx_archive_too_large"),
    (b"not xml", None, "invalid_docx_document"),
], ids=["traversal", "drive", "entity", "zip-bomb", "invalid-xml"])
def test_docx_rejects_unsafe_archives_and_xml(xml, extra, error):
    assert extract_document("unsafe.docx", _docx(xml, extra)).error_code == error


def test_docx_rejects_too_many_entries_and_encrypted_flag():
    assert extract_document("many.docx", _docx(_word("safe"), [(f"entry{i}", b"a") for i in range(1000)])).error_code == "docx_archive_too_large"
    data = bytearray(_docx(_word("safe")))
    # Set ZIP encryption flag in both local and central headers; no decrypt attempt is made.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = data.find(signature)
        data[index + offset] |= 1
    assert extract_document("encrypted.docx", bytes(data)).error_code == "encrypted_docx_not_supported"
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        link = ZipInfo("word/link")
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "outside")
        archive.writestr("word/document.xml", _word("safe"))
    assert extract_document("link.docx", output.getvalue()).error_code == "unsafe_docx_archive_path"


def test_pdf_unavailable_explicit_and_invalid_header(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    result = extract_document("report.pdf", b"%PDF-1.7\n")
    assert result.status == "unavailable" and result.error_code == "pdf_parser_unavailable"
    assert extract_document("wrong.pdf", b"html").error_code == "invalid_pdf_document"


def _fake_pdf(monkeypatch, texts, *, encrypted=False):
    pages = [SimpleNamespace(extract_text=lambda text=text: text, get_contents=lambda: None) for text in texts]
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda *args, **kwargs: SimpleNamespace(pages=pages, is_encrypted=encrypted)))


def test_pdf_page_locators_partial_ocr_encryption_and_page_limit(monkeypatch):
    _fake_pdf(monkeypatch, ["第一页内容", "第二页内容"])
    result = extract_document("report.pdf", b"%PDF-1.7\n")
    assert result.status == "extracted"
    assert [span["locator"]["page"] for span in result.spans] == [1, 2]
    _fake_pdf(monkeypatch, ["有文字", ""])
    result = extract_document("report.pdf", b"%PDF-1.7\n")
    assert result.status == "needs_ocr" and result.error_code == "pdf_partial_ocr_required" and result.content == "有文字"
    _fake_pdf(monkeypatch, [""])
    assert extract_document("scan.pdf", b"%PDF-1.7\n").error_code == "pdf_ocr_required"
    _fake_pdf(monkeypatch, ["text"], encrypted=True)
    assert extract_document("secret.pdf", b"%PDF-1.7\n").error_code == "encrypted_pdf_not_supported"
    _fake_pdf(monkeypatch, ["text"] * 501)
    assert extract_document("huge.pdf", b"%PDF-1.7\n").error_code == "pdf_too_many_pages"


def test_real_pypdf_text_page_and_scan_status():
    pypdf = pytest.importorskip("pypdf", reason="optional PDF parser will be added by the application integration")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Budget 30000 confirmed.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    result = extract_document("real.pdf", output.getvalue())
    assert result.status == "extracted" and "Budget 30000 confirmed." in result.content
    assert result.spans[0]["locator"] == {"type": "pdf_page", "page": 1, "offset": 0}
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    assert extract_document("partial.pdf", output.getvalue()).status == "needs_ocr"


def test_import_original_draft_preview_correction_confirmation_and_invalid_scope(tmp_path):
    repository = KnowledgeRepository(tmp_path / "library.db")
    try:
        project = repository.create_project("客户项目")
        folder = repository.create_folder(project["id"], "访谈材料")
        importer = DocumentImportService(repository, tmp_path / "private")
        result = importer.import_document(project["id"], "访谈.md", "预算数字错了30万元。".encode(), folder_id=folder["id"])
        document = result["document"]
        assert document["status"] == "draft" and document["import_status"] == "extracted"
        assert importer.original_path(document["id"]).read_bytes() == "预算数字错了30万元。".encode()
        assert "访谈" not in document["original_relative_path"]
        assert repository.search("预算", project_id=project["id"]) == []
        corrected = repository.add_version(document["id"], "预算已校对为3万元。", expected_revision=document["revision"])
        document = repository.confirm_version(document["id"], corrected["id"], expected_revision=document["revision"] + 1)
        evidence = repository.search("预算", project_id=project["id"])[0]
        assert evidence["version"] == 2 and "3万元" in evidence["quote"]
        assert evidence["locator"]["type"] == "converted_text"
        assert result["version"]["spans"][0]["locator"]["type"] == "converted_text"
        other = repository.create_project("其他项目")
        before = len(list(importer.assets_dir.iterdir()))
        with pytest.raises(KnowledgeScopeError):
            importer.import_document(other["id"], "材料.txt", b"abc", folder_id=folder["id"])
        assert len(list(importer.assets_dir.iterdir())) == before
    finally:
        repository.close()


def test_failed_conversion_is_visible_and_retains_original(tmp_path):
    repository = KnowledgeRepository(tmp_path / "library.db")
    try:
        project = repository.create_project("测试")
        importer = DocumentImportService(repository, tmp_path / "private")
        result = importer.import_document(project["id"], "bad.docx", b"bad zip")
        assert result["document"]["import_status"] == "failed" and result["version"] is None
        assert result["document"]["status"] == "draft"
        assert importer.original_path(result["document"]["id"]).read_bytes() == b"bad zip"
    finally:
        repository.close()
