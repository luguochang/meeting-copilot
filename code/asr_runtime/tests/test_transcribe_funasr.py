import json
import io
import sys
import types
from pathlib import Path

import numpy as np

from scripts import transcribe_funasr
from scripts import funasr_stream_worker


REPO_ROOT = Path(__file__).resolve().parents[3]
FUNASR_HOTWORD_MANIFEST = REPO_ROOT / "data" / "asr_eval" / "glossaries" / "funasr-hotwords.zh.json"


def create_local_funasr_model_dir(tmp_path):
    model_dir = tmp_path / "funasr-local-model"
    model_dir.mkdir()
    (model_dir / "model.pt").write_text("fake-model", encoding="utf-8")
    (model_dir / "config.yaml").write_text("fake-config", encoding="utf-8")
    return model_dir


class FakeAutoModel:
    calls = []

    def __init__(self, **kwargs):
        print("provider boot log")
        self.calls.append(kwargs)

    def generate(self, **kwargs):
        print("provider generate log")
        return [
            {
                "text": "灰度百分之十",
                "timestamp": [[100, 260], [260, 520], [520, 760]],
            }
        ]


class FakeOfflineBatchAutoModel:
    calls = []

    def __init__(self, **kwargs):
        print("offline batch provider boot log")
        self.calls.append(("init", kwargs))

    def generate(self, **kwargs):
        print("offline batch provider generate log")
        self.calls.append(("generate", kwargs))
        audio_name = Path(str(kwargs["input"])).stem
        return [
            {
                "text": f"{audio_name}，已加标点。",
                "timestamp": [[0, 100], [100, 200]],
            }
        ]


def test_main_keeps_provider_stdout_noise_out_of_json(capsys, monkeypatch, tmp_path):
    FakeAutoModel.calls = []
    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeAutoModel))
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"")

    transcribe_funasr.main([str(audio), "--model", "fake-model", "--no-punc"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["text"] == "灰度百分之十"
    assert payload["segments"] == [
        {
            "id": "funasr_001",
            "start_ms": 100,
            "end_ms": 760,
            "text": "灰度百分之十",
            "is_final": True,
        }
    ]
    assert "provider boot log" in captured.err
    assert "provider generate log" in captured.err


def test_no_punc_omits_punctuation_model(monkeypatch, tmp_path):
    FakeAutoModel.calls = []
    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeAutoModel))
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"")

    transcribe_funasr.transcribe(
        audio_path=audio,
        model_name="fake-model",
        device="cpu",
        punc_model=None,
    )

    assert FakeAutoModel.calls[0]["model"] == "fake-model"
    assert FakeAutoModel.calls[0]["vad_model"] == "fsmn-vad"
    assert "punc_model" not in FakeAutoModel.calls[0]


def test_transcribe_preserves_one_segment_per_funasr_result(monkeypatch, tmp_path):
    FakeAutoModel.calls = []
    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=FakeAutoModel))
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"")

    result = transcribe_funasr.transcribe(
        audio_path=audio,
        model_name="fake-model",
        device="cpu",
        punc_model=None,
    )

    assert result["segments"] == [
        {
            "id": "funasr_001",
            "start_ms": 100,
            "end_ms": 760,
            "text": "灰度百分之十",
            "is_final": True,
        }
    ]


class FakeStreamingAutoModel:
    calls = []

    def __init__(self, **kwargs):
        print("streaming provider boot log")
        self.calls.append(("init", kwargs))
        self.outputs = ["先灰度", "百分之十", "需要回滚", "阈值"]

    def generate(self, **kwargs):
        print("streaming provider generate log")
        self.calls.append(("generate", kwargs))
        text = self.outputs.pop(0) if self.outputs else ""
        return [{"text": text}]


class FakeCumulativeStreamingAutoModel:
    calls = []

    def __init__(self, **kwargs):
        self.calls.append(("init", kwargs))
        self.outputs = ["先灰", "先灰度", "先灰度百分之十", "需要回滚"]

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        text = self.outputs.pop(0) if self.outputs else ""
        return [{"text": text}]


