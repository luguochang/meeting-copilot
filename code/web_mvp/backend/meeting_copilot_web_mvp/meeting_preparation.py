from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Iterable, Mapping

from .storage_governance import (
    ensure_private_directory,
    harden_private_file,
)


_MEETING_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_HOTWORDS = 50
MAX_HOTWORD_LENGTH = 64
MAX_HOTWORD_CHARACTERS = 2_000
SUPPORTED_INPUT_SOURCES = frozenset({"microphone", "system_audio", "dual_track"})
SUPPORTED_PRESETS = frozenset({"general", "decision", "project", "interview", "brainstorm"})
SUPPORTED_OUTPUT_FORMATS = frozenset({"standard", "decision_log", "action_plan", "brief"})
SUPPORTED_SUGGESTION_POLICIES = frozenset({"off", "low_frequency", "standard"})
MAX_FOCUS_POINTS = 12


@dataclass(frozen=True)
class MeetingPreparation:
    meeting_id: str
    hotwords: tuple[str, ...]
    input_source: str
    input_device_id: str | None
    input_device_name: str | None
    notice_acknowledged: bool
    preset_id: str
    meeting_goal: str | None
    participant_role: str | None
    focus_points: tuple[str, ...]
    output_format: str
    proactive_suggestion_policy: str
    version: int
    updated_at_ms: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["hotwords"] = list(self.hotwords)
        payload["focus_points"] = list(self.focus_points)
        return payload


