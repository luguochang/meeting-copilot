import base64
import io
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from scripts import funasr_diarization_worker as worker


def _pcm(*samples: float) -> str:
    return base64.b64encode(
        b"".join(struct.pack("<f", sample) for sample in samples)
    ).decode("ascii")


class FakeDiarizationBackend:
    def is_speech(self, samples, **_kwargs):
        return bool(samples)

    def embedding(self, samples, **_kwargs):
        marker = int(round(samples[0]))
        return (1.0, 0.0) if marker == 1 else (0.0, 1.0)


class FakeEmbedding:
    def __init__(self, values):
        self._values = np.asarray([values], dtype=np.float32)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._values


class FakeAutoModel:
    def __init__(self, *, model: str, **kwargs):
        self.model = Path(model)
        self.kwargs = kwargs

    def generate(self, *, input, **kwargs):
        assert input.dtype == np.float32
        assert kwargs["disable_pbar"] is True
        if (self.model / "campplus_cn_common.bin").exists():
            return [{"spk_embedding": FakeEmbedding([3.0, 4.0])}]
        return [{"value": [[0, 100]] if float(np.max(np.abs(input))) > 0.01 else []}]


class NoisyFakeAutoModel(FakeAutoModel):
    def __init__(self, *, model: str, **kwargs):
        print("python init noise")
        os.write(1, b"native init noise\n")
        super().__init__(model=model, **kwargs)

    def generate(self, *, input, **kwargs):
        print("python inference noise")
        os.write(1, b"native inference noise\n")
        return super().generate(input=input, **kwargs)


def _model_dirs(tmp_path: Path) -> tuple[Path, Path]:
    vad = tmp_path / "vad"
    camplus = tmp_path / "camplus"
    vad.mkdir()
    camplus.mkdir()
    for filename in worker._VAD_REQUIRED_FILES:
        (vad / filename).write_bytes(b"fixture")
    for filename in worker._CAMPLUS_REQUIRED_FILES:
        (camplus / filename).write_bytes(b"fixture")
    return vad, camplus


def _commands(*commands: dict) -> list[str]:
    return [json.dumps(command, separators=(",", ":")) for command in commands]


def _events(lines: list[str], *, backend=None) -> list[dict]:
    return worker.run_jsonl(lines, backend=backend or FakeDiarizationBackend())


def test_jsonl_v1_emits_ready_turns_and_done_with_injected_backends():
    events = _events(
        _commands(
            {"type": "session", "session_id": "meeting-1"},
            {
                "type": "audio",
                "session_id": "meeting-1",
                "sample_start": 0,
                "pcm_base64": _pcm(1, 1),
            },
            {
                "type": "audio",
                "session_id": "meeting-1",
                "sample_start": 2,
                "pcm_base64": _pcm(2, 2),
            },
            {"type": "end", "session_id": "meeting-1"},
        )
    )

    assert events[0]["event_type"] == "ready"
    assert events[0]["protocol"] == worker.DIARIZATION_PROTOCOL
    turns = [event for event in events if event["event_type"] == "speaker.turn"]
    assert [
        (turn["speaker_id"], turn["sample_start"], turn["sample_end"]) for turn in turns
    ] == [
        ("speaker_1", 0, 2),
        ("speaker_2", 2, 4),
    ]
    done = [event for event in events if event["event_type"] == "speaker.done"]
    assert len(done) == 1
    assert done[0]["session_id"] == "meeting-1"
    assert done[0]["sample_count"] == 4
    assert done[0]["speaker_count"] == 2


def test_audio_payload_validation_rejects_bad_base64_unaligned_nonfinite_and_wrong_metadata():
    bad_lines = _commands(
        {"type": "session", "session_id": "validation"},
        {
            "type": "audio",
            "session_id": "validation",
            "sample_start": 0,
            "pcm_base64": "%%%",
        },
        {
            "type": "audio",
            "session_id": "validation",
            "sample_start": 0,
            "pcm_base64": base64.b64encode(b"123").decode("ascii"),
        },
        {
            "type": "audio",
            "session_id": "validation",
            "sample_start": 0,
            "sample_count": 2,
            "pcm_base64": _pcm(1),
        },
        {
            "type": "audio",
            "session_id": "validation",
            "sample_start": 0,
            "pcm_base64": _pcm(float("nan")),
        },
        {"type": "end", "session_id": "validation"},
    )

    events = _events(bad_lines)

    assert [
        event["error_code"] for event in events if event["event_type"] == "error"
    ] == [
        "invalid_audio_base64",
        "audio_payload_unaligned",
        "sample_count_mismatch",
        "non_finite_audio",
    ]


