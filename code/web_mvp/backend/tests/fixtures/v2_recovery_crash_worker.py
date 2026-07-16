from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys
import time

from meeting_copilot_web_mvp.audio_assets import SAMPLE_RATE_HZ, RealtimeWavAssetWriter
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


MEETING_ID = "phase2-process-recovery"


def main() -> None:
    data_dir = Path(sys.argv[1])
    ready_path = Path(sys.argv[2])
    persistence = V2Persistence(data_dir / "meeting_copilot.db")
    persistence.create_meeting(
        meeting_id=MEETING_ID,
        title="Phase 2 process recovery",
        now_ms=1_000,
    )
    persistence.begin_recording(
        meeting_id=MEETING_ID,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=SAMPLE_RATE_HZ,
        lease_owner="crash-capture-worker",
        lease_ms=500,
        now_ms=1_000,
    )

    def record(chunk: dict[str, object]) -> None:
        persistence.record_audio_chunk(
            meeting_id=MEETING_ID,
            track="microphone",
            epoch=0,
            chunk_seq=int(chunk["chunk_index"]),
            relative_path=str(chunk["relative_path"]),
            sha256=str(chunk["sha256"]),
            sample_rate_hz=int(chunk["sample_rate_hz"]),
            sample_count=int(chunk["sample_count"]),
            duration_ms=int(chunk["duration_ms"]),
            file_size_bytes=int(chunk["file_size_bytes"]),
            now_ms=1_100,
            lease_owner="crash-capture-worker",
            lease_ms=500,
        )

    writer = RealtimeWavAssetWriter(
        data_dir=data_dir,
        session_id=MEETING_ID,
        source_type="browser_live_mic",
        on_chunk_committed=record,
    )
    writer.write_float32_pcm(
        struct.pack("<f", 0.1) * (SAMPLE_RATE_HZ * 9)
    )
    committed = persistence.commit_final_and_enqueue(
        meeting_id=MEETING_ID,
        final_id="final-before-sigkill",
        segment_id="segment-before-sigkill",
        text="进程恢复后必须继续保存录音并完成后台任务。",
        normalized_text="进程恢复后必须继续保存录音并完成后台任务。",
        started_at_ms=1_050,
        ended_at_ms=1_150,
        evidence_hash="phase2-process-recovery-evidence",
        now_ms=1_200,
    )
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "job_ids": sorted(committed["job_ids"].values()),
            "committed_audio_ms": sum(
                chunk["duration_ms"]
                for chunk in persistence.list_audio_chunks(MEETING_ID)
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    temporary = ready_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as marker:
        marker.write(payload)
        marker.flush()
        os.fsync(marker.fileno())
    temporary.replace(ready_path)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
