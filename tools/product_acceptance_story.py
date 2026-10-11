"""Exercise only an isolated localhost candidate with synthetic project content.

An explicit --live-ask makes one paid project-only Ask request. The default has
no LLM calls. Real source records, versions and HTTP routes are used throughout.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code/web_mvp/backend"))


def read_ask_stream(lines, started: float) -> dict:
    """Read the route's NDJSON contract; an empty/error stream is not success."""
    messages = []
    first_content = None
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, TypeError) as error:
            raise ValueError("Ask stream contains invalid NDJSON") from error
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError("Ask stream event has no valid type")
        messages.append(item)
        if first_content is None and item.get("type") == "delta" and isinstance(item.get("text"), str) and item["text"]:
            first_content = round((time.perf_counter() - started) * 1000, 2)
    return {"events": messages, "http_to_first_content_ms": first_content,
            "total_ms": round((time.perf_counter() - started) * 1000, 2)}


def ask_stream_completed(ask: object) -> bool:
    if not isinstance(ask, Mapping) or not isinstance(ask.get("events"), list):
        return False
    events = ask["events"]
    if not events or not all(isinstance(item, Mapping) for item in events):
        return False
    starts = [item for item in events if item.get("type") == "started"]
    ends = [item for item in events if item.get("type") == "done"]
    if len(starts) != 1 or len(ends) != 1 or events[0] is not starts[0] or events[-1] is not ends[0]:
        return False
    if any(item.get("type") not in {"started", "delta", "done"} for item in events):
        return False
    message = ends[0].get("message")
    return (
        isinstance(message, Mapping) and message.get("status") == "completed"
        and not message.get("error_class")
        and isinstance(message.get("content"), str) and bool(message["content"].strip())
    )


def selected_folder_matches(response: object, *, meeting_id: str, project_id: str, folder_id: str | None) -> bool:
    """Check the persisted scope returned by the real PUT context contract."""
    if not isinstance(response, Mapping):
        return False
    context = response.get("context")
    if not isinstance(context, Mapping):
        return False
    required = {"meeting_id", "project_id", "folder_id", "selection_mode", "selected_document_ids"}
    return (
        required.issubset(context)
        and context["meeting_id"] == meeting_id
        and context["project_id"] == project_id
        and context["folder_id"] == folder_id
        and context["selection_mode"] == "project"
        and context["selected_document_ids"] == []
    )


def confirmed_final_reference(response: object, *, project_id: str, document_id: str, version_id: str) -> dict | None:
    """Use an exact, current search result; never synthesize a document citation."""
    if not isinstance(response, Mapping) or not isinstance(response.get("items"), list):
        return None
    required = {"source_type", "source_id", "document_id", "project_id", "title", "version_id",
                "version", "content_hash", "ordinal", "quote", "locator"}
    for item in response["items"]:
        if (
            isinstance(item, Mapping) and required.issubset(item)
            and item["source_type"] == "document" and item["project_id"] == project_id
            and item["document_id"] == document_id and item["version_id"] == version_id
            and isinstance(item["source_id"], str) and item["source_id"]
            and isinstance(item["quote"], str) and "验收负责人仍未指定，需要下次明确。" in item["quote"]
        ):
            return dict(item)
    return None


def task_inherited(response: object, *, project_id: str, task_id: str) -> bool:
    if not isinstance(response, Mapping) or response.get("project_id") != project_id:
        return False
    tasks = response.get("unfinished_tasks")
    return isinstance(tasks, list) and any(
        isinstance(item, Mapping) and item.get("id") == task_id and item.get("type") == "task"
        and item.get("status") in {"active", "unknown"}
        for item in tasks
    )


