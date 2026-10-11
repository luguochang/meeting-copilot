"""Bounded, read-only project ZIP snapshots; never reads runtime files or runs jobs."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from tempfile import SpooledTemporaryFile
import time
from typing import Any, BinaryIO, Mapping
from zipfile import ZIP_DEFLATED, ZipFile

from .knowledge_repository import KnowledgeRepository, KnowledgeScopeError
from .project_outcomes import ProjectOutcomes, review_content_text
from .source_library_provenance import library_source_status


MAX_EXPORT_DOCUMENTS = 3_000
MAX_EXPORT_FOLDERS = 3_000
MAX_EXPORT_STATE_ITEMS = 10_000
MAX_EXPORT_SPANS = 50_000
MAX_EXPORT_BYTES = 128 * 1024 * 1024
MAX_FOLDER_DEPTH = 12
MAX_EXPORT_PATH_CHARACTERS = 180
EXPORT_SCHEMA = "meeting_copilot.project_export.v1"
_REF_FIELDS = (
    "source_type", "source_id", "document_id", "project_id", "meeting_id", "title",
    "version_id", "version", "version_kind", "content_hash", "ordinal", "quote", "document_kind",
)
_LOCATOR_FIELDS = {"type", "page", "paragraph", "offset", "document_kind", "kind",
                   "source_id", "source_version", "source_hash"}
_EXCLUDED = [
    "草稿和未确认版本；删除的资料；旧的确认版本（只导出当前选中的确认版本）。",
    "尚未确认的 AI 项目变更提议；个人记忆、术语和偏好（包括候选）。",
    "原始上传二进制、会议音频、未明确收进资料库的笔记或会议正文。",
    "环境变量、密钥与供应商设置、数据库、日志、作业快照、租约及运行时私有文件。",
]


class ProjectExportError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 413) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


@dataclass
class ProjectExportArchive:
    file: BinaryIO
    filename: str
    size_bytes: int


def _too_large(reason: str) -> ProjectExportError:
    return ProjectExportError("project_export_too_large", f"项目导出超过限制：{reason}。未生成截断文件，请拆分项目后重试。")


def _invalid(message: str) -> ProjectExportError:
    return ProjectExportError("project_export_invalid_source", message, status_code=409)


def _component(name: str, stable_id: str, prefix: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-_")[:24] or prefix
    return f"{prefix}-{slug}-{hashlib.sha256(stable_id.encode()).hexdigest()[:16]}"


def _locator(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid("来源定位信息无效，请先核对资料。")
    return {key: item for key, item in value.items() if key in _LOCATOR_FIELDS
            and isinstance(item, (str, int)) and not isinstance(item, bool)}


class _ArchiveWriter:
    def __init__(self, archive: ZipFile) -> None:
        self.archive, self.total = archive, 0
        self.files: list[dict[str, Any]] = []
        self.paths: set[str] = set()

    def text(self, path: str, value: str) -> None:
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or "\\" in path or path in self.paths:
            raise _invalid("导出目录结构无效，请先核对项目文件夹。")
        if len(path) > MAX_EXPORT_PATH_CHARACTERS:
            raise _too_large("导出相对路径长度上限为 180 个字符")
        content = value.encode("utf-8")
        if self.total + len(content) > MAX_EXPORT_BYTES:
            raise _too_large("全部文件的未压缩大小上限为 128 MiB")
        self.archive.writestr(path, content)
        self.total += len(content)
        self.paths.add(path)
        self.files.append({"path": path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})

    def json(self, path: str, value: Any) -> None:
        self.text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _folder_paths(folders: list[dict[str, Any]]) -> dict[str, str]:
    indexed = {folder["id"]: folder for folder in folders}
    components = {folder["id"]: f"f-{index:04d}" for index, folder in enumerate(folders, 1)}
    paths: dict[str, str] = {}
    for folder in folders:
        lineage, seen, current = [], set(), folder["id"]
        while current is not None:
            if current in seen or current not in indexed:
                raise _invalid("项目文件夹存在循环或跨项目父目录，已停止导出。")
            seen.add(current)
            if len(seen) > MAX_FOLDER_DEPTH:
                raise _too_large("文件夹层级上限为 12 层")
            row = indexed[current]
            lineage.append(components[current])
            current = row["parent_id"]
        paths[folder["id"]] = "/".join(reversed(lineage))
    return paths


def _source_ref(repository: KnowledgeRepository, project_id: str, ref: Any) -> dict[str, Any]:
    if not isinstance(ref, dict) or ref.get("project_id") != project_id:
        raise _invalid("项目结果包含跨项目来源，已停止导出，请先核对来源。")
    source_type = ref.get("source_type")
    deleted = False
    omission_reason = "source_deleted"
    if source_type == "document":
        row = repository._conn.execute("SELECT project_id,status FROM knowledge_documents WHERE id=?", (ref.get("document_id"),)).fetchone()
        version = repository._conn.execute("SELECT document_id,number,content_hash,confirmed_at_ms FROM knowledge_versions WHERE id=?",
                                           (ref.get("version_id"),)).fetchone()
        span = repository._conn.execute("SELECT s.version_id,s.text,s.locator_json,v.document_id,d.project_id FROM knowledge_spans s "
            "JOIN knowledge_versions v ON v.id=s.version_id JOIN knowledge_documents d ON d.id=v.document_id WHERE s.id=?",
            (ref.get("source_id"),)).fetchone()
        if version is not None and version["document_id"] != ref.get("document_id"):
            raise _invalid("项目结果的版本属于其他资料，已停止导出。")
        if span is not None and (span["project_id"] != project_id or span["document_id"] != ref.get("document_id")
                                 or span["version_id"] != ref.get("version_id")):
            raise _invalid("项目结果的来源片段或版本归属不匹配，已停止导出。")
        if version is not None and (version["number"] != ref.get("version") or version["content_hash"] != ref.get("content_hash")
                                    or version["confirmed_at_ms"] is None):
            raise _invalid("项目结果的已确认来源版本无法验证，已停止导出。")
        if span is not None and (json.loads(span["locator_json"]) != ref.get("locator")
                                 or not isinstance(ref.get("quote"), str) or not ref["quote"].strip() or ref["quote"] not in span["text"]):
            raise _invalid("项目结果的来源定位或引用原文无法验证，已停止导出。")
        if row is None:
            deleted = True
        elif row["project_id"] != project_id:
            raise _invalid("项目结果包含跨项目资料，已停止导出。")
        else:
            deleted = row["status"] == "deleted"
        if not deleted and (version is None or span is None):
            deleted, omission_reason = True, "source_version_unavailable"
    elif source_type in {"review_document", "meeting_entity"}:
        context = repository._conn.execute("SELECT project_id FROM meeting_knowledge_context WHERE meeting_id=?", (ref.get("meeting_id"),)).fetchone()
        if context is None or context[0] != project_id:
            raise _invalid("项目结果包含其他项目的会议来源，已停止导出。")
        if source_type == "review_document":
            row = repository._conn.execute("SELECT meeting_id,document_kind FROM review_documents WHERE document_id=?", (ref.get("source_id"),)).fetchone()
            if row is not None and row[0] != ref.get("meeting_id"):
                raise _invalid("项目结果的会议来源不匹配，已停止导出。")
            deleted = row is None
            if row is not None:
                if ref.get("version_kind") != "user_final" or ref.get("document_kind") != row["document_kind"]:
                    raise _invalid("项目结果不是所选会议的人工终稿来源，已停止导出。")
                historical = repository._conn.execute("SELECT content_json FROM review_document_revisions "
                    "WHERE document_id=? AND version_kind='user_final' AND author='user' AND version=? ORDER BY revision DESC LIMIT 1",
                    (ref.get("source_id"), ref.get("version"))).fetchone()
                if historical is None:
                    deleted, omission_reason = True, "source_version_unavailable"
                else:
                    content = json.loads(historical[0])
                    content_hash = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    if content_hash != ref.get("content_hash") or not isinstance(ref.get("quote"), str) or not ref["quote"].strip() or ref["quote"] not in review_content_text(content):
                        raise _invalid("项目结果的人工终稿版本或引用无法验证，已停止导出。")
        else:
            row = repository._conn.execute("SELECT 1 FROM meeting_entities WHERE meeting_id=? AND entity_id=?", (ref.get("meeting_id"), ref.get("source_id"))).fetchone()
            deleted = row is None
    else:
        raise _invalid("项目结果的来源类型无法验证，已停止导出。")
    result = {key: ref[key] for key in _REF_FIELDS if key in ref}
    result["locator"] = _locator(ref.get("locator", {}))
    if deleted:
        result.pop("quote", None)
        result["quote_omitted_reason"] = omission_reason
    return result


def _state(repository: KnowledgeRepository, project_id: str) -> list[dict[str, Any]]:
    size = repository._conn.execute("SELECT count(*),coalesce(sum(length(cast(source_refs_json AS BLOB))+length(cast(text AS BLOB))),0) "
                                    "FROM project_state_items WHERE project_id=?", (project_id,)).fetchone()
    if size[0] > MAX_EXPORT_STATE_ITEMS or size[1] > MAX_EXPORT_BYTES:
        raise _too_large("已确认项目结果的数量或大小")
    rows = repository._conn.execute("SELECT * FROM project_state_items WHERE project_id=? ORDER BY effective_at_ms,id LIMIT ?",
                                    (project_id, MAX_EXPORT_STATE_ITEMS + 1)).fetchall()
    if len(rows) > MAX_EXPORT_STATE_ITEMS:
        raise _too_large("已确认项目结果上限为 10000 条")
    service = ProjectOutcomes(repository)
    items = []
    for row in rows:
        refs = json.loads(row["source_refs_json"])
        safe_refs = [_source_ref(repository, project_id, ref) for ref in refs]
        try:
            service._refs(refs, project_id)  # Read-only validation; wiki()/reconcile() would write.
        except (KnowledgeScopeError, KeyError, ValueError):
            evidence_status = "stale"
        else:
            evidence_status = "valid" if refs else "user_declared"
        if any("quote_omitted_reason" in ref for ref in safe_refs):
            evidence_status = "stale"
        item = {key: row[key] for key in (
            "id", "project_id", "type", "text", "status", "owner", "due", "source_meeting_id", "origin",
            "supersedes_id", "revision", "effective_at_ms", "confirmed_at_ms", "created_at_ms", "updated_at_ms",
        )}
        item.update(source_refs=safe_refs, evidence_status=evidence_status,
                    export_effective_status="needs_review" if evidence_status == "stale" and row["status"] in {"active", "unknown"} else row["status"])
        items.append(item)
    return items


def _markdown(value: str) -> str:
    value = value.replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")
    return re.sub(r"([\\\[\]()])", r"\\\1", value)


def _wiki(project: Mapping[str, Any], items: list[dict[str, Any]], documents: list[dict[str, Any]]) -> str:
    sections = [
        ("当前决定", [item for item in items if item["type"] == "decision" and item["export_effective_status"] == "active"]),
        ("未完成事项", [item for item in items if item["type"] == "task" and item["export_effective_status"] in {"active", "unknown"}]),
        ("开放问题", [item for item in items if item["type"] == "question" and item["export_effective_status"] in {"active", "unknown"}]),
        ("来源变化，待核对", [item for item in items if item["export_effective_status"] == "needs_review"]),
        ("已关闭或被替代的结果", [item for item in items if item["export_effective_status"] in {"done", "cancelled", "superseded", "discarded"}]),
    ]
    lines = [f"# {_markdown(project['name'])}", "", "此页仅由已确认项目结果生成。来源变化只在本导出中标记待核对，不修改资料库。"]
    for title, grouped in sections:
        lines.extend(["", f"## {title}", ""])
        if not grouped:
            lines.append("暂无。")
        for item in grouped:
            lines.append(f"- {_markdown(item['text'])}（状态：{item['export_effective_status']}；负责人：{_markdown(item['owner'] or '未指定')}；期限：{_markdown(item['due'] or '未知')}）")
            for ref in item["source_refs"]:
                lines.append(f"  - 来源 {ref['source_type']} / {ref['source_id']} / 版本 {ref.get('version', '未知')}；完整溯源见 state.json。")
    lines.extend(["", "## 已确认资料", ""])
    if not documents:
        lines.append("暂无。")
    for document in documents:
        lines.append(f"- [{_markdown(document['title'])}]({document['text_path']})（版本 {document['version']}；状态 {document['status']}；来源 {document['source_status']}）")
    return "\n".join(lines) + "\n"


def build_project_export(repository: KnowledgeRepository, project_id: str) -> ProjectExportArchive:
    """One consistent read transaction; oversized/corrupt snapshots fail before any HTTP body."""
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with repository._lock:
            connection = repository._conn
            own_transaction = not connection.in_transaction
            if own_transaction:
                connection.execute("BEGIN")
            try:
                project = repository._project(project_id, active=False)
                folders = [dict(row) for row in connection.execute("SELECT * FROM knowledge_folders WHERE project_id=? ORDER BY id LIMIT ?",
                                                                 (project_id, MAX_EXPORT_FOLDERS + 1)).fetchall()]
                if len(folders) > MAX_EXPORT_FOLDERS:
                    raise _too_large("文件夹上限为 3000 个")
                folder_paths = _folder_paths(folders)
                rows = connection.execute("SELECT d.* FROM knowledge_documents d JOIN knowledge_versions v ON v.id=d.active_version_id "
                    "WHERE d.project_id=? AND d.status!='deleted' AND v.document_id=d.id AND v.confirmed_at_ms IS NOT NULL ORDER BY d.id LIMIT ?",
                    (project_id, MAX_EXPORT_DOCUMENTS + 1)).fetchall()
                if len(rows) > MAX_EXPORT_DOCUMENTS:
                    raise _too_large("已确认资料上限为 3000 份")
                text_size = connection.execute("SELECT coalesce(sum(length(cast(v.content AS BLOB))),0) FROM knowledge_documents d "
                    "JOIN knowledge_versions v ON v.id=d.active_version_id WHERE d.project_id=? AND d.status!='deleted' "
                    "AND v.document_id=d.id AND v.confirmed_at_ms IS NOT NULL", (project_id,)).fetchone()[0]
                if text_size > MAX_EXPORT_BYTES:
                    raise _too_large("已确认资料全文的大小")
                excluded_documents = connection.execute("SELECT count(*) FROM knowledge_documents WHERE project_id=?", (project_id,)).fetchone()[0] - len(rows)
                items = _state(repository, project_id)
                documents, span_count = [], 0
                with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=False) as archive:
                    writer = _ArchiveWriter(archive)
                    writer.json("project.json", {**project, "folders": [{**folder, "export_path": folder_paths[folder['id']]} for folder in folders]})
                    writer.json("state.json", {"project_id": project_id, "project_revision": project["revision"], "items": items})
                    for row in rows:
                        document = dict(row)
                        version = connection.execute("SELECT * FROM knowledge_versions WHERE id=?", (document["active_version_id"],)).fetchone()
                        if hashlib.sha256(version["content"].encode()).hexdigest() != version["content_hash"]:
                            raise _invalid("已确认资料的内容校验不匹配，请先核对资料。")
                        spans = connection.execute("SELECT id,ordinal,text,locator_json FROM knowledge_spans WHERE version_id=? ORDER BY ordinal LIMIT ?",
                                                   (version["id"], MAX_EXPORT_SPANS - span_count + 1)).fetchall()
                        span_count += len(spans)
                        if span_count > MAX_EXPORT_SPANS:
                            raise _too_large("来源段落上限为 50000 条")
                        folder_path = folder_paths.get(document["folder_id"], "") if document["folder_id"] is not None else ""
                        if document["folder_id"] is not None and not folder_path:
                            raise _invalid("资料属于跨项目文件夹，已停止导出。")
                        base = "/".join(part for part in ("sources", folder_path, _component(document["title"], document["id"], "d")) if part)
                        source_status = library_source_status(connection, document["id"], version["id"])
                        summary = {key: document[key] for key in ("id", "project_id", "folder_id", "kind", "title", "source_meeting_id", "status", "revision")}
                        summary.update(version_id=version["id"], version=version["number"], content_hash=version["content_hash"],
                                       origin=version["origin"], confirmed_at_ms=version["confirmed_at_ms"], source_status=source_status,
                                       text_path=base + ".txt", metadata_path=base + ".json")
                        provenance = connection.execute("SELECT source_kind,source_id,source_version,source_hash FROM knowledge_source_versions WHERE library_version_id=? AND document_id=?",
                                                        (version["id"], document["id"])).fetchone()
                        metadata = {**summary, "created_at_ms": version["created_at_ms"],
                                    "original_filename": re.split(r"[/\\]", document["filename"])[-1] if document["filename"] else None,
                                    "provenance": dict(provenance) if provenance is not None else None,
                                    "spans": [{"id": span["id"], "ordinal": span["ordinal"], "text": span["text"],
                                               "locator": _locator(json.loads(span["locator_json"]))} for span in spans]}
                        writer.text(summary["text_path"], version["content"])
                        writer.json(summary["metadata_path"], metadata)
                        documents.append(summary)
                    writer.text("wiki.md", _wiki(project, items, documents))
                    writer.text("README.txt", "项目资料导出（UTF-8）\n\n包含：当前选中的已确认资料全文和来源目录、项目/文件夹 JSON、人工确认的决定/待办/问题（含已关闭状态）、可重建 Wiki 和 manifest 文件校验。\n禁用资料或来源变动的确认文本仍保留，状态明确标注，不代表当前有效事实。删除来源的引用原文不再导出，并注明 source_deleted。\n\n不包含：\n" + "\n".join(_EXCLUDED) + "\n\n本导出是同一 SQLite 只读事务的快照；不触发模型、作业或状态修正。所有文档完整输出，超限明确失败，不截断。目录名称为安全短名，真实中文名称、稳定 ID、版本和定位见 JSON。\n")
                    writer.json("manifest.json", {
                        "schema_version": EXPORT_SCHEMA, "project_id": project_id, "project_revision": project["revision"],
                        "exported_at_ms": time.time_ns() // 1_000_000, "snapshot_consistent": True, "truncated": False,
                        "document_count": len(documents), "folder_count": len(folders), "state_item_count": len(items),
                        "span_count": span_count, "excluded_document_count": excluded_documents,
                        "limits": {"documents": MAX_EXPORT_DOCUMENTS, "uncompressed_bytes": MAX_EXPORT_BYTES,
                                   "folders": MAX_EXPORT_FOLDERS, "state_items": MAX_EXPORT_STATE_ITEMS, "spans": MAX_EXPORT_SPANS,
                                   "folder_depth": MAX_FOLDER_DEPTH, "relative_path_characters": MAX_EXPORT_PATH_CHARACTERS},
                        "excluded": _EXCLUDED, "documents": documents,
                        "locator_fields": sorted(_LOCATOR_FIELDS),
                        "files": list(writer.files), "manifest_self_hash_excluded": True,
                    })
            finally:
                if own_transaction:
                    connection.execute("ROLLBACK")
        size = output.tell()
        if size > MAX_EXPORT_BYTES:
            raise _too_large("ZIP 下载大小上限为 128 MiB")
        output.seek(0)
        return ProjectExportArchive(output, "project-" + hashlib.sha256(project_id.encode()).hexdigest()[:16] + ".zip", size)
    except BaseException:
        output.close()
        raise