class MeetingPreparationStore:
    """Persist non-sensitive, meeting-scoped capture preferences atomically."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        ensure_private_directory(self._root)
        self._lock = RLock()

    def get(self, meeting_id: str) -> MeetingPreparation | None:
        normalized_id = normalize_meeting_id(meeting_id)
        path = self._path(normalized_id)
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return None
        return preparation_from_mapping(normalized_id, payload)

    def save(
        self,
        meeting_id: str,
        *,
        hotwords: Iterable[object] = (),
        input_source: str = "microphone",
        input_device_id: str | None = None,
        input_device_name: str | None = None,
        notice_acknowledged: bool = False,
        preset_id: str = "general",
        meeting_goal: str | None = None,
        participant_role: str | None = None,
        focus_points: Iterable[object] = (),
        output_format: str = "standard",
        proactive_suggestion_policy: str = "low_frequency",
        updated_at_ms: int,
    ) -> MeetingPreparation:
        normalized_id = normalize_meeting_id(meeting_id)
        path = self._path(normalized_id)
        temporary_path = path.with_suffix(f".json.tmp-{os.getpid()}")
        with self._lock:
            existing_payload: dict[str, Any] = {}
            if path.is_file():
                try:
                    candidate = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(candidate, dict):
                        existing_payload = candidate
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    existing_payload = {}
            preparation = MeetingPreparation(
                meeting_id=normalized_id,
                hotwords=normalize_hotwords(hotwords),
                input_source=normalize_input_source(input_source),
                input_device_id=normalize_optional_label(input_device_id, "input_device_id"),
                input_device_name=normalize_optional_label(input_device_name, "input_device_name"),
                notice_acknowledged=bool(notice_acknowledged),
                preset_id=normalize_choice(preset_id, "preset_id", SUPPORTED_PRESETS, "general"),
                meeting_goal=normalize_optional_text(meeting_goal, "meeting_goal", maximum=2_000),
                participant_role=normalize_optional_text(participant_role, "participant_role", maximum=200),
                focus_points=normalize_focus_points(focus_points),
                output_format=normalize_choice(output_format, "output_format", SUPPORTED_OUTPUT_FORMATS, "standard"),
                proactive_suggestion_policy=normalize_choice(
                    proactive_suggestion_policy,
                    "proactive_suggestion_policy",
                    SUPPORTED_SUGGESTION_POLICIES,
                    "low_frequency",
                ),
                version=max(0, int(existing_payload.get("version") or 0)) + 1,
                updated_at_ms=max(0, int(updated_at_ms)),
            )
            version_history = existing_payload.get("versions")
            if not isinstance(version_history, list):
                version_history = []
            current_payload = preparation.to_dict()
            serialized = json.dumps(
                {
                    **current_payload,
                    "versions": [*version_history[-99:], current_payload],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            temporary_path.write_text(serialized, encoding="utf-8")
            harden_private_file(temporary_path)
            os.replace(temporary_path, path)
            harden_private_file(path)
        return preparation

    def list_versions(self, meeting_id: str) -> tuple[MeetingPreparation, ...]:
        normalized_id = normalize_meeting_id(meeting_id)
        path = self._path(normalized_id)
        with self._lock:
            if not path.is_file():
                return ()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return ()
        raw_versions = payload.get("versions") if isinstance(payload, Mapping) else None
        if not isinstance(raw_versions, list):
            raw_versions = [payload]
        versions: list[MeetingPreparation] = []
        for raw_version in raw_versions:
            if not isinstance(raw_version, Mapping):
                continue
            try:
                versions.append(preparation_from_mapping(normalized_id, raw_version))
            except (TypeError, ValueError):
                continue
        return tuple(versions)

    def delete(self, meeting_id: str) -> bool:
        normalized_id = normalize_meeting_id(meeting_id)
        path = self._path(normalized_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
        return True

    def _path(self, meeting_id: str) -> Path:
        path = (self._root / f"{meeting_id}.json").resolve()
        if path.parent != self._root:
            raise ValueError("meeting preparation path escaped the managed root")
        return path


def normalize_meeting_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not _MEETING_ID_RE.fullmatch(normalized):
        raise ValueError("meeting_id contains unsupported characters")
    return normalized


def normalize_hotwords(values: Iterable[object]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("hotwords must be a sequence")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        if _CONTROL_CHARACTER_RE.search(value):
            raise ValueError("hotword contains control characters")
        if len(value) > MAX_HOTWORD_LENGTH:
            raise ValueError(f"hotword must not exceed {MAX_HOTWORD_LENGTH} characters")
        dedupe_key = value.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(value)
    if len(normalized) > MAX_HOTWORDS:
        raise ValueError(f"hotwords must not contain more than {MAX_HOTWORDS} entries")
    if sum(len(value) for value in normalized) > MAX_HOTWORD_CHARACTERS:
        raise ValueError(f"hotwords must not exceed {MAX_HOTWORD_CHARACTERS} characters in total")
    return tuple(normalized)


def normalize_input_source(value: object) -> str:
    normalized = str(value or "microphone").strip().lower()
    if normalized not in SUPPORTED_INPUT_SOURCES:
        raise ValueError("input_source must be microphone, system_audio, or dual_track")
    return normalized


def normalize_optional_label(value: object, field: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if _CONTROL_CHARACTER_RE.search(normalized):
        raise ValueError(f"{field} contains control characters")
    if len(normalized) > 256:
        raise ValueError(f"{field} must not exceed 256 characters")
    return normalized


def normalize_optional_text(value: object, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    if _CONTROL_CHARACTER_RE.search(normalized):
        raise ValueError(f"{field} contains control characters")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters")
    return normalized


def normalize_choice(value: object, field: str, supported: frozenset[str], default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in supported:
        raise ValueError(f"{field} is unsupported")
    return normalized


def normalize_focus_points(values: Iterable[object]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("focus_points must be a sequence")
    result: list[str] = []
    for value in values:
        normalized = normalize_optional_text(value, "focus_point", maximum=200)
        if normalized and normalized not in result:
            result.append(normalized)
    if len(result) > MAX_FOCUS_POINTS:
        raise ValueError(f"focus_points must not contain more than {MAX_FOCUS_POINTS} entries")
    return tuple(result)


def preparation_from_mapping(
    meeting_id: str,
    payload: Mapping[str, Any],
) -> MeetingPreparation:
    if not isinstance(payload, Mapping):
        raise ValueError("meeting preparation payload must be an object")
    return MeetingPreparation(
        meeting_id=normalize_meeting_id(meeting_id),
        hotwords=normalize_hotwords(payload.get("hotwords") or ()),
        input_source=normalize_input_source(payload.get("input_source") or "microphone"),
        input_device_id=normalize_optional_label(payload.get("input_device_id"), "input_device_id"),
        input_device_name=normalize_optional_label(payload.get("input_device_name"), "input_device_name"),
        notice_acknowledged=bool(payload.get("notice_acknowledged", False)),
        preset_id=normalize_choice(payload.get("preset_id"), "preset_id", SUPPORTED_PRESETS, "general"),
        meeting_goal=normalize_optional_text(payload.get("meeting_goal"), "meeting_goal", maximum=2_000),
        participant_role=normalize_optional_text(
            payload.get("participant_role"),
            "participant_role",
            maximum=200,
        ),
        focus_points=normalize_focus_points(payload.get("focus_points") or ()),
        output_format=normalize_choice(
            payload.get("output_format"),
            "output_format",
            SUPPORTED_OUTPUT_FORMATS,
            "standard",
        ),
        proactive_suggestion_policy=normalize_choice(
            payload.get("proactive_suggestion_policy"),
            "proactive_suggestion_policy",
            SUPPORTED_SUGGESTION_POLICIES,
            "low_frequency",
        ),
        version=max(0, int(payload.get("version") or 0)),
        updated_at_ms=max(0, int(payload.get("updated_at_ms") or 0)),
    )
