"""Run local ASR against one public fixture, with no meeting database or LLM."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code/web_mvp/backend"))
sys.path.insert(0, str(ROOT / "tools"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-asr", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("write evaluation only under ignored artifacts")
    output.parent.mkdir(parents=True, exist_ok=True)
    from web_quality import DEFAULT_MODELS, quality_environment
    os.environ.update(quality_environment(DEFAULT_MODELS, output.parent / "unused-data"))
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from meeting_copilot_web_mvp.batch_transcribe import capability_status, transcribe_file_report
    import imageio_ffmpeg

    fixture = DEFAULT_MODELS / "camplus/examples/speaker1_a_cn_16k.wav"
    if not fixture.is_file():
        parser.error("the public CAM++ example WAV must already be available locally")
    mp3 = output.parent / "public-asr-fixture.mp3"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(fixture),
                    "-codec:a", "libmp3lame", "-b:a", "96k", str(mp3)],
                   check=True, capture_output=True)
    capability = capability_status()
    report = {"scope": "public local fixture; no app or user database; no LLM calls",
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "gateway_calls": 0, "capability_status": capability["status"],
              "formats": capability["formats"], "samples": []}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for sample in (fixture, mp3):
        started = time.perf_counter()
        try:
            result = transcribe_file_report(sample, timeout=150)
            text = str(result.get("text") or "")
            if not text:
                text = " ".join(str(segment.get("text") or "") for segment in result.get("segments") or [])
            record = {"format": sample.suffix, "status": "succeeded" if text.strip() else "empty",
                      "text": text, "text_chars": len(text),
                      "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        except Exception as exc:
            record = {"format": sample.suffix, "status": "failed", "error_class": type(exc).__name__,
                      "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        report["samples"].append(record)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(record, ensure_ascii=True), flush=True)
    if any(sample["status"] != "succeeded" for sample in report["samples"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
