from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).resolve().parents[4] / "data" / "web_mvp" / "fixtures"


def list_demo_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        fixture = _load_json(path)
        fixtures.append(_metadata(fixture))
    return fixtures


def load_demo_fixture(fixture_id: str) -> dict[str, Any]:
    if not _valid_fixture_id(fixture_id):
        raise KeyError(f"fixture not found: {fixture_id}")
    path = FIXTURE_DIR / f"{fixture_id}.json"
    if not path.exists():
        raise KeyError(f"fixture not found: {fixture_id}")
    return _load_json(path)


def session_payload_from_fixture(
    fixture_id: str,
    *,
    session_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = load_demo_fixture(fixture_id)
    session = deepcopy(fixture["session"])
    if session_id:
        session["session_id"] = session_id
    metadata = {
        "fixture_id": str(fixture["id"]),
        "source": str(fixture.get("source", "fixture")),
        "replay_mode": "demo_fixture",
        "expected_gap_rule_count": int(fixture.get("expected_gap_rule_count", 2)),
    }
    return session, metadata


def _metadata(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(fixture["id"]),
        "title": str(fixture.get("title", fixture["id"])),
        "source": str(fixture.get("source", "fixture")),
        "scenario_type": str(fixture.get("scenario_type", "unknown")),
        "is_engineering_meeting": _is_engineering_fixture(fixture),
        "expected_gap_rule_count": int(fixture.get("expected_gap_rule_count", 2)),
        "expected_gate_tags": list(fixture.get("expected_gate_tags", [])),
    }


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"fixture must be a JSON object: {path.name}")
    return value


def _valid_fixture_id(fixture_id: str) -> bool:
    return bool(fixture_id) and "/" not in fixture_id and "\\" not in fixture_id and ".." not in fixture_id


def _is_engineering_fixture(fixture: dict[str, Any]) -> bool:
    return bool(
        fixture
        .get("session", {})
        .get("analysis", {})
        .get("meeting_context", {})
        .get("is_engineering_meeting", False)
    )
