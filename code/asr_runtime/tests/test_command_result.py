from scripts.command_result import ProviderTranscript, parse_provider_stdout


def test_parse_provider_stdout_reads_required_text_and_latency():
    result = parse_provider_stdout(
        '{"text":"你好","latency_ms":123,"raw":{"provider":"test"}}'
    )

    assert result == ProviderTranscript(
        text="你好",
        latency_ms=123,
        raw={"provider": "test"},
        segments=[],
    )


def test_parse_provider_stdout_rejects_missing_text():
    try:
        parse_provider_stdout('{"latency_ms":123}')
    except ValueError as exc:
        assert "missing text" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_parse_provider_stdout_preserves_segments():
    result = parse_provider_stdout(
        """
        {
          "text": "先灰度百分之十。",
          "latency_ms": 500,
          "raw": {"provider": "test"},
          "segments": [
            {"start_ms": 0, "end_ms": 2000, "text": "先灰度百分之十。"}
          ]
        }
        """
    )

    assert result.segments == [
        {"start_ms": 0, "end_ms": 2000, "text": "先灰度百分之十。"}
    ]
