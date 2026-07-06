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
        if session_id in self._records:
            raise ValueError(f"ASR live session already exists: {session_id}")
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

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        session_id = str(record["session_id"])
        path = self._record_path(session_id)
        if path.exists():
            raise ValueError(f"ASR live session already exists: {session_id}")
        self._records_dir.mkdir(parents=True, exist_ok=True)
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
