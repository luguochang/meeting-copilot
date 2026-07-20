from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import packaged_ai_mainline_smoke as smoke  # noqa: E402


def test_local_provider_supports_streaming_and_non_streaming_openai_shapes():
    provider = smoke.LocalOpenAIProvider()
    provider.start()
    try:
        assert provider.base_url.startswith("http://127.0.0.1:")
        assert smoke._completion_for("transcript_correction", "原文") == "原文"
        minutes = json.loads(smoke._completion_for("minutes", "缓存穿透需要验证"))
        assert minutes["risks"]
        assert smoke._completion_for("approach", "原文") == "[]"
        intelligence = json.loads(smoke._completion_for(
            "realtime_intelligence",
            json.dumps({
                "new_paragraphs": [{
                    "id": "paragraph-1",
                    "revision": 1,
                    "text": "先灰度百分之五。",
                }],
            }, ensure_ascii=False),
        ))
        assert intelligence["follow_up"]["evidence_segment_ids"] == ["paragraph-1"]
        assert intelligence["follow_up"]["evidence_quote"] == "先灰度百分之五。"
    finally:
        provider.stop()


def test_packaged_ai_evidence_never_contains_the_fake_secret_source():
    source = Path(smoke.__file__).read_text(encoding="utf-8")
    evidence_block = source.split('evidence = {', 1)[1]
    assert '"api_key"' not in evidence_block
    assert 'FAKE_API_KEY' not in evidence_block


def test_packaged_ai_mainline_uses_the_recording_notice_contract():
    source = Path(smoke.__file__).read_text(encoding="utf-8")

    assert 'responses["preparation"] = preparation_status' in source
    assert 'meeting_preparation_payload()' in source


def test_packaged_ai_mainline_accepts_only_the_llm_first_follow_up_contract():
    source = Path(smoke.__file__).read_text(encoding="utf-8")

    assert 'snapshot.get("follow_up")' in source
    assert '"meeting.intelligence.applied" in event_types' in source
    assert '"suggestion.committed" in event_types' not in source