def test_sample_start_must_be_contiguous_and_bad_chunk_does_not_advance_state():
    events = _events(
        _commands(
            {"type": "session", "session_id": "gap"},
            {
                "type": "audio",
                "session_id": "gap",
                "sample_start": 0,
                "pcm_base64": _pcm(1),
            },
            {
                "type": "audio",
                "session_id": "gap",
                "sample_start": 3,
                "pcm_base64": _pcm(1),
            },
            {
                "type": "audio",
                "session_id": "gap",
                "sample_start": 2,
                "pcm_base64": _pcm(1),
            },
            {
                "type": "audio",
                "session_id": "gap",
                "sample_start": 1,
                "pcm_base64": _pcm(1),
            },
            {"type": "end", "session_id": "gap"},
        )
    )

    errors = [event for event in events if event["event_type"] == "error"]
    assert [event["error_code"] for event in errors] == [
        "sample_start_gap",
        "sample_start_gap",
    ]
    done = next(event for event in events if event["event_type"] == "speaker.done")
    assert done["sample_count"] == 2


def test_abort_discards_session_state_and_does_not_emit_done():
    events = _events(
        _commands(
            {"type": "session", "session_id": "abort-me"},
            {
                "type": "audio",
                "session_id": "abort-me",
                "sample_start": 0,
                "pcm_base64": _pcm(1),
            },
            {"type": "abort", "session_id": "abort-me", "reason": "client_cancelled"},
            {"type": "session", "session_id": "after-abort"},
            {"type": "end", "session_id": "after-abort"},
        )
    )

    aborted = next(
        event for event in events if event["event_type"] == "session_aborted"
    )
    assert aborted["session_id"] == "abort-me"
    assert aborted["reason"] == "client_cancelled"
    assert not any(
        event["event_type"] == "speaker.done" and event["session_id"] == "abort-me"
        for event in events
    )
    assert any(
        event["event_type"] == "speaker.done" and event["session_id"] == "after-abort"
        for event in events
    )


def test_session_state_is_bounded_and_only_one_session_is_allowed():
    events = worker.run_jsonl(
        _commands(
            {"type": "session", "session_id": "one"},
            {"type": "session", "session_id": "two"},
            {
                "type": "audio",
                "session_id": "one",
                "sample_start": 0,
                "pcm_base64": _pcm(1),
            },
            {"type": "end", "session_id": "one"},
        ),
        backend=FakeDiarizationBackend(),
        max_session_samples=0,
    )
    assert any(
        event["event_type"] == "error"
        and event["error_code"] == "session_already_active"
        for event in events
    )
    assert any(
        event["event_type"] == "error"
        and event["error_code"] == "session_limit_exceeded"
        for event in events
    )
    assert any(event["event_type"] == "speaker.done" for event in events)


def test_missing_local_models_fail_closed_without_constructing_or_downloading_backend(
    monkeypatch, capsys, tmp_path
):
    missing_vad = tmp_path / "missing-vad"
    missing_camplus = tmp_path / "missing-camplus"
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    exit_code = worker.main(
        ["--vad-dir", str(missing_vad), "--camplus-dir", str(missing_camplus)]
    )

    assert exit_code == 0
    event = json.loads(capsys.readouterr().out)
    assert event["event_type"] == "diarization_unavailable"
    assert event["reason"] == "missing_local_model"
    assert event["model_download_status"] == "not_performed"
    assert event["safe_to_download_models"] is False
    assert worker.os.environ["MODELSCOPE_OFFLINE"] == "1"
    assert worker.os.environ["HF_HUB_OFFLINE"] == "1"


def test_local_backend_requires_complete_model_inventory(tmp_path):
    vad, camplus = _model_dirs(tmp_path)
    (camplus / "campplus_cn_common.bin").unlink()

    with pytest.raises(worker.DiarizationUnavailable) as failure:
        worker.FunASRLocalDiarizationBackend(
            vad_dir=vad,
            camplus_dir=camplus,
            auto_model_factory=FakeAutoModel,
            numpy_module=np,
        )

    assert failure.value.reason == "incomplete_local_model"
    assert "campplus_cn_common.bin" in str(failure.value.detail)


