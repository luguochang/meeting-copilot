from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.knowledge_aliases import MAX_EXPANDED_TOKENS, expanded_fts_expression
from meeting_copilot_web_mvp.knowledge_api import mount_knowledge_api
from meeting_copilot_web_mvp.knowledge_context import retrieve_meeting_knowledge, source_evidence_state
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository, _tokens
from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
from meeting_copilot_web_mvp.prepared_brief import PreparedBriefSources
from meeting_copilot_web_mvp.user_memory import UserMemory
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def local(tmp_path):
    persistence = V2Persistence(tmp_path / "synthetic.db")
    for meeting in ("current", "next"):
        persistence.create_meeting(meeting_id=meeting, title="Synthetic", now_ms=1)
    library = KnowledgeRepository(tmp_path / "synthetic.db")
    project = library.create_project("Synthetic project")
    for meeting in ("current", "next"):
        library.bind_meeting_context(meeting, project["id"])
    yield library, persistence, UserMemory(library), project
    library.close()
    persistence.close()


def document(library, project, text, folder_id=None):
    doc = library.create_document(project["id"], "资料", folder_id=folder_id)
    version = library.add_version(doc["id"], text)
    return library.confirm_version(doc["id"], version["id"], expected_revision=doc["revision"] + 1), version


def term(memory, project, value="Aster", aliases=None, **kwargs):
    fields = {"scope": "project", "scope_id": project["id"], "kind": "term", "memory_key": "product",
              "value": value, "aliases": ["Alpha"] if aliases is None else aliases}
    item = memory.create(**(fields | kwargs))
    return memory.update(item["id"], expected_revision=item["revision"], status="active")


def test_bidirectional_alias_preserves_other_words_and_original_versioned_sources(local):
    library, persistence, memory, project = local
    current, version = document(library, project, "Aster 120 pending；压测未通过。")
    old, _ = document(library, project, "Alpha 120 pending；原先拼写。")
    document(library, project, "Aster 900 pending；压测已通过。")
    term(memory, project)
    for query in ("Alpha 120 pending", "Aster 120 pending", "Ａｌｐｈａ 120 pending"):
        rows = library.search(query, project_id=project["id"])
        assert {row["document_id"] for row in rows} == {current["id"], old["id"]}
        assert {row["quote"] for row in rows} == {"Aster 120 pending；压测未通过。", "Alpha 120 pending；原先拼写。"}
    evidence = retrieve_meeting_knowledge(library, "current", "请问Alpha压测未通过")
    assert len(evidence) == 1 and evidence[0]["document_id"] == current["id"]
    assert evidence[0]["version_id"] == version["id"] and evidence[0]["content_hash"] == version["content_hash"]
    assert source_evidence_state(persistence._conn, "current", evidence) == "valid"
    assert "segment_id" not in evidence[0]


@pytest.mark.parametrize("status", ["candidate", "disabled", "deleted"])
def test_unconfirmed_or_revoked_alias_is_never_used(local, status):
    library, _, memory, project = local
    document(library, project, "Aster pending")
    item = memory.create(scope="project", scope_id=project["id"], kind="term", memory_key="product", value="Aster", aliases=["Alpha"])
    if status != "candidate":
        active = memory.update(item["id"], expected_revision=1, status="active")
        memory.update(item["id"], expected_revision=active["revision"], status=status)
    assert library.search("Alpha", project_id=project["id"]) == []
    assert retrieve_meeting_knowledge(library, "current", "Alpha") == []


def test_other_project_terms_and_documents_do_not_leak(local):
    library, _, memory, project = local
    other = library.create_project("Other")
    term(memory, other)
    document(library, other, "Aster pending")
    document(library, project, "Aster pending")
    assert library.search("Alpha", project_id=project["id"]) == []
    assert retrieve_meeting_knowledge(library, "current", "Alpha") == []
    assert len(library.search("Alpha", project_id=other["id"])) == 1


