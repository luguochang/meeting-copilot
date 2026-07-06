# PCWEB-041 Live ASR Audit Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist local Live ASR JSON event audit records when the Web MVP uses a local data directory, and delete those records through the existing session deletion endpoint.

**Architecture:** Add a narrow ASR live audit repository parallel to the existing session repository. `create_app(data_dir=...)` uses a JSON repository under `data_dir/live_asr_sessions`, while in-memory mode keeps an in-process repository. The Live ASR endpoints read from this repository and continue to return the same JSON/SSE envelope.

**Tech Stack:** Python 3, FastAPI, pytest, local JSON files.

---

### Task 1: Persist Live ASR Audit Records Across App Instances

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Create: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_repository.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`

- [ ] **Step 1: Write the failing test**

Add this test to `test_app.py`:

```python
def test_asr_live_session_persists_json_events_across_app_instances(tmp_path):
    first_client = TestClient(create_app(data_dir=tmp_path))
    payload = _asr_live_payload(session_id="persisted_asr_live_review")

    create_response = first_client.post("/live/asr/mock/sessions", json=payload)

    assert create_response.status_code == 201
    second_client = TestClient(create_app(data_dir=tmp_path))
    json_response = second_client.get("/live/asr/sessions/persisted_asr_live_review/events")
    sse_response = second_client.get("/live/asr/sessions/persisted_asr_live_review/events.sse")

    assert json_response.status_code == 200
    events = json_response.json()["events"]
    assert events == create_response.json()["live_events"]
    assert json_response.json()["source"] == "live_asr_stream"
    assert json_response.json()["trace_kind"] == "live_event"
    assert sse_response.status_code == 200
    assert "event: state_event" in sse_response.text
    assert "谁负责回滚？" in sse_response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_session_persists_json_events_across_app_instances -q
```

Expected: fail with 404 because the second app instance cannot see the in-memory ASR live session.

- [ ] **Step 3: Implement repository**

Create `asr_live_repository.py` with:

```python
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from meeting_copilot_web_mvp.repository import SESSION_ID_PATTERN


class InMemoryAsrLiveSessionRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        session_id = str(record["session_id"])
        self._records[session_id] = deepcopy(record)
        return deepcopy(record)

    def get(self, session_id: str) -> dict[str, Any]:
        try:
            return deepcopy(self._records[session_id])
        except KeyError as exc:
            raise KeyError(f"ASR live session not found: {session_id}") from exc

    def delete(self, session_id: str) -> bool:
        return self._records.pop(session_id, None) is not None


class JsonFileAsrLiveSessionRepository:
    def __init__(self, data_dir: str | Path) -> None:
        self._records_dir = Path(data_dir) / "live_asr_sessions"
        self._records_dir.mkdir(parents=True, exist_ok=True)

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        session_id = str(record["session_id"])
        path = self._record_path(session_id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
        return deepcopy(record)

    def get(self, session_id: str) -> dict[str, Any]:
        path = self._record_path(session_id)
        if not path.exists():
            raise KeyError(f"ASR live session not found: {session_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, session_id: str) -> bool:
        path = self._record_path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _record_path(self, session_id: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"unsafe session_id: {session_id}")
        return self._records_dir / f"{session_id}.json"
```

- [ ] **Step 4: Integrate with app**

In `app.py`, instantiate `JsonFileAsrLiveSessionRepository(data_dir)` when `data_dir` is set; otherwise use `InMemoryAsrLiveSessionRepository`. Replace `asr_live_sessions[...]` reads and writes with repository calls.

- [ ] **Step 5: Run focused test to verify it passes**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py::test_asr_live_session_persists_json_events_across_app_instances -q
```

Expected: pass.

### Task 2: Delete and Safety Semantics

**Files:**
- Modify: `code/web_mvp/backend/tests/test_app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Modify: `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_repository.py`

- [ ] **Step 1: Add deletion test**

Add a test that creates a Live ASR session with `data_dir`, calls `DELETE /sessions/{id}`, and then verifies `/live/asr/sessions/{id}/events` returns 404.

- [ ] **Step 2: Add unsafe id test**

Add a test that posts a Live ASR session with `session_id="../bad"` using `create_app(data_dir=tmp_path)` and expects HTTP 422 containing `unsafe session_id`.

- [ ] **Step 3: Run both tests to verify failure**

Run the two tests by name and confirm the expected failures before implementation.

- [ ] **Step 4: Implement delete and ValueError mapping**

Call `asr_live_repo.delete(session_id)` inside the existing DELETE endpoint. Convert repository `ValueError` in Live ASR create/get/SSE/delete paths into HTTP 422.

- [ ] **Step 5: Run backend tests**

Run:

```bash
cd code/web_mvp/backend
python3 -m pytest tests/test_app.py tests/test_live_events.py -q
```

Expected: pass.

### Task 3: Docs and Quality Gates

**Files:**
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/pc-local-web-mvp-acceptance.md`
- Modify: `docs/end-to-end-design-checklist.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `code/web_mvp/README.md`

- [ ] **Step 1: Update docs**

Add `PCWEB-041`, `AC-PCWEB-034`, and `DEC-041`. Wording must say this persists JSON audit records only and does not persist raw audio or call remote services.

- [ ] **Step 2: Run quality gates**

Run:

```bash
cd /Users/chase/Documents/面试/meeting-copilot
python3 tools/run_quality_gate.py --profile pc-web
python3 tools/run_quality_gate.py --profile all-local --no-browser
```

Expected: both pass.

- [ ] **Step 3: Cleanup and safety checks**

Run cache cleanup, port checks for 8767/9223, and the sensitive scan excluding `configs/local/**`.

