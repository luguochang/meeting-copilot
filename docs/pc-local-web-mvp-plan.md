# PC Local Web MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local PC Web MVP vertical slice that exposes Meeting Copilot transcript/state/suggestion/report data through reusable core code and a local API.

**Architecture:** Keep `code/core` platform-neutral and reusable by later Mac/Windows clients. Keep `code/web_mvp/backend` as a thin FastAPI adapter with local in-memory/file-ready session storage. Reuse existing `asr_runtime` contracts and validation behavior rather than duplicating ASR/LLM logic.

**Tech Stack:** Python 3, pytest, FastAPI, Pydantic/httpx via FastAPI TestClient, existing `code/asr_runtime` scripts.

---

## File Structure

```text
code/
  core/
    pytest.ini
    meeting_copilot_core/
      __init__.py
      session_snapshot.py
    tests/
      test_session_snapshot.py

  web_mvp/
    backend/
      pytest.ini
      pyproject.toml
      meeting_copilot_web_mvp/
        __init__.py
        app.py
        repository.py
      tests/
        test_app.py
```

## Task 1: Core session snapshot

**Files:**

- Create: `code/core/meeting_copilot_core/session_snapshot.py`
- Create: `code/core/tests/test_session_snapshot.py`
- Create: `code/core/pytest.ini`

- [ ] Write failing tests for engineering and non-engineering snapshots.
- [ ] Run `cd code/core && pytest -q` and verify failure.
- [ ] Implement `build_session_snapshot`.
- [ ] Run `cd code/core && pytest -q` and verify pass.

## Task 2: Local Web MVP API

**Files:**

- Create: `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- Create: `code/web_mvp/backend/meeting_copilot_web_mvp/repository.py`
- Create: `code/web_mvp/backend/tests/test_app.py`
- Create: `code/web_mvp/backend/pytest.ini`
- Create: `code/web_mvp/backend/pyproject.toml`

- [ ] Write failing API tests for health, create session, read session, update card status, delete session.
- [ ] Run `cd code/web_mvp/backend && pytest -q` and verify failure.
- [ ] Implement repository and FastAPI adapter.
- [ ] Run `cd code/web_mvp/backend && pytest -q` and verify pass.

## Task 3: Documentation and traceability

**Files:**

- Modify: `docs/implementation-roadmap.md`
- Modify: `docs/requirements-traceability-matrix.md`
- Modify: `docs/decision-log.md`
- Modify: `docs/project-structure.md`

- [ ] Add PC-1 as the active implementation stage.
- [ ] Add PCWEB requirements to the traceability matrix.
- [ ] Record the decision to enter PC Local Web MVP before desktop shell.
- [ ] Run existing ASR/runtime tests and web/core tests.

## Verification

```bash
cd /Users/chase/Documents/面试/meeting-copilot/code/core
pytest -q

cd /Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend
pytest -q

cd /Users/chase/Documents/面试/meeting-copilot/code/asr_runtime
pytest -q

cd /Users/chase/Documents/面试/meeting-copilot/code/asr_bakeoff
python3 -m pytest tests -q
```

Security scan:

```bash
cd /Users/chase/Documents/面试
rg -n "sk-[A-Za-z0-9]{16,}" meeting-copilot || true
```

Private local filenames, recorder temp paths and user-specific path fragments must be scanned locally when relevant, but must not be written into public docs.