def test_local_backend_runs_vad_and_returns_normalized_camplus_embedding(tmp_path):
    vad, camplus = _model_dirs(tmp_path)
    backend = worker.FunASRLocalDiarizationBackend(
        vad_dir=vad,
        camplus_dir=camplus,
        auto_model_factory=FakeAutoModel,
        numpy_module=np,
    )

    assert backend.is_speech((0.1, 0.2)) is True
    assert backend.is_speech((0.0, 0.0)) is False
    assert backend.embedding((0.1, 0.2)) == pytest.approx((0.6, 0.8))
    ready = worker._ready_event(backend)
    assert ready["backend"] == "funasr_local_vad_camplus"
    assert ready["model_resolution"] == "absolute_local_verified_files"
    assert worker.os.environ["MODELSCOPE_OFFLINE"] == "1"
    assert worker.os.environ["HF_HUB_OFFLINE"] == "1"


def test_main_keeps_import_init_and_inference_noise_out_of_protocol_stdout(
    monkeypatch, capfd, tmp_path
):
    vad, camplus = _model_dirs(tmp_path)
    audio_payload = _pcm(0.1, 0.2)
    commands = _commands(
        {"type": "session", "session_id": "noise-isolation"},
        {
            "type": "audio",
            "session_id": "noise-isolation",
            "sample_start": 0,
            "pcm_base64": audio_payload,
        },
        {"type": "end", "session_id": "noise-isolation"},
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(commands) + "\n"))

    def noisy_funasr_import():
        print("python import noise")
        os.write(1, b"native import noise\n")
        return NoisyFakeAutoModel

    def noisy_numpy_import():
        print("python numpy import noise")
        os.write(1, b"native numpy import noise\n")
        return np

    monkeypatch.setattr(worker, "_import_funasr_auto_model", noisy_funasr_import)
    monkeypatch.setattr(worker, "_import_numpy", noisy_numpy_import)

    assert worker.main(["--vad-dir", str(vad), "--camplus-dir", str(camplus)]) == 0

    captured = capfd.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert [event["event_type"] for event in events] == [
        "ready",
        "session_started",
        "speaker.turn",
        "speaker.done",
    ]
    noise_markers = (
        "python import noise",
        "native import noise",
        "python numpy import noise",
        "native numpy import noise",
        "python init noise",
        "native init noise",
        "python inference noise",
        "native inference noise",
    )
    assert all(marker not in captured.out for marker in noise_markers)
    assert all(marker in captured.err for marker in noise_markers)
    assert captured.err.count("python inference noise") == 2
    assert captured.err.count("native inference noise") == 2
    assert audio_payload not in captured.err


def test_default_cluster_threshold_merges_known_same_speaker_and_separates_other_speaker():
    state = worker.SessionState(session_id="calibrated")
    diarization = worker.DiarizationWorker(backend=FakeDiarizationBackend())
    first = (1.0, 0.0, 0.0)
    known_same_speaker = worker._normalize_vector((0.694, 0.72, 0.0))
    different_speaker = (0.0, 0.0, 1.0)

    assert diarization._assign_cluster(state, first)[0] == "speaker_1"
    assert diarization._assign_cluster(state, known_same_speaker)[0] == "speaker_1"
    assert diarization._assign_cluster(state, different_speaker)[0] == "speaker_2"
    assert worker.DEFAULT_SIMILARITY_THRESHOLD == 0.35


def test_describe_is_protocol_only_and_does_not_require_models(capsys):
    assert worker.main(["--describe"]) == 0

    description = json.loads(capsys.readouterr().out)
    assert description["protocol"] == worker.DIARIZATION_PROTOCOL
    assert description["protocol_version"] == 1
    assert description["commands"] == ["session", "audio", "end", "abort"]
    assert description["offline_policy"]["remote_download"] is False
    assert description["implementation_status"] == "local_funasr_vad_camplus_supported"
    assert description["clustering"]["cosine_similarity_threshold"] == 0.35


def test_manifest_records_verified_camplus_without_claiming_bundle_or_public_release():
    manifest_path = (
        Path(__file__).parents[1]
        / "model_packs"
        / "diarization-camplus-zh-cn.manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["status"] == "locally_verified_not_bundled"
    assert manifest["resolution"] == "immutable_upstream_tag_and_file_hashes"
    assert manifest["public_release_approved"] is False
    assert manifest["license"]["spdx"] == "Apache-2.0"
    camplus = manifest["models"]["camplus"]
    assert camplus["model_id"] == "iic/speech_campplus_sv_zh-cn_16k-common"
    assert camplus["revision"] == "v2.0.2"
    assert camplus["sha256"] == camplus["required_files"]["campplus_cn_common.bin"]
    assert manifest["models"]["vad"]["status"] == "unresolved"
    assert manifest["models"]["vad"]["sha256"] is None