class FakeCorrectionStreamingAutoModel:
    def __init__(self, **kwargs):
        self.outputs = ["先挥", "先灰度", "先灰度百分之十"]

    def generate(self, **kwargs):
        text = self.outputs.pop(0) if self.outputs else ""
        return [{"text": text}]


class FakeWorkerNonCumulativeStreamingAutoModel:
    def __init__(self, **kwargs):
        self.outputs = ["发布评审", "P99延迟超过九百毫秒", "张三补SLO看板"]

    def generate(self, **kwargs):
        text = self.outputs.pop(0) if self.outputs else ""
        return [{"text": text}]


class FakeWorkerStdin:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)


def test_stream_events_emits_partial_multiple_finals_and_eos(monkeypatch, tmp_path):
    FakeStreamingAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(38400, dtype=np.float32),
                16000,
            )
        ),
    )

    events = transcribe_funasr.stream_events(
        audio_path=tmp_path / "sample.wav",
        model_name="paraformer-zh-streaming",
        local_model_dir=create_local_funasr_model_dir(tmp_path),
        device="cpu",
        chunk_size=[0, 10, 5],
        final_window_ms=1200,
    )

    assert [(event.event_type, event.segment_id, event.text) for event in events] == [
        ("partial", "funasr_001", "先灰度"),
        ("partial", "funasr_001", "百分之十"),
        ("final", "funasr_001", "先灰度百分之十"),
        ("partial", "funasr_002", "需要回滚"),
        ("partial", "funasr_002", "阈值"),
        ("final", "funasr_002", "需要回滚阈值"),
        ("end_of_stream", "funasr_eos", ""),
    ]
    assert events[0].start_ms == 0
    assert events[0].end_ms == 600
    assert events[2].start_ms == 0
    assert events[2].end_ms == 1200
    assert events[5].start_ms == 1200
    assert events[5].end_ms == 2400

    init_kwargs = FakeStreamingAutoModel.calls[0][1]
    assert init_kwargs["model"] == str(tmp_path / "funasr-local-model")
    assert init_kwargs["device"] == "cpu"

    generate_kwargs = [call[1] for call in FakeStreamingAutoModel.calls if call[0] == "generate"]
    assert generate_kwargs[0]["chunk_size"] == [0, 10, 5]
    assert generate_kwargs[0]["encoder_chunk_look_back"] == 4
    assert generate_kwargs[0]["decoder_chunk_look_back"] == 1
    assert [kwargs["is_final"] for kwargs in generate_kwargs] == [False, False, False, True]


def test_stream_worker_accepts_balanced_chinese_meeting_chunk_profile():
    args = funasr_stream_worker.parse_args(["--chunk-size", "0,30,15"])

    assert args.chunk_size == [0, 30, 15]
    assert funasr_stream_worker.chunk_stride_samples(args.chunk_size) == 28_800


def test_stream_worker_merges_non_cumulative_partials_for_final_transcript():
    merged = ""

    for partial in ["院子门口不远处", "就是一个地铁站", "邮局门前的人行道上有一个蓝色的邮箱"]:
        merged = funasr_stream_worker.merge_partial_hypothesis(merged, partial)

    assert merged == "院子门口不远处就是一个地铁站邮局门前的人行道上有一个蓝色的邮箱"

    corrected = funasr_stream_worker.merge_partial_hypothesis("先挥", "先灰度百分之十")
    assert corrected == "先灰度百分之十"


def test_stream_worker_emits_accumulated_partial_text_for_live_transcript(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeWorkerNonCumulativeStreamingAutoModel),
    )
    chunk = np.ones(funasr_stream_worker.chunk_stride_samples([0, 10, 5]), dtype=np.float32)
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(chunk.tobytes() * 3))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    funasr_stream_worker.main(["--chunk-size", "0,10,5"])

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    partial_texts = [event["text"] for event in events if event["event_type"] == "partial"]
    assert partial_texts == [
        "发布评审",
        "发布评审P99延迟超过九百毫秒",
        "发布评审P99延迟超过九百毫秒张三补SLO看板",
    ]