def run(port: int, output: Path, live_ask: bool) -> dict:
    import httpx

    run_id = "acceptance_" + uuid.uuid4().hex[:10]
    report = {"run_id": run_id, "scope": "synthetic candidate HTTP story; no microphone or user database", "checks": {}, "gateway_calls": 0}
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=70, trust_env=False) as client:
        def call(method, path, **kwargs):
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            response = client.request(method, path, **kwargs)
            if response.is_error:
                report["harness_failure"] = {"method": method, "path": path, "status": response.status_code}
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                raise RuntimeError(f"{method} {path} returned {response.status_code}")
            return response.json()

        project = next((item for item in call("GET", "/v2/knowledge/projects")["items"] if item["name"] == "验收客户A"), None)
        if project is None:
            raise RuntimeError("create the synthetic 验收客户A project in the candidate UI first")
        project_id = project["id"]
        documents = call("GET", "/v2/knowledge/documents", params={"project_id": project_id})["items"]
        initial_document = next(item for item in documents if item["title"] == "客户A发布条件 v1")
        folder_id = initial_document["folder_id"]
        meeting_id = run_id + "_first"
        call("POST", "/v2/meetings", json={"meeting_id": meeting_id, "title": "验收客户A · 首次会议"})
        call("PUT", f"/v2/meetings/{meeting_id}/preparation", json={"suggestion_policy": "off", "preset_id": "project"})
        context = call("PUT", f"/v2/knowledge/meetings/{meeting_id}/context", json={"project_id": project_id, "folder_id": folder_id})
        evidence = call("POST", "/v2/knowledge/search", json={"meeting_id": meeting_id, "query": "预算 上线", "limit": 6})
        report["checks"]["selected_folder_bound"] = selected_folder_matches(
            context, meeting_id=meeting_id, project_id=project_id, folder_id=folder_id,
        )
        report["selected_context"] = context.get("context") if isinstance(context, Mapping) else None
        report["search"] = evidence
        report["ids"] = {"project_id": project_id, "meeting_id": meeting_id, "initial_document_id": initial_document["id"]}
        if live_ask:
            started = time.perf_counter()
            report["gateway_calls"] += 1  # attempted logical Ask; not proof of successful billing or answer
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            with client.stream("POST", f"/v2/meetings/{meeting_id}/ask/stream", json={"question": "客户A现在预算是多少？可以直接上线吗？", "scope": "project"}) as response:
                response.raise_for_status()
                report["ask"] = read_ask_stream(response.iter_lines(), started)
            report["checks"]["ask_stream_completed_with_nonempty_answer"] = ask_stream_completed(report["ask"])
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        note = call("POST", f"/v2/meetings/{meeting_id}/notes", json={"title": "验收素材笔记", "body": "客户A预算120元，尚未验收。", "source_kind": "manual"})["note"]
        imported_note = call("POST", f"/v2/knowledge/notes/{note['note_id']}/import", json={"project_id": project_id, "folder_id": folder_id, "expected_source_version": note["version"]})
        report["checks"]["note_import_is_draft"] = imported_note["document"]["active_version_id"] is None
        note_document = call("POST", f"/v2/knowledge/documents/{imported_note['document']['id']}/confirm",
                             json={"version_id": imported_note["version"]["id"], "expected_revision": imported_note["document"]["revision"]})["document"]
        call("PATCH", f"/v2/notes/{note['note_id']}", json={"expected_version": note["version"], "body": "客户A预算120元，验收安排待确认。"})
        stale = call("GET", f"/v2/knowledge/documents/{note_document['id']}")["document"]
        report["checks"]["original_note_edit_invalidates_library_source"] = stale["source_status"] == "needs_review"
        final = call("PATCH", f"/v2/meetings/{meeting_id}/documents/minutes", json={"expected_revision": 0, "content_json": {"markdown": "客户A人工核对：预算120元。压测已完成并通过。验收负责人仍未指定，需要下次明确。"}})["document"]
        imported_final = call("POST", f"/v2/knowledge/meetings/{meeting_id}/import-final", json={"expected_source_version": final["user_final"]["version"], "folder_id": folder_id})
        report["checks"]["real_human_final_import_is_draft"] = imported_final["document"]["active_version_id"] is None
        final_document = call("POST", f"/v2/knowledge/documents/{imported_final['document']['id']}/confirm",
                              json={"version_id": imported_final["version"]["id"], "expected_revision": imported_final["document"]["revision"]})["document"]
        final_sources = call("POST", "/v2/knowledge/search", json={
            "project_id": project_id, "folder_id": folder_id, "document_ids": [final_document["id"]],
            "query": "验收负责人", "limit": 6,
        })
        task_source = confirmed_final_reference(final_sources, project_id=project_id,
                                                document_id=final_document["id"], version_id=final_document["active_version_id"])
        report["checks"]["confirmed_final_source_found_for_task"] = task_source is not None
        if task_source is None:
            report["harness_failure"] = {"code": "confirmed_final_source_unavailable"}
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError("the confirmed synthetic final has no exact source for the task")
        revision = call("GET", f"/v2/knowledge/projects/{project_id}/state")["project_revision"]
        state = call("POST", f"/v2/knowledge/projects/{project_id}/state", json={
            "type": "task", "text": "明确验收负责人", "source_refs": [task_source], "expected_project_revision": revision,
        })
        report["checks"]["confirmed_open_task_saved"] = state["item"]["status"] == "active"
        next_meeting = run_id + "_next"
        call("POST", "/v2/meetings", json={"meeting_id": next_meeting, "title": "验收客户A · 下次会议"})
        call("PUT", f"/v2/knowledge/meetings/{next_meeting}/context", json={"project_id": project_id, "folder_id": folder_id})
        inherited = call("GET", f"/v2/knowledge/meetings/{next_meeting}/project-state")
        memory = call("GET", f"/v2/knowledge/meetings/{next_meeting}/memory-context")
        report["inherited_state"] = inherited
        report["checks"]["confirmed_term_used_next_meeting"] = any(item["canonical"] == "Talktrace" for item in memory["terms"])
        report["checks"]["open_task_inherited"] = task_inherited(inherited, project_id=project_id, task_id=state["item"]["id"])
        page = call("GET", "/v2/notes/page", params={"meeting_id": meeting_id, "limit": 1})
        report["checks"]["notes_page_available"] = page["items"][0]["note_id"] == note["note_id"]
        report["ids"].update(next_meeting_id=next_meeting, note_id=note["note_id"], note_document_id=note_document["id"], final_document_id=imported_final["document"]["id"], task_id=state["item"]["id"])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in report.items() if key not in {"ask", "search", "inherited_state"}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-ask", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.port == 8765 or not 1024 <= args.port <= 65535 or not output.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("use an isolated candidate port and ignored artifacts output")
    if output.exists():
        parser.error("preserve the previous report; choose a fresh output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = run(args.port, output, args.live_ask)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    checks = report.get("checks", {})
    return 0 if checks and all(value is True for value in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
