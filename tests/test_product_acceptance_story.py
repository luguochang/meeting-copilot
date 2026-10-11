"""The synthetic HTTP story must verify scope and fail honestly without a provider."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("product_acceptance_story", ROOT / "tools/product_acceptance_story.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def ask_events():
    return [
        {"type": "started", "thread_id": "thread_synthetic"},
        {"type": "delta", "text": "预算120元，尚未压测。"},
        {"type": "done", "message": {"status": "completed", "content": "预算120元，尚未压测。", "error_class": None}},
    ]


def test_ask_reads_actual_ndjson_including_first_content():
    events = ask_events()
    result = tool.read_ask_stream(["", *(json.dumps(item, ensure_ascii=False) for item in events)], tool.time.perf_counter())
    assert result["events"] == events
    assert result["http_to_first_content_ms"] is not None
    assert result["total_ms"] >= result["http_to_first_content_ms"]
    assert tool.ask_stream_completed(result)


@pytest.mark.parametrize("events", [
    [], [{"type": "started"}], [{"type": "delta", "text": "非空不等于完成"}],
    [{"type": "started"}, {"type": "error", "error_class": "StreamingProviderError"}],
    [{"type": "started"}, {"type": "done", "message": {"status": "failed", "content": "旧结果"}}],
    [{"type": "started"}, {"type": "done", "message": {"status": "completed", "content": " "}}],
    [{"type": "started"}, {"type": "done", "message": {"status": "completed", "content": "旧结果", "error_class": "EvidenceSuperseded"}}],
    [{"type": "done", "message": {"status": "completed", "content": "无started"}}],
    [*ask_events(), {"type": "delta", "text": "结束后还有流"}],
    [ask_events()[0], *ask_events()],
    [*ask_events(), ask_events()[-1]],
    [ask_events()[0], {"type": "error"}, *ask_events()[1:]],
    [None],
])
def test_incomplete_or_failed_ask_stream_cannot_pass(events):
    assert not tool.ask_stream_completed({"events": events})


@pytest.mark.parametrize("line", ["data: {}", "not json", "[]", "null", '{}', '{"type":3}'])
def test_ask_does_not_silently_discard_wrong_protocol_or_invalid_events(line):
    with pytest.raises(ValueError):
        tool.read_ask_stream([line], tool.time.perf_counter())


def test_main_preserves_existing_report_without_repeating_live_request(monkeypatch, tmp_path):
    output = tmp_path / "artifacts" / "already-paid-story.json"
    output.parent.mkdir()
    original = b'{"historical_failure":"preserve this evidence"}'
    output.write_bytes(original)
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool.sys, "argv", ["product_acceptance_story.py", "--output", str(output), "--live-ask"])

    def forbidden_run(*args):
        pytest.fail("an existing report must not trigger another live request")

    monkeypatch.setattr(tool, "run", forbidden_run)
    with pytest.raises(SystemExit) as stopped:
        tool.main()
    assert stopped.value.code == 2
    assert output.read_bytes() == original


def context_response(folder_id="folder_expected"):
    return {"context": {
        "meeting_id": "meeting_expected", "project_id": "project_expected", "folder_id": folder_id,
        "selection_mode": "project", "selected_document_ids": [], "context_revision": 1,
    }}


def matches(response, folder_id="folder_expected"):
    return tool.selected_folder_matches(
        response, meeting_id="meeting_expected", project_id="project_expected", folder_id=folder_id,
    )


@pytest.mark.parametrize("folder_id", ["folder_expected", None])
def test_context_scope_accepts_only_the_requested_project_and_directory(folder_id):
    assert matches(context_response(folder_id), folder_id)


@pytest.mark.parametrize("field,value", [
    ("project_id", "other_project"),
    ("folder_id", "other_folder"),
    ("meeting_id", "other_meeting"),
    ("selection_mode", "documents"),
    ("selection_mode", "none"),
    ("selected_document_ids", ["unexpected_document"]),
])
def test_nonempty_response_with_wrong_scope_cannot_pass(field, value):
    response = context_response()
    response["context"][field] = value
    assert not matches(response)


@pytest.mark.parametrize("field", ["project_id", "folder_id", "meeting_id", "selection_mode", "selected_document_ids"])
def test_missing_context_fields_cannot_pass_even_when_folder_is_null(field):
    response = context_response(None)
    del response["context"][field]
    assert not matches(response, None)


@pytest.mark.parametrize("response", [None, [], {}, {"context": None}, {"context": []}])
def test_invalid_context_wrapper_is_not_a_success(response):
    assert not matches(response)


@pytest.mark.parametrize("checks,expected_code", [
    ({"selected_folder_bound": True, "notes_page_available": True}, 0),
    ({"selected_folder_bound": False, "notes_page_available": True}, 1),
    ({"selected_folder_bound": True, "notes_page_available": False}, 1),
    ({"selected_folder_bound": 1}, 1),
    ({}, 1),
])
def test_main_uses_checks_as_exit_status_and_keeps_the_report(monkeypatch, tmp_path, capsys, checks, expected_code):
    output = tmp_path / "artifacts" / "synthetic-story.json"
    report = {"run_id": "synthetic_only", "checks": checks, "gateway_calls": 0}
    observed = []

    def fake_run(port, report_path, live_ask):
        observed.append((port, report_path, live_ask))
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "run", fake_run)
    monkeypatch.setattr(tool.sys, "argv", ["product_acceptance_story.py", "--output", str(output)])
    assert tool.main() == expected_code
    assert observed == [(8766, output.resolve(), False)]
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert json.loads(capsys.readouterr().out) == report


def final_reference():
    return {
        "source_type": "document", "source_id": "span_expected", "document_id": "document_expected",
        "project_id": "project_expected", "title": "合成终稿", "version_id": "version_expected",
        "version": 1, "content_hash": "a" * 64, "ordinal": 0,
        "quote": "预算120元。验收负责人仍未指定，需要下次明确。", "locator": {"type": "converted_text", "paragraph": 1},
    }


def find_final(response):
    return tool.confirmed_final_reference(
        response, project_id="project_expected", document_id="document_expected", version_id="version_expected",
    )


def test_task_reference_keeps_the_exact_versioned_search_result():
    reference = final_reference()
    found = find_final({"items": [reference]})
    assert found == reference
    assert found is not reference


@pytest.mark.parametrize("field,value", [
    ("project_id", "other_project"), ("document_id", "other_document"), ("version_id", "old_version"),
    ("source_type", "review_document"), ("quote", "没有对应任务原文。"), ("source_id", ""),
])
def test_task_source_cannot_come_from_other_documents_or_unrelated_quotes(field, value):
    reference = final_reference()
    reference[field] = value
    assert find_final({"items": [reference]}) is None


@pytest.mark.parametrize("response", [None, {}, {"items": []}, {"items": [{"document_id": "document_expected"}]}])
def test_missing_real_task_reference_is_not_replaced_by_a_fabricated_source(response):
    assert find_final(response) is None


@pytest.mark.parametrize("project_id,tasks,expected", [
    ("project_expected", [{"id": "task_expected", "type": "task", "status": "active"}], True),
    ("project_expected", [{"id": "task_expected", "type": "task", "status": "unknown"}], True),
    ("project_expected", [{"id": "task_other", "type": "task", "status": "active", "text": "明确验收负责人"}], False),
    ("project_expected", [{"id": "task_expected", "type": "decision", "status": "active"}], False),
    ("project_expected", [{"id": "task_expected", "type": "task", "status": "done"}], False),
    ("other_project", [{"id": "task_expected", "type": "task", "status": "active"}], False),
    ("project_expected", [], False),
])
def test_inheritance_checks_the_specific_unfinished_task_not_another_task_with_the_same_text(project_id, tasks, expected):
    assert tool.task_inherited({"project_id": project_id, "unfinished_tasks": tasks},
                               project_id="project_expected", task_id="task_expected") is expected


def test_story_stops_and_retains_a_failure_report_when_the_confirmed_final_has_no_source(monkeypatch, tmp_path):
    import httpx

    requests = []
    responses = {
        ("GET", "/v2/knowledge/projects"): {"items": [{"id": "project_expected", "name": "验收客户A"}]},
        ("GET", "/v2/knowledge/documents"): {"items": [
            {"id": "initial_document", "title": "客户A发布条件 v1", "folder_id": "folder_expected"},
        ]},
        ("POST", "/v2/knowledge/notes/note_expected/import"): {
            "document": {"id": "note_document", "revision": 1, "active_version_id": None}, "version": {"id": "note_version"},
        },
        ("POST", "/v2/knowledge/documents/note_document/confirm"): {"document": {"id": "note_document"}},
        ("PATCH", "/v2/notes/note_expected"): {},
        ("GET", "/v2/knowledge/documents/note_document"): {"document": {"source_status": "needs_review"}},
        ("POST", "/v2/knowledge/documents/document_expected/confirm"): {
            "document": {"id": "document_expected", "active_version_id": "version_expected"},
        },
    }

    def handle(request):
        path = request.url.path
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, path, payload))
        result = responses.get((request.method, path))
        if request.method == "POST" and path == "/v2/meetings":
            result = {}
        elif path.endswith("/preparation"):
            assert payload["suggestion_policy"] == "off"
            result = {}
        elif path.endswith("/context"):
            result = context_response()
            result["context"]["meeting_id"] = path.split("/")[-2]
        elif path.endswith("/notes"):
            result = {"note": {"note_id": "note_expected", "version": 1}}
        elif path.endswith("/documents/minutes"):
            result = {"document": {"user_final": {"version": 1}}}
        elif path.endswith("/import-final"):
            result = {"document": {"id": "document_expected", "revision": 1, "active_version_id": None},
                      "version": {"id": "version_expected"}}
        elif path == "/v2/knowledge/search":
            if "project_id" in payload:
                assert payload["project_id"] == "project_expected"
                assert payload["folder_id"] == "folder_expected"
                assert payload["document_ids"] == ["document_expected"]
            result = {"items": []}
        assert result is not None, f"unexpected request: {request.method} {path}"
        return httpx.Response(200, json=result)

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    output = tmp_path / "failed-synthetic-story.json"
    with pytest.raises(RuntimeError, match="no exact source for the task"):
        tool.run(8766, output, False)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["harness_failure"] == {"code": "confirmed_final_source_unavailable"}
    assert report["checks"]["confirmed_final_source_found_for_task"] is False
    assert report["gateway_calls"] == 0
    assert not any(path.endswith("/state") or "/ask/" in path for _, path, _ in requests)
