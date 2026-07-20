#!/usr/bin/env python3
"""Synthetic NEXT-014 ASR fixture.

This fixture keeps the ASR checkpoint clock deterministic while the browser
streams ordinary float32 PCM over the real WebSocket. It is deliberately a
non-acceptance provider: it exercises durable paragraph projection and UI
timing, but it is not evidence for natural multi-speaker recognition quality.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import uvicorn

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp import asr_stream


CHECKPOINTS: tuple[dict[str, Any], ...] = (
    {
        "id": "next014-history-1",
        "text": "会前先确认本次发布范围、录音保留策略和回滚窗口，所有参与人都从同一份检查表开始。",
        "start_ms": 0,
        "end_ms": 1_000,
        "final_at_chunk": 5,
    },
    {
        "id": "next014-history-2",
        "text": "接着核对灰度开关、监控面板以及值班联系方式，发布前不再临时修改观察口径。",
        "start_ms": 3_000,
        "end_ms": 4_000,
        "final_at_chunk": 10,
    },
    {
        "id": "next014-history-3",
        "text": "最后确认异常升级路径和录音定位入口，后续每个结论都保留对应的时间证据。",
        "start_ms": 6_000,
        "end_ms": 7_000,
        "final_at_chunk": 15,
    },
    {
        "id": "next014-continuous-00",
        "text": "连续发言先从发布范围和灰度策略开始，第一阶段只覆盖百分之五的用户并保持回滚负责人在线。",
        "start_ms": 9_000,
        "end_ms": 24_000,
        # Final just before the internal 15s VAD ceiling. The checkpoint still
        # spans the full 15s semantic window and is joined to its neighbors.
        "final_at_chunk": 45,
        "continuous": True,
    },
    {
        "id": "next014-continuous-15",
        "text": "随后持续观察错误率、P99 延迟和关键接口成功率，任何单项指标越线都先暂停扩大范围。",
        "start_ms": 24_000,
        "end_ms": 39_000,
        "final_at_chunk": 90,
        "continuous": True,
    },
    {
        "id": "next014-continuous-30",
        "text": "在观察窗口结束前把结果写回发布记录，如果指标恢复稳定再进入下一阶段，否则按预案执行回滚。",
        "start_ms": 39_000,
        "end_ms": 54_000,
        "final_at_chunk": 135,
        "continuous": True,
    },
    {
        "id": "next014-history-tail",
        "text": "新增结论继续追加到全文末尾，并保留与前面连续发言相同的时间顺序。",
        "start_ms": 56_000,
        "end_ms": 60_000,
        "final_at_chunk": 170,
    },
)


class Next014ContinuousRecognizer:
    provider = "next014_scripted_continuous_asr"
    provider_mode = "mock"
    is_mock = True
    fallback_used = False
    degradation_reasons = ["e2e_next014_synthetic_fixture"]

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._seq = 0
        self._emitted_finals: set[str] = set()

    def _current_checkpoint(self) -> dict[str, Any] | None:
        for checkpoint in CHECKPOINTS:
            if self._seq <= int(checkpoint["final_at_chunk"]):
                return checkpoint
        return None

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        del pcm
        self._seq += 1
        checkpoint = self._current_checkpoint()
        if checkpoint is None:
            return []
        final_at_chunk = int(checkpoint["final_at_chunk"])
        if self._seq != final_at_chunk or str(checkpoint["id"]) in self._emitted_finals:
            # Do not leave a VAD candidate open between controlled checkpoints.
            # The pair below still exercises the browser's partial/final path.
            return []
        partial: dict[str, Any] = {
            "event_type": "partial",
            "segment_id": str(checkpoint["id"]),
            "text": str(checkpoint["text"]),
            "start_ms": int(checkpoint["start_ms"]),
            "end_ms": int(checkpoint["end_ms"]) - 1,
            "confidence": 0.96,
        }
        self._emitted_finals.add(str(checkpoint["id"]))
        return [partial, {
            **partial,
            "event_type": "final",
            "end_ms": int(checkpoint["end_ms"]),
        }]

    def finalize(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for checkpoint in CHECKPOINTS:
            checkpoint_id = str(checkpoint["id"])
            if checkpoint_id in self._emitted_finals:
                continue
            events.append({
                "event_type": "final",
                "segment_id": checkpoint_id,
                "text": str(checkpoint["text"]),
                "start_ms": int(checkpoint["start_ms"]),
                "end_ms": int(checkpoint["end_ms"]),
                "confidence": 0.96,
            })
        return events

    def abort(self) -> None:
        return None


def main() -> None:
    data_dir = Path(os.environ["MEETING_COPILOT_DATA_DIR"])
    host = os.environ.get("MEETING_COPILOT_E2E_HOST", "127.0.0.1")
    port = int(os.environ.get("MEETING_COPILOT_E2E_PORT", "8794"))
    asr_stream.get_recognizer = lambda session_id: Next014ContinuousRecognizer(session_id)
    # The product preflight asks whether a realtime ASR provider is available.
    # This fixture advertises only its explicitly synthetic provider so the
    # existing preflight can be exercised without changing production code.
    app_module._realtime_asr_providers = lambda: ["next014_scripted_continuous_asr"]
    app = app_module.create_app(
        data_dir=data_dir,
        allow_fake_asr_fallback=True,
        semantic_projection_mode="legacy",
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
