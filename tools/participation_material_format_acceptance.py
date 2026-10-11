"""Actual, model-free attachment conversion checks in the isolated candidate."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import time
from zipfile import ZipFile, ZIP_DEFLATED

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/realtime-participation-20261007/material-formats"


def fixtures():
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body><w:p><w:r><w:t>需求评审：权益即时可见，通知可以稍后送达。</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>约束：复用现有服务，重复请求不能重复扣款。'
           '</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    yield "simple-review.docx", out.getvalue(), "extracted", "权益即时可见", "docx_paragraph"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Timeout does not mean delivery failed. Query by request ID.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO(); writer.write(out)
    yield "text-reference.pdf", out.getvalue(), "extracted", "Query by request ID", "pdf_page"
    writer.add_blank_page(width=612, height=792)
    out = BytesIO(); writer.write(out)
    yield "partial-reference.pdf", out.getvalue(), "needs_ocr", "Query by request ID", "pdf_page"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {"scope": "synthetic simple fixtures; real attachment HTTP conversion; no AI, capture, OCR or authorization", "cases": []}
    with httpx.Client(base_url="http://127.0.0.1:8768", trust_env=False, timeout=20) as client:
        response = client.post("/v2/material-sets", json={}); response.raise_for_status()
        set_id = response.json()["material_set"]["id"]
        report["material_set_id"] = set_id
        for name, payload, status, required, locator in fixtures():
            (OUTPUT / name).write_bytes(payload)
            response = client.post(f"/v2/material-sets/{set_id}/attachments", files={"file": (name, payload)}, data={"title": name}); response.raise_for_status()
            aid = response.json()["attachment"]["id"]
            for _ in range(80):
                response = client.get(f"/v2/material-sets/{set_id}/attachments/{aid}"); response.raise_for_status()
                detail = response.json()
                if detail.get("version") or (detail.get("job") or {}).get("state") in {"failed", "cancelled"}:
                    break
                time.sleep(.25)
            version = detail.get("version") or {}
            response = client.get(f"/v2/material-sets/{set_id}/attachments/{aid}/versions/{version['id']}/spans"); response.raise_for_status()
            spans = response.json()["items"]
            text = version.get("content") or "\n".join(s.get("text") or s.get("quote") or "" for s in spans)
            actual_status = detail["attachment"].get("import_status") or version.get("import_status")
            entry = {"file": name, "attachment_id": aid, "detail": detail,
                     "expected_status": status, "actual_status": actual_status,
                     "required_text_present": required in text,
                     "expected_locator_present": any((s.get("locator") or {}).get("type") == locator for s in spans),
                     "enabled": detail["attachment"].get("enabled"), "spans": spans}
            report["cases"].append(entry)
    (OUTPUT / "verification-r01.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(OUTPUT / "verification-r01.json"), "cases": [{k: v for k, v in c.items() if k != "detail"} for c in report["cases"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