def test_stream_worker_emits_ready_control_event_before_audio_events(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setattr(sys, "stdin", FakeWorkerStdin(b""))
    stdout = io.StringIO()
    monkeypatch.setattr(funasr_stream_worker, "_REAL_STDOUT", stdout)

    funasr_stream_worker.main([])

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert events[0]["event_type"] == "ready"
    assert events[0]["provider"] == "funasr_realtime"


def test_stream_events_merges_cumulative_partial_hypotheses(monkeypatch, tmp_path):
    FakeCumulativeStreamingAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeCumulativeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(38400, dtype=np.float32),
                16000,
            )
        ),
    )

    events = transcribe_funasr.stream_events(
        audio_path=tmp_path / "sample.wav",
        model_name="paraformer-zh-streaming",
        local_model_dir=create_local_funasr_model_dir(tmp_path),
        device="cpu",
        chunk_size=[0, 10, 5],
        final_window_ms=1800,
    )

    finals = [event for event in events if event.event_type == "final"]
    assert [event.text for event in finals] == ["先灰度百分之十", "需要回滚"]


def test_stream_events_drops_stale_text_when_partial_corrects_previous(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeCorrectionStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(28800, dtype=np.float32),
                16000,
            )
        ),
    )

    events = transcribe_funasr.stream_events(
        audio_path=tmp_path / "sample.wav",
        model_name="paraformer-zh-streaming",
        local_model_dir=create_local_funasr_model_dir(tmp_path),
        device="cpu",
        chunk_size=[0, 10, 5],
        final_window_ms=0,
    )

    finals = [event for event in events if event.event_type == "final"]
    assert [event.text for event in finals] == ["先灰度百分之十"]


def test_stream_events_rejects_non_16k_audio(monkeypatch, tmp_path):
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(48000, dtype=np.float32),
                48000,
            )
        ),
    )

    try:
        transcribe_funasr.stream_events(
            audio_path=tmp_path / "sample.wav",
            model_name="paraformer-zh-streaming",
            local_model_dir=create_local_funasr_model_dir(tmp_path),
            device="cpu",
            chunk_size=[0, 10, 5],
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected non-16k audio to fail")

    assert "16k" in message


def test_streaming_main_can_write_events_without_leaking_paths(monkeypatch, tmp_path, capsys):
    FakeStreamingAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(38400, dtype=np.float32),
                16000,
            )
        ),
    )
    audio = tmp_path / "private-audio-name.wav"
    audio.write_bytes(b"fake")
    events_output = tmp_path / "events.json"
    local_model_dir = create_local_funasr_model_dir(tmp_path)

    transcribe_funasr.main(
        [
            str(audio),
            "--streaming",
            "--model",
            "paraformer-zh-streaming",
            "--local-model-dir",
            str(local_model_dir),
            "--events-output",
            str(events_output),
            "--final-window-ms",
            "1200",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    events = json.loads(events_output.read_text(encoding="utf-8"))
    assert payload["raw"]["mode"] == "file_replayed_streaming_events"
    assert payload["raw"]["provider"] == "funasr"
    assert payload["raw"]["model_id"] == "paraformer-zh-streaming"
    assert payload["raw"]["model_resolution"] == "local_model_dir"
    assert payload["raw"]["model_download_status"] == "not_performed"
    assert payload["raw"]["finalization_strategy"] == "fixed_window_from_partial_hypotheses"
    assert payload["raw"]["provider_endpoint_finals"] is False
    assert payload["raw"]["partial_event_count"] == 4
    assert payload["raw"]["final_event_count"] == 2
    assert "audio_path" not in payload["raw"]
    assert "private-audio-name" not in captured.out
    assert [event["event_type"] for event in events] == [
        "partial",
        "partial",
        "final",
        "partial",
        "partial",
        "final",
        "end_of_stream",
    ]
    assert "streaming provider boot log" in captured.err
    assert "streaming provider generate log" in captured.err


def test_streaming_main_accepts_hotword_manifest(monkeypatch, tmp_path, capsys):
    FakeStreamingAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(19200, dtype=np.float32),
                16000,
            )
        ),
    )
    audio = tmp_path / "private-audio-name.wav"
    audio.write_bytes(b"fake")

    transcribe_funasr.main(
        [
            str(audio),
            "--streaming",
            "--model",
            "paraformer-zh-streaming",
            "--local-model-dir",
            str(create_local_funasr_model_dir(tmp_path)),
            "--hotword-manifest",
            str(FUNASR_HOTWORD_MANIFEST),
            "--final-window-ms",
            "1200",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    generate_kwargs = [call[1] for call in FakeStreamingAutoModel.calls if call[0] == "generate"]
    assert "payment-gateway" in generate_kwargs[0]["hotword"]
    assert payload["raw"]["hotword_status"] == "enabled"
    assert payload["raw"]["hotword_count"] >= 10
    assert "funasr-hotwords.zh.json" not in captured.out
    assert str(REPO_ROOT) not in captured.out


def test_streaming_blocks_without_local_model_dir_before_importing_funasr(monkeypatch, tmp_path):
    class ExplodingAutoModel:
        def __init__(self, **_kwargs):
            raise AssertionError("AutoModel must not be constructed without a local model dir")

    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=ExplodingAutoModel),
    )

    result = transcribe_funasr.transcribe_streaming(
        audio_path=tmp_path / "sample.wav",
        model_name="paraformer-zh-streaming",
        device="cpu",
        chunk_size=[0, 10, 5],
    )

    assert result["status"] == "blocked"
    assert result["provider"] == "funasr"
    assert result["model_id"] == "paraformer-zh-streaming"
    assert result["model_resolution_status"] == "blocked_missing_local_model_dir"
    assert result["safe_to_download_models"] is False
    assert result["safe_to_call_remote_asr"] is False
    assert result["safe_to_call_llm"] is False
    assert result["text"] == ""
    assert result["segments"] == []
    result_json = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in result_json


