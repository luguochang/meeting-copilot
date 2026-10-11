"""One existing-audio MP3 import through the official local pipeline, without devices."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
import wave

try:
    from .long_audio_soak import durable_counts, safe_snapshot
    from .pi_stage0_production_replay import JsonHttpClient
    from .workbench_server import status_report
except ImportError:
    from long_audio_soak import durable_counts, safe_snapshot
    from pi_stage0_production_replay import JsonHttpClient
    from workbench_server import status_report

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts/realtime-participation-20261007"
EXPECTED_SOURCE_SHA256 = "2ede97d7d2860b67aac9ba3e255307b054dfff5aef4a4dfe368cd8ea57e39d32"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def wav_metadata(path: Path) -> dict:
    with wave.open(str(path), "rb") as reader:
        return {"sample_rate_hz": reader.getframerate(), "sample_count": reader.getnframes(),
                "channels": reader.getnchannels(), "sample_width_bytes": reader.getsampwidth(),
                "duration_ms": round(reader.getnframes() / reader.getframerate() * 1000)}


def post_once(client: JsonHttpClient, path: Path) -> tuple[int, dict]:
    boundary = "acceptance_" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\n"
        "已有32秒录音·MP3兼容性验收（文件模拟）\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"existing_recording_excerpt_32s.mp3\"\r\n"
        "Content-Type: audio/mpeg\r\n\r\n"
    ).encode("utf-8") + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Origin": client.origin, "Accept": "application/json", "X-Meeting-Copilot-Verification": "1",
               "Content-Type": f"multipart/form-data; boundary={boundary}"}
    if client.token:
        headers["X-Meeting-Copilot-Token"] = client.token
    if client.cookie:
        headers["Cookie"] = client.cookie
    connection = http.client.HTTPConnection("127.0.0.1", 8769, timeout=30)
    try:
        connection.request("POST", "/v2/meetings/import-audio", body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(1024 * 1024)
        return int(response.status), json.loads(raw)
    finally:
        connection.close()


def run(output: Path) -> dict:
    if output.resolve() != ARTIFACTS / "file-import-mp3-r01" or output.exists():
        raise ValueError("requires a new isolated file-import-mp3-r01 directory; no automatic retry")
    output.mkdir()
    source = ARTIFACTS / "audio-replay/existing_recording_excerpt_32s.wav"
    runtime = ARTIFACTS / "long-soak-r02/runtime"
    client = JsonHttpClient("http://127.0.0.1:8769", token=os.environ.get("MEETING_COPILOT_LOCAL_API_TOKEN", ""))
    proxy = JsonHttpClient("http://127.0.0.1:8790")
    report = {"status": "running", "scope": "existing third-party recording; MP3 formal file import only",
              "started_at_ms": time.time_ns() // 1000000, "device_or_playback": False,
              "recorded_text_or_secrets_saved": False, "upload_attempt_count": 0,
              "model_value_verified": False, "human_transcription_accuracy_verified": False}

    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        identity = status_report(port=8769, pid_file=ARTIFACTS / "long-soak-r02/server.pid")
        if identity.get("runtime_identity_verified") is not True:
            raise ValueError("managed runtime identity unavailable")
        report["service_pid"] = identity.get("pid")
        config = client.request("GET", "/providers/config").payload
        if config.get("base_url") not in {"http://127.0.0.1:8790", "http://127.0.0.1:8790/v1"} or config.get("model") != "gpt-6-sol":
            raise ValueError("isolated provider boundary unavailable")
        before = proxy.request("GET", "/status").payload
        if before.get("reserved_forward_attempts") != 12 or before.get("maximum_forward_attempts") != 12:
            raise ValueError("existing proxy must remain saturated at12")
        report["forward_attempts_at_start"] = 12
        if digest(source) != EXPECTED_SOURCE_SHA256:
            raise ValueError("source WAV provenance hash mismatch")
        report["source_wav"] = {"sha256": digest(source), "bytes": source.stat().st_size, **wav_metadata(source)}
        import imageio_ffmpeg
        converter = imageio_ffmpeg.get_ffmpeg_exe()
        derived = output / "existing_recording_excerpt_32s.mp3"
        converted = subprocess.run([converter, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
                                    "-vn", "-codec:a", "libmp3lame", "-b:a", "96k", "-y", str(derived)],
                                   capture_output=True, timeout=60,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        report["derivation_return_code"] = converted.returncode
        if converted.returncode != 0:
            raise ValueError("local MP3 derivation failed")
        report["derived_mp3"] = {"sha256": digest(derived), "bytes": derived.stat().st_size,
                                 "encoder": "libmp3lame", "bitrate_kbps": 96, "source_wav_sha256": EXPECTED_SOURCE_SHA256}
        save()
        report["upload_attempt_count"] = 1
        status, uploaded = post_once(client, derived)
        report["upload_http_status"] = status
        if status != 202:
            raise ValueError("formal MP3 upload rejected")
        meeting_id = str(uploaded["meeting_id"])
        report["meeting_id"] = meeting_id
        report["import_job_id"] = uploaded["import_job"]["id"]
        report["source_asset_sha256_at_upload"] = uploaded["source_audio"].get("sha256")
        save()
        print(json.dumps({"phase": "queued", "meeting_id": meeting_id, "upload_http_status": status}), flush=True)
        deadline = time.monotonic() + 360
        samples = []
        while True:
            job_response = client.request("GET", f"/v2/meetings/{meeting_id}/import-job")
            snapshot_response = client.request("GET", f"/v2/meetings/{meeting_id}/snapshot?segment_limit=100")
            job = job_response.payload["import_job"]
            sample = {key: job.get(key) for key in ["status", "stage", "attempts", "error_class", "progress"]}
            sample["captured_at_ms"] = time.time_ns() // 1000000
            sample["snapshot"] = safe_snapshot(snapshot_response.payload)
            samples.append(sample)
            report["samples"] = samples
            save()
            print(json.dumps({"phase": "poll", "import": sample,
                              "new_upstream_attempts": proxy.request("GET", "/status").payload.get("reserved_forward_attempts", 12) - 12}), flush=True)
            if job["status"] in {"failed", "cancelled"}:
                report["status"] = "failed_import"
                break
            if job["status"] == "succeeded" and sample["snapshot"]["active_job_count"] == 0:
                report["status"] = "terminal"
                break
            if time.monotonic() >= deadline:
                report["status"] = "bounded_timeout"
                break
            time.sleep(15)
        durable = durable_counts(runtime, meeting_id)
        report["durable"] = durable
        report["job_status_counts"] = dict(Counter(j["kind"] + ":" + j["status"] for j in durable["jobs"]))
        original = runtime / f"audio_assets/{meeting_id}/source.mp3"
        retained = runtime / f"audio_assets/{meeting_id}/audio.wav"
        report["retained_mp3"] = {"present": original.is_file(),
                                  "sha256": digest(original) if original.is_file() else None}
        report["normalized_pcm"] = {"present": retained.is_file(),
                                    "sha256": digest(retained) if retained.is_file() else None,
                                    **(wav_metadata(retained) if retained.is_file() else {})}
        after = proxy.request("GET", "/status").payload
        report["actual_forward_attempts_after"] = after.get("reserved_forward_attempts")
        report["new_upstream_forward_attempts"] = after.get("reserved_forward_attempts", 12) - 12
        report["new_local_budget_rejections"] = after.get("budget_rejections", 0) - before.get("budget_rejections", 0)
        normalized = report["normalized_pcm"]
        display = samples[-1]["snapshot"]
        report["conditions"] = {
            "one_official_upload": report["upload_attempt_count"] == 1 and status == 202,
            "local_batch_import_succeeded": job["status"] == "succeeded",
            "nonempty_durable_transcript": durable["raw_character_count"] > 0 and durable["segment_count"] > 0,
            "formal_uploaded_file_mirror": durable["formal_asr_mirror"]["present"] and durable["formal_asr_mirror"]["is_mock"] is False and durable["formal_asr_mirror"]["audio_source"] == "uploaded_file",
            "original_mp3_retained_exactly": report["retained_mp3"]["sha256"] == report["derived_mp3"]["sha256"],
            "normalized_pcm_retained": normalized["present"] and normalized.get("sample_rate_hz") == 16000 and normalized.get("channels") == 1,
            "decoded_duration_matches_source": abs(normalized.get("duration_ms", 0) - report["source_wav"]["duration_ms"]) <= 250,
            "display_uses_normalized_audio_duration": display.get("elapsed_ms") == normalized.get("duration_ms"),
            "managed_recording_hash_verified": bool(durable["recordings"]) and all(x["saved_file_verified"] for x in durable["recordings"]),
            "all_tasks_terminal": durable["active_job_count"] == 0,
            "zero_new_upstream_requests": report["new_upstream_forward_attempts"] == 0,
        }
        report["file_import_compatibility_verified"] = all(report["conditions"].values())
    except Exception as error:
        report["status"] = "exception"
        report["failure_class"] = type(error).__name__
        report["file_import_compatibility_verified"] = False
    report["completed_at_ms"] = time.time_ns() // 1000000
    save()
    print(json.dumps({"phase": "complete", "status": report["status"], "meeting_id": report.get("meeting_id"),
                      "file_import_compatibility_verified": report.get("file_import_compatibility_verified"),
                      "new_upstream_forward_attempts": report.get("new_upstream_forward_attempts"),
                      "failure_class": report.get("failure_class")}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ARTIFACTS / "file-import-mp3-r01")
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output_dir)["file_import_compatibility_verified"] else 1)
