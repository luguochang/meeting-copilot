"""Read-only recording status derived from durable capture rows."""
from __future__ import annotations

from typing import Any, Mapping


def recording_attempt_failed(recording: Mapping[str, Any]) -> bool:
    if recording.get("status") == "failed":
        return True
    # A terminated setup with no samples has nothing for an exporter to assemble.
    # Missing counts are unknown, rather than assumed to be zero.
    return (recording.get("status") == "interrupted"
            and recording.get("chunk_count") == 0 and recording.get("sample_count") == 0)


def recording_needs_assembly(recording: Mapping[str, Any]) -> bool:
    return (recording.get("status") in {"sealed", "exporting", "interrupted"}
            and not recording_attempt_failed(recording))
