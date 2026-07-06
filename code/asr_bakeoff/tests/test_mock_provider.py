from asr_bakeoff.providers.mock import MockProvider


def test_mock_provider_accepts_structured_transcript_result(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake wav")
    provider = MockProvider(
        {
            "S01": {
                "text": "接口新增 trace_id 字段。",
                "latency_ms": 123,
                "entities": ["trace_id"],
                "raw": {"source": "fixture"},
            }
        }
    )

    result = provider.transcribe("S01", audio)

    assert result.text == "接口新增 trace_id 字段。"
    assert result.latency_ms == 123
    assert result.entities == ["trace_id"]
    assert result.raw == {"source": "fixture"}


def test_mock_provider_keeps_string_transcript_shortcut(tmp_path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake wav")
    provider = MockProvider({"S01": "接口新增 trace_id 字段。"})

    result = provider.transcribe("S01", audio)

    assert result.text == "接口新增 trace_id 字段。"
    assert result.entities == ["trace_id"]
