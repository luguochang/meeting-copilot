import json
import subprocess
import sys
from pathlib import Path


def test_cli_runs_command_provider(tmp_path: Path):
    audio = tmp_path / "S01.wav"
    audio.write_bytes(b"fake wav")
    reference = tmp_path / "S01.txt"
    reference.write_text("接口新增 trace_id 字段。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "result.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {
                        "id": "S01",
                        "audio_path": str(audio),
                        "reference_path": str(reference),
                        "language": "zh-CN",
                        "scenario": "api_review",
                        "duration_seconds": 10.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "asr_bakeoff.cli",
            "--manifest",
            str(manifest),
            "--provider",
            "command",
            "--provider-name",
            "fake-command-asr",
            "--command",
            (
                f"{sys.executable} -c \"import json; "
                "print(json.dumps({'text': '接口新增 trace_id 字段。'}, ensure_ascii=False))\""
            ),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["provider"] == "fake-command-asr"
    assert report["summary"]["avg_cer"] == 0