def test_transcribe_streaming_batch_reuses_one_model_and_reports_per_file_rtf(monkeypatch, tmp_path):
    FakeStreamingAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(19200, dtype=np.float32),
                16000,
            )
        ),
    )
    audio_paths = [tmp_path / "sample-a.wav", tmp_path / "sample-b.wav"]
    for audio_path in audio_paths:
        audio_path.write_bytes(b"fake")

    batch = transcribe_funasr.transcribe_streaming_batch(
        audio_paths=audio_paths,
        model_name="paraformer-zh-streaming",
        local_model_dir=create_local_funasr_model_dir(tmp_path),
        device="cpu",
        chunk_size=[0, 10, 5],
        final_window_ms=1200,
    )

    init_calls = [call for call in FakeStreamingAutoModel.calls if call[0] == "init"]
    generate_calls = [call for call in FakeStreamingAutoModel.calls if call[0] == "generate"]
    assert len(init_calls) == 1
    assert len(generate_calls) == 4
    assert batch["status"] == "ok"
    assert batch["batch_mode"] == "single_process_reused_funasr_model"
    assert batch["model_load_latency_ms"] >= 0
    assert [item["audio_id"] for item in batch["items"]] == ["sample-a", "sample-b"]
    assert all(item["raw"]["mode"] == "file_replayed_streaming_events_batch" for item in batch["items"])
    assert all(item["audio_duration_seconds"] == 1.2 for item in batch["items"])
    assert all(str(tmp_path) not in json.dumps(item, ensure_ascii=False) for item in batch["items"])