def test_scene_and_meeting_priority_reuse_the_memory_contract(local):
    library, persistence, memory, project = local
    for name in ("Aster", "Birch", "Cedar", "Global"):
        document(library, project, name + " pending")
    term(memory, project, value="Global", scope="global", scope_id="")
    term(memory, project, value="Aster")
    term(memory, project, value="Birch", scene="interview")
    term(memory, project, value="Cedar", scope="meeting", scope_id="current", scene="interview")
    assert {row["quote"] for row in library.search("Alpha pending", project_id=project["id"])} == {"Aster pending"}
    assert {row["quote"] for row in library.search("Alpha pending", project_id=project["id"], memory_scene="interview")} == {"Birch pending"}
    assert [row["quote"] for row in retrieve_meeting_knowledge(library, "current", "Alpha", memory_scene="interview")] == ["Cedar pending"]
    assert [row["quote"] for row in retrieve_meeting_knowledge(library, "next", "Alpha", memory_scene="interview")] == ["Birch pending"]
    registry = PiEvidenceRegistry(persistence, meeting_id="current", knowledge_repository=library, memory_scene="interview")
    assert registry.search_documents("Alpha")[0]["quote"] == "Cedar pending"
    prepared = PreparedBriefSources(library)
    _, payload = prepared.capture(project["id"], goal="Alpha pending", scene="interview",
                                  expected_project_revision=library.get_project(project["id"])["revision"])
    assert [ref["quote"] for ref in payload["document_evidence"]] == ["Birch pending"]


@pytest.mark.parametrize("restricted", ["documents", "folder"])
def test_source_backed_term_requires_selected_current_material(local, restricted):
    library, _, memory, project = local
    outside = library.create_folder(project["id"], "Outside")
    parent = library.create_folder(project["id"], "Selected")
    child = library.create_folder(project["id"], "Child", parent_id=parent["id"])
    source, version = document(library, project, "Explicit correction Alpha means Aster.", folder_id=outside["id"])
    target, _ = document(library, project, "Aster pending", folder_id=child["id"])
    ref = library.search("Explicit correction", project_id=project["id"])[0]
    term(memory, project, source_refs=[ref])
    selection = {"document_ids": [target["id"]]} if restricted == "documents" else {"folder_id": parent["id"]}
    library.bind_meeting_context("current", project["id"], **selection)
    assert library.search("Alpha pending", project_id=project["id"], **selection) == []
    # A common word such as pending can still recall a candidate through the
    # pre-existing OR fallback; use the spelling alone to verify its exclusion.
    assert retrieve_meeting_knowledge(library, "current", "Alpha") == []
    assert PreparedBriefSources(library).documents({"project_id": project["id"], "folder_id": selection.get("folder_id"),
                                                   "document_ids": selection.get("document_ids")}, "Alpha") == []
    library.bind_meeting_context("current", project["id"])
    assert retrieve_meeting_knowledge(library, "current", "Alpha pending")[0]["document_id"] == target["id"]
    newer = library.add_version(source["id"], "The old correction is withdrawn.")
    library.confirm_version(source["id"], newer["id"], expected_revision=library.get_document(source["id"])["revision"])
    assert newer["id"] != version["id"]
    assert library.search("Alpha pending", project_id=project["id"]) == []
    assert retrieve_meeting_knowledge(library, "current", "Alpha") == []


def test_ambiguous_spelling_and_ascii_substring_stay_literal(local):
    library, _, memory, project = local
    term(memory, project, value="Aster", aliases=["Shared"], scope="global", scope_id="", memory_key="global-product")
    term(memory, project, value="Birch", aliases=["Shared"], memory_key="project-product")
    term(memory, project, value="Cedar", aliases=["Alpha"], memory_key="third-product")
    for name in ("Aster", "Birch", "Shared", "Cedar", "AlphaBeta"):
        document(library, project, name + " pending")
    assert [row["quote"] for row in library.search("Shared pending", project_id=project["id"])] == ["Shared pending"]
    assert [row["quote"] for row in library.search("Aster pending", project_id=project["id"])] == ["Aster pending"]
    assert [row["quote"] for row in library.search("AlphaBeta pending", project_id=project["id"])] == ["AlphaBeta pending"]


def test_withdrawal_while_expanding_discards_old_mapping(local, monkeypatch):
    library, _, memory, project = local
    document(library, project, "Aster pending")
    active = term(memory, project)
    from meeting_copilot_web_mvp import knowledge_repository as module
    original = module.expanded_fts_expression

    def withdrawing(*args, **kwargs):
        result = original(*args, **kwargs)
        memory.update(active["id"], expected_revision=active["revision"], status="disabled")
        return result

    monkeypatch.setattr(module, "expanded_fts_expression", withdrawing)
    assert library.search("Alpha pending", project_id=project["id"]) == []


def test_expansion_is_bounded_and_keeps_remaining_query_condition():
    terms = [{"canonical": f"Product{i}", "aliases": [f"Alias{i}", *["长" * 50 + str(i) + str(j) for j in range(7)]]}
             for i in range(40)]
    expression = expanded_fts_expression("Alias0 Alias1 Alias2 Alias3 Alias4 Alias5 critical42", terms,
                                         tokenize=lambda text: _tokens(text, query=True), match_mode="all")
    assert expression.count('"') // 2 <= MAX_EXPANDED_TOKENS
    assert '"critical42"' in expression and '"product0"' in expression
    assert '"product6"' not in expression
    assert ') AND ' in expression


