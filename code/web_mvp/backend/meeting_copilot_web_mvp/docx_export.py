"""Small dependency-free DOCX writer for local meeting exports.

The exporter intentionally emits plain paragraphs and headings. It does not
need a document-conversion service or ``python-docx`` in the packaged runtime,
which keeps meeting content local and the release dependency set bounded.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from .review_export import (
    export_fact_items,
    export_minutes_markdown,
    export_transcript,
    format_meeting_datetime,
    format_meeting_duration,
    transcript_display_line,
    value_text,
)


_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml_text(value: Any) -> str:
    text = "".join(
        character
        for character in str(value)
        if character in "\t\n\r" or ord(character) >= 0x20
    )
    return escape(text, {'"': "&quot;"})


def _text_paragraph(text: str, *, style: str | None = None) -> str:
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
    safe_text = _xml_text(text)
    return (
        "<w:p>"
        f"<w:pPr>{style_xml}</w:pPr>"
        f"<w:r><w:t xml:space=\"preserve\">{safe_text}</w:t></w:r>"
        "</w:p>"
    )


def _markdown_lines(markdown: str) -> Iterable[tuple[str, str | None]]:
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            yield "", None
        elif line.startswith("### "):
            yield line[4:], "Heading2"
        elif line.startswith("## "):
            yield line[3:], "Heading2"
        elif line.startswith("# "):
            yield line[2:], "Heading2"
        elif line.startswith("- "):
            yield f"• {line[2:]}", None
        else:
            yield line, None


def _fact_line(fact: Any) -> str:
    if not isinstance(fact, dict):
        return f"• {value_text(fact)}"
    details: list[str] = []
    if fact.get("status") is not None:
        details.append(f"状态：{value_text(fact, 'status')}")
    if fact.get("owner") is not None:
        details.append(f"负责人：{value_text(fact, 'owner')}")
    if fact.get("deadline") is not None:
        details.append(f"截止：{value_text(fact, 'deadline')}")
    if fact.get("mitigation") is not None:
        details.append(f"缓解：{value_text(fact, 'mitigation')}")
    evidence = fact.get("evidence") if isinstance(fact.get("evidence"), dict) else {}
    evidence_id = value_text(evidence, "segment_id") or value_text(fact, "evidence_segment_id")
    if evidence_id:
        details.append(f"依据：{evidence_id}")
    suffix = f"（{'；'.join(details)}）" if details else ""
    return f"• {value_text(fact, 'text', 'item')}{suffix}"


def _payload_lines(payload: dict[str, Any]) -> list[tuple[str, str | None]]:
    meeting = dict(payload.get("meeting") or {})
    title = str(meeting.get("title") or "会议复盘").strip()
    lines: list[tuple[str, str | None]] = [(title, "Title")]
    lines.extend(
        [
            (f"会议日期：{format_meeting_datetime(meeting)}", None),
            (f"会议时长：{format_meeting_duration(meeting)}", None),
            (f"会议状态：{meeting.get('state') or 'unknown'}", None),
            ("", None),
            ("会议复盘", "Heading1"),
        ]
    )
    markdown = export_minutes_markdown(payload)
    lines.extend(_markdown_lines(markdown))
    if not markdown.strip():
        lines.append(("暂无复盘内容。", None))

    for heading, kind, key, fallback in (
        ("决策", "decisions", "decisions", payload.get("decision_candidates") or []),
        ("待办", "action_items", "action_items", payload.get("action_items") or []),
        ("风险", "risks", "risks", payload.get("risks") or []),
    ):
        facts = export_fact_items(payload, kind=kind, key=key, fallback=fallback)
        lines.append((heading, "Heading1"))
        if facts:
            lines.extend((_fact_line(fact), None) for fact in facts)
        else:
            lines.append(("暂无。", None))

    lines.append(("完整会议文字", "Heading1"))
    transcript = export_transcript(payload)
    if transcript:
        for segment in transcript:
            lines.append((transcript_display_line(segment), None))
    else:
        lines.append(("暂无会议文字。", None))
    return lines


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{_WORD_NS}">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Aptos" w:hAnsi="Aptos" w:eastAsia="PingFang SC"/>'
        '<w:lang w:val="zh-CN" w:eastAsia="zh-CN"/>'
        '</w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title">'
        '<w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="320" w:after="120"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2">'
        '<w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:spacing w:before="220" w:after="80"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>'
        '</w:styles>'
    )


def render_docx(payload: dict[str, Any]) -> bytes:
    body = "".join(_text_paragraph(text, style=style) for text, style in _payload_lines(payload))
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_WORD_NS}"><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _styles_xml())
        archive.writestr("word/_rels/document.xml.rels", document_relationships)
    return output.getvalue()