def test_streaming_batch_loads_hotword_manifest_and_passes_terms_to_generate(monkeypatch, tmp_path):
    FakeStreamingAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeStreamingAutoModel),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(
            read=lambda *_args, **_kwargs: (
                np.ones(19200, dtype=np.float32),
                16000,
            )
        ),
    )
    audio = tmp_path / "sample-a.wav"
    audio.write_bytes(b"fake")

    batch = transcribe_funasr.transcribe_streaming_batch(
        audio_paths=[audio],
        model_name="paraformer-zh-streaming",
        local_model_dir=create_local_funasr_model_dir(tmp_path),
        device="cpu",
        chunk_size=[0, 10, 5],
        final_window_ms=1200,
        hotword_manifest_path=FUNASR_HOTWORD_MANIFEST,
    )

    generate_kwargs = [call[1] for call in FakeStreamingAutoModel.calls if call[0] == "generate"]
    assert generate_kwargs
    assert "payment-gateway" in generate_kwargs[0]["hotword"]
    assert generate_kwargs[0]["hotwords"] == generate_kwargs[0]["hotword"]
    assert batch["hotword_status"] == "enabled"
    assert batch["hotword_count"] >= 10
    assert len(batch["hotword_manifest_sha256"]) == 64
    item_json = json.dumps(batch["items"][0], ensure_ascii=False)
    assert "funasr-hotwords.zh.json" not in item_json
    assert str(REPO_ROOT) not in item_json
    assert batch["items"][0]["raw"]["hotword_status"] == "enabled"


def test_transcribe_offline_batch_reuses_one_model_and_reports_rtf_without_path_leaks(monkeypatch, tmp_path):
    FakeOfflineBatchAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeOfflineBatchAutoModel),
    )

    class FakeInfo:
        frames = 32000
        samplerate = 16000

    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(info=lambda *_args, **_kwargs: FakeInfo()),
    )
    audio_paths = [tmp_path / "meeting-a.16k.wav", tmp_path / "meeting-b.wav"]
    for audio_path in audio_paths:
        audio_path.write_bytes(b"fake")

    batch = transcribe_funasr.transcribe_offline_batch(
        audio_paths=audio_paths,
        model_name="/private/cache/offline-model",
        vad_model="/private/cache/vad-model",
        punc_model="/private/cache/punc-model",
        device="cpu",
    )

    init_calls = [call for call in FakeOfflineBatchAutoModel.calls if call[0] == "init"]
    generate_calls = [call for call in FakeOfflineBatchAutoModel.calls if call[0] == "generate"]
    assert len(init_calls) == 1
    assert len(generate_calls) == 2
    assert init_calls[0][1]["model"] == "/private/cache/offline-model"
    assert init_calls[0][1]["vad_model"] == "/private/cache/vad-model"
    assert init_calls[0][1]["punc_model"] == "/private/cache/punc-model"
    assert batch["status"] == "ok"
    assert batch["batch_mode"] == "single_process_reused_funasr_offline_model"
    assert batch["item_count"] == 2
    assert batch["total_audio_duration_seconds"] == 4.0
    assert batch["transcribe_only_rtf"] >= 0
    assert [item["audio_id"] for item in batch["items"]] == ["meeting-a", "meeting-b"]
    assert [item["text"] for item in batch["items"]] == [
        "meeting-a.16k，已加标点。",
        "meeting-b，已加标点。",
    ]
    assert all(item["raw"]["mode"] == "file_batch_offline_transcript" for item in batch["items"])
    assert all(item["audio_duration_seconds"] == 2.0 for item in batch["items"])
    assert batch["safe_to_call_remote_asr"] is False
    assert batch["safe_to_call_llm"] is False
    batch_json = json.dumps(batch, ensure_ascii=False)
    assert str(tmp_path) not in batch_json
    assert "/private/cache" not in batch_json


def test_transcribe_offline_batch_omits_punctuation_model_when_disabled(monkeypatch, tmp_path):
    FakeOfflineBatchAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeOfflineBatchAutoModel),
    )

    class FakeInfo:
        frames = 16000
        samplerate = 16000

    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(info=lambda *_args, **_kwargs: FakeInfo()),
    )
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")

    batch = transcribe_funasr.transcribe_offline_batch(
        audio_paths=[audio],
        model_name="offline-model",
        vad_model="fsmn-vad",
        punc_model=None,
        device="cpu",
    )

    init_kwargs = [call[1] for call in FakeOfflineBatchAutoModel.calls if call[0] == "init"][0]
    assert "punc_model" not in init_kwargs
    assert batch["punc_model_status"] == "disabled"
    assert batch["items"][0]["raw"]["punc_model_status"] == "disabled"


