#!/usr/bin/env python3
"""Run an explicit scripted-ASR backend for unattended V2 browser E2E.

This launcher is intentionally outside the production package. It exercises
the real WebSocket, recording, SQLite, durable AI jobs, shared React UI and
review/export APIs while keeping the ASR provider visibly marked as mock.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp import asr_stream


class ScriptedChineseMeetingRecognizer:
    provider = "scripted_chinese_e2e_asr"
    provider_mode = "mock"
    is_mock = True
    fallback_used = False
    degradation_reasons = ["e2e_scripted_asr"]

    _segments = (
        "我们灰度百分之五验证 cheout outservice，异常时立即回滚。",
        "如果 P99 延迟超过九百毫秒，需要确认回滚负责人和监控 owner。",
    )
    _segment_windows_ms = ((0, 600), (6_000, 6_600))

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._seq = 0
        self._emitted_finals: set[int] = set()

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        self._seq += 1
        if self._seq not in {1, 2, 3, 4}:
            return []
        segment_index = 0 if self._seq <= 2 else 1
        segment_id = f"scripted_segment_{segment_index + 1}"
        start_ms, final_end_ms = self._segment_windows_ms[segment_index]
        chunk_in_segment = ((self._seq - 1) % 2) + 1
        end_ms = min(final_end_ms, start_ms + chunk_in_segment * 300)

        final_at = 2 if segment_index == 0 else 4
        if self._seq == final_at and segment_index not in self._emitted_finals:
            self._emitted_finals.add(segment_index)
            return [{
                "event_type": "final",
                "segment_id": segment_id,
                "text": self._segments[segment_index],
                "start_ms": start_ms,
                "end_ms": end_ms,
                "confidence": 0.96,
            }]
        return [{
            "event_type": "partial",
            "segment_id": segment_id,
            "text": self._segments[segment_index][:-1],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "confidence": 0.91,
        }]

    def finalize(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for index, text in enumerate(self._segments):
            if index in self._emitted_finals:
                continue
            start_ms, end_ms = self._segment_windows_ms[index]
            events.append({
                "event_type": "final",
                "segment_id": f"scripted_segment_{index + 1}",
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "confidence": 0.96,
            })
        return events

    def abort(self) -> None:
        return None


def main() -> None:
    data_dir = Path(os.environ["MEETING_COPILOT_DATA_DIR"])
    host = os.environ.get("MEETING_COPILOT_E2E_HOST", "127.0.0.1")
    port = int(os.environ.get("MEETING_COPILOT_E2E_PORT", "8782"))
    # This launcher is strictly non-acceptance. Keep the provider metadata
    # truthful even when callers only provide a local gateway URL.
    os.environ["LLM_GATEWAY_IS_MOCK"] = "true"
    asr_stream.get_recognizer = lambda session_id: ScriptedChineseMeetingRecognizer(session_id)
    # Production correctly rejects mock providers from creating formal
    # derivations. This E2E-only process opts into non-acceptance execution so
    # the full durable/UI flow can be tested while provider metadata remains
    # is_mock=true and cannot be mistaken for release evidence.
    app_module._ensure_llm_provider_allowed_for_derivation = lambda *_args, **_kwargs: None
    app_module._ensure_enabled_llm_allowed = lambda *_args, **_kwargs: None
    app = app_module.create_app(
        data_dir=data_dir,
        allow_fake_asr_fallback=True,
        semantic_projection_mode="llm_first",
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