def test_same_canonical_across_scopes_does_not_grow_one_family_past_nine_spellings():
    terms = [{"canonical": "Aster", "aliases": [f"Alpha{i}" for i in range(8)]},
             {"canonical": "Aster", "aliases": [f"Beta{i}" for i in range(8)]}]
    expression = expanded_fts_expression("Beta7", terms, tokenize=lambda text: _tokens(text, query=True), match_mode="all")
    assert '"beta7"' in expression and '"aster"' in expression
    assert expression.count('"') // 2 <= 9


def test_public_search_route_uses_saved_scene_and_preserves_empty_scope(local, tmp_path):
    library, _, memory, project = local
    general, _ = document(library, project, "Aster pending")
    interview, _ = document(library, project, "Birch pending")
    term(memory, project)
    term(memory, project, value="Birch", scene="interview")
    app = FastAPI()
    app.state.meeting_preparation_store = SimpleNamespace(get=lambda meeting: SimpleNamespace(preset_id="interview"))
    mount_knowledge_api(app, library, tmp_path)
    client = TestClient(app)
    meeting = client.post("/v2/knowledge/search", json={"meeting_id": "current", "query": "Alpha pending"})
    assert meeting.status_code == 200 and [row["document_id"] for row in meeting.json()["items"]] == [interview["id"]]
    project_search = client.post("/v2/knowledge/search", json={"project_id": project["id"], "query": "Alpha pending"})
    assert [row["document_id"] for row in project_search.json()["items"]] == [general["id"]]
    library.bind_meeting_context("current", project["id"], document_ids=[])
    assert client.post("/v2/knowledge/search", json={"meeting_id": "current", "query": "Alpha pending"}).json()["items"] == []


def test_alias_retrieval_keeps_six_source_3600_character_budget(local):
    library, _, memory, project = local
    term(memory, project)
    for index in range(9):
        document(library, project, f"Aster pending item{index}. " + "资料文字" * 600)
    evidence = retrieve_meeting_knowledge(library, "current", "Alpha pending")
    assert len(evidence) <= 6 and sum(len(ref["quote"]) for ref in evidence) <= 3600
    assert all(ref["quote"].startswith("Aster pending") and ref["content_hash"] for ref in evidence)


def test_real_app_project_ask_and_pi_registry_use_saved_scene_without_network(tmp_path, monkeypatch):
    # app.py has a module-level application too; isolate before importing it.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "module-runtime"))
    from meeting_copilot_web_mvp import llm_service
    from meeting_copilot_web_mvp.app import create_app
    from meeting_copilot_web_mvp.streaming_llm_provider import OpenAICompatibleStreamingProvider, TransportMode

    app = create_app(data_dir=tmp_path / "actual-app")
    library = app.state.knowledge_repository
    project = library.create_project("Synthetic")
    app.state.v2_persistence.create_meeting(meeting_id="meeting", title="Synthetic", now_ms=1)
    library.bind_meeting_context("meeting", project["id"])
    general, _ = document(library, project, "Aster pending")
    selected, _ = document(library, project, "Birch pending")
    term(UserMemory(library), project)
    term(UserMemory(library), project, value="Birch", scene="interview")
    app.state.meeting_preparation_store.save("meeting", preset_id="interview", updated_at_ms=2)
    factory = app.state.pi_coach_runtime._delegate._evidence_registry_factory
    assert [ref["document_id"] for ref in factory("meeting").search_documents("Alpha pending")] == [selected["id"]]
    config = llm_service.LlmConfig(base_url="https://controlled.invalid/v1", api_key="controlled-test-only", model="fake")
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda cls: config))
    calls = []

    async def fake_complete(self, messages, **kwargs):
        calls.append(messages)
        return SimpleNamespace(content="Birch pending", usage=None, transport_mode=TransportMode.STREAMING)

    monkeypatch.setattr(OpenAICompatibleStreamingProvider, "complete", fake_complete)
    with TestClient(app) as client:
        response = client.post("/v2/meetings/meeting/ask/stream", json={"scope": "project", "question": "Alpha pending"})
        assert response.status_code == 200
        answer = client.get("/v2/meetings/meeting/ask/threads").json()["threads"][0]["messages"][-1]
        assert answer["status"] == "completed" and answer["evidence_status"] == "valid"
        assert {ref["document_id"] for ref in answer["evidence"]} == {selected["id"]}
        assert general["id"] not in {ref["document_id"] for ref in answer["evidence"]}
    assert len(calls) == 1 and "Birch pending" in calls[0][-1]["content"]