def test_offline_batch_main_outputs_json_and_keeps_provider_noise_out_of_stdout(monkeypatch, tmp_path, capsys):
    FakeOfflineBatchAutoModel.calls = []
    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=FakeOfflineBatchAutoModel),
    )

    class FakeInfo:
        frames = 16000
        samplerate = 16000

    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(info=lambda *_args, **_kwargs: FakeInfo()),
    )
    audio_a = tmp_path / "meeting-a.16k.wav"
    audio_b = tmp_path / "meeting-b.wav"
    audio_a.write_bytes(b"fake")
    audio_b.write_bytes(b"fake")

    transcribe_funasr.main(
        [
            str(audio_a),
            str(audio_b),
            "--offline-batch",
            "--model",
            "/private/cache/offline-model",
            "--vad-model",
            "/private/cache/vad-model",
            "--punc-model",
            "/private/cache/punc-model",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["batch_mode"] == "single_process_reused_funasr_offline_model"
    assert [item["audio_id"] for item in payload["items"]] == ["meeting-a", "meeting-b"]
    assert "offline batch provider boot log" in captured.err
    assert "offline batch provider generate log" in captured.err
    assert "/private/cache" not in captured.out
    assert str(tmp_path) not in captured.out


def test_streaming_batch_blocks_forbidden_hotword_manifest_before_model_load(monkeypatch, tmp_path):
    class ExplodingAutoModel:
        def __init__(self, **_kwargs):
            raise AssertionError("AutoModel must not be constructed for a forbidden hotword manifest")

    monkeypatch.setitem(
        sys.modules,
        "funasr",
        types.SimpleNamespace(AutoModel=ExplodingAutoModel),
    )

    batch = transcribe_funasr.transcribe_streaming_batch(
        audio_paths=[tmp_path / "sample-a.wav"],
        model_name="paraformer-zh-streaming",
        local_model_dir=create_local_funasr_model_dir(tmp_path),
        device="cpu",
        chunk_size=[0, 10, 5],
        hotword_manifest_path=transcribe_funasr.REPO_ROOT
        / "data"
        / "asr_eval"
        / "local_samples"
        / "hotwords.json",
    )

    assert batch["status"] == "blocked"
    assert batch["hotword_status"] == "blocked_invalid_hotword_manifest"
    assert batch["items"] == []
    assert any("hotword manifest path is forbidden" in error for error in batch["validation_errors"])


def test_stream_events_rejects_local_model_dir_without_required_files(tmp_path):
    incomplete_model_dir = tmp_path / "incomplete-model"
    incomplete_model_dir.mkdir()

    try:
        transcribe_funasr.stream_events(
            audio_path=tmp_path / "sample.wav",
            model_name="paraformer-zh-streaming",
            local_model_dir=incomplete_model_dir,
            device="cpu",
            chunk_size=[0, 10, 5],
        )
    except transcribe_funasr.OfflineModelGuardError as exc:
        errors = exc.validation_errors
    else:
        raise AssertionError("expected incomplete local model dir to fail")

    assert "local model dir is missing required file: model.pt" in errors
    assert "local model dir is missing required file: config.yaml" in errors


def test_stream_events_rejects_local_model_dir_under_forbidden_project_roots():
    forbidden_model_dir = transcribe_funasr.REPO_ROOT / "configs" / "local" / "funasr-model"

    try:
        transcribe_funasr.stream_events(
            audio_path=transcribe_funasr.REPO_ROOT / "artifacts" / "tmp" / "synthetic_audio" / "sample.wav",
            model_name="paraformer-zh-streaming",
            local_model_dir=forbidden_model_dir,
            device="cpu",
            chunk_size=[0, 10, 5],
        )
    except transcribe_funasr.OfflineModelGuardError as exc:
        errors = exc.validation_errors
    else:
        raise AssertionError("expected forbidden local model dir to fail")

    assert "local model dir is under a forbidden project root" in errors
