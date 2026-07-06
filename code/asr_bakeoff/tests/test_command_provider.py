import json
import sys
from pathlib import Path

import pytest

from asr_bakeoff.providers.command import CommandProvider, ProviderExecutionError


def test_command_provider_invokes_external_command_and_parses_json(tmp_path: Path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake wav")
    provider = CommandProvider(
        name="fake-asr",
        command=[
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "print(json.dumps({"
                "'text': '接口新增 trace_id 字段，需要兼容调用方。', "
                "'latency_ms': 321, "
                "'entities': ['trace_id', '兼容', '调用方'], "
                "'segments': [{'start_ms': 0, 'end_ms': 3000, 'text': '接口新增 trace_id 字段，需要兼容调用方。'}], "
                "'raw': {'argv': sys.argv[1:]}"
                "}, ensure_ascii=False))"
            ),
            "{sample_id}",
            "{audio_path}",
        ],
    )

    result = provider.transcribe("S01", audio)

    assert provider.name == "fake-asr"
    assert result.text == "接口新增 trace_id 字段，需要兼容调用方。"
    assert result.latency_ms == 321
    assert result.entities == ["trace_id", "兼容", "调用方"]
    assert result.segments == [
        {
            "start_ms": 0,
            "end_ms": 3000,
            "text": "接口新增 trace_id 字段，需要兼容调用方。",
        }
    ]
    assert result.raw == {"argv": ["S01", str(audio)]}


def test_command_provider_rejects_invalid_json(tmp_path: Path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake wav")
    provider = CommandProvider(
        name="bad-json",
        command=[sys.executable, "-c", "print('not json')"],
    )

    with pytest.raises(ProviderExecutionError, match="invalid JSON"):
        provider.transcribe("S01", audio)


def test_command_provider_requires_text_field(tmp_path: Path):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"fake wav")
    provider = CommandProvider(
        name="missing-text",
        command=[sys.executable, "-c", "import json; print(json.dumps({'latency_ms': 10}))"],
    )

    with pytest.raises(ProviderExecutionError, match="missing required field: text"):
        provider.transcribe("S01", audio)
