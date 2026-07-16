from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.streaming_contract import (
    StreamingTranscriptEvent,
    build_provider_transcript_from_stream,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_LOCAL_MODEL_FILES = ("model.pt", "config.yaml")
FORBIDDEN_LOCAL_MODEL_REPO_ROOTS = (
    Path("configs/local"),
    Path("data/asr_eval/local_samples"),
    Path("data/asr_eval/samples"),
    Path("data/local_runtime"),
    Path("outputs"),
)
ALLOWED_HOTWORD_MANIFEST_REPO_ROOTS = (
    Path("data/asr_eval/glossaries"),
    Path("artifacts/tmp/asr_reports"),
)


class OfflineModelGuardError(RuntimeError):
    def __init__(
        self,
        message: str,
        validation_errors: list[str],
        model_resolution_status: str,
    ) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors
        self.model_resolution_status = model_resolution_status


@dataclass(frozen=True)
class HotwordConfig:
    words: list[str]
    status: str
    count: int
    sha256: str | None

    @classmethod
    def disabled(cls) -> "HotwordConfig":
        return cls(words=[], status="disabled", count=0, sha256=None)

    def raw_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "hotword_status": self.status,
            "hotword_count": self.count,
        }
        if self.sha256:
            metadata["hotword_manifest_sha256"] = self.sha256
        return metadata


def transcribe(
    audio_path: Path,
    model_name: str,
    device: str,
    punc_model: str | None = "ct-punc",
) -> dict:
    started = time.monotonic()
    model_kwargs = {
        "model": model_name,
        "vad_model": "fsmn-vad",
        "device": device,
        "disable_update": True,
    }
    if punc_model:
        model_kwargs["punc_model"] = punc_model

    with contextlib.redirect_stdout(sys.stderr):
        from funasr import AutoModel

        model = AutoModel(**model_kwargs)
        result = model.generate(
            input=str(audio_path),
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
    text = "".join(item.get("text", "") for item in result)
    return {
        "text": text,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "entities": [],
        "segments": _segments_from_result(result),
        "raw": {
            "provider": "funasr",
            "model": model_name,
            "device": device,
            "punc_model": punc_model,
            "result_count": len(result),
        },
    }


def transcribe_streaming(
    audio_path: Path,
    model_name: str,
    device: str,
    chunk_size: list[int],
    local_model_dir: Path | None = None,
    encoder_chunk_look_back: int = 4,
    decoder_chunk_look_back: int = 1,
    final_window_ms: int = 3_000,
    events_output: Path | None = None,
    hotword_manifest_path: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        hotword_config = load_hotword_manifest(hotword_manifest_path)
        events = stream_events(
            audio_path=audio_path,
            model_name=model_name,
            local_model_dir=local_model_dir,
            device=device,
            chunk_size=chunk_size,
            encoder_chunk_look_back=encoder_chunk_look_back,
            decoder_chunk_look_back=decoder_chunk_look_back,
            final_window_ms=final_window_ms,
            hotwords=hotword_config.words,
        )
    except OfflineModelGuardError as exc:
        return _blocked_streaming_result(
            model_name=model_name,
            device=device,
            chunk_size=chunk_size,
            encoder_chunk_look_back=encoder_chunk_look_back,
            decoder_chunk_look_back=decoder_chunk_look_back,
            final_window_ms=final_window_ms,
            latency_ms=int((time.monotonic() - started) * 1000),
            guard_error=exc,
        )
    if events_output:
        events_output.parent.mkdir(parents=True, exist_ok=True)
        events_output.write_text(
            json.dumps([asdict(event) for event in events], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    transcript = build_provider_transcript_from_stream("funasr", events)
    latency_ms = int((time.monotonic() - started) * 1000)
    duration_seconds = max((event.end_ms for event in events), default=0) / 1000
    transcript["latency_ms"] = latency_ms
    transcript["raw"].update(
        {
            "provider": "funasr",
            "model_id": model_name,
            "model_resolution": "local_model_dir",
            "model_download_status": "not_performed",
            "safe_to_download_models": False,
            "device": device,
            "chunk_size": chunk_size,
            "encoder_chunk_look_back": encoder_chunk_look_back,
            "decoder_chunk_look_back": decoder_chunk_look_back,
            "final_window_ms": final_window_ms,
            "finalization_strategy": "fixed_window_from_partial_hypotheses",
            "provider_endpoint_finals": False,
            "mode": "file_replayed_streaming_events",
            **hotword_config.raw_metadata(),
        }
    )
    return {
        "status": "ok",
        "text": transcript["text"],
        "latency_ms": latency_ms,
        "audio_duration_seconds": round(duration_seconds, 6),
        "rtf": round((latency_ms / 1000) / duration_seconds, 6) if duration_seconds else 0.0,
        "entities": [],
        "segments": transcript["segments"],
        "raw": transcript["raw"],
    }


def transcribe_streaming_batch(
    *,
    audio_paths: list[Path],
    model_name: str,
    local_model_dir: Path | None,
    device: str,
    chunk_size: list[int],
    encoder_chunk_look_back: int = 4,
    decoder_chunk_look_back: int = 1,
    final_window_ms: int = 3_000,
    events_output_dir: Path | None = None,
    hotword_manifest_path: Path | None = None,
) -> dict[str, Any]:
    model_started = time.monotonic()
    try:
        hotword_config = load_hotword_manifest(hotword_manifest_path)
        with contextlib.redirect_stdout(sys.stderr):
            model = _load_streaming_model(
                model_name=model_name,
                local_model_dir=local_model_dir,
                device=device,
            )
    except OfflineModelGuardError as exc:
        return {
            "status": "blocked",
            "batch_mode": "single_process_reused_funasr_model",
            "model_resolution_status": exc.model_resolution_status,
            "model_download_status": "blocked_or_not_started",
            "hotword_status": "blocked_invalid_hotword_manifest"
            if exc.model_resolution_status == "blocked_invalid_hotword_manifest"
            else "disabled",
            "model_load_latency_ms": int((time.monotonic() - model_started) * 1000),
            "items": [],
            "safe_to_download_models": False,
            "safe_to_read_user_audio": False,
            "safe_to_read_configs_local": False,
            "safe_to_call_remote_asr": False,
            "safe_to_call_llm": False,
            "validation_errors": exc.validation_errors,
        }

    model_load_latency_ms = int((time.monotonic() - model_started) * 1000)
    items: list[dict[str, Any]] = []
    total_audio_duration_seconds = 0.0
    total_transcribe_latency_ms = 0

    for audio_path in audio_paths:
        item_started = time.monotonic()
        with contextlib.redirect_stdout(sys.stderr):
            events = _stream_events_with_loaded_model(
                model=model,
                audio_path=audio_path,
                chunk_size=chunk_size,
                encoder_chunk_look_back=encoder_chunk_look_back,
                decoder_chunk_look_back=decoder_chunk_look_back,
                final_window_ms=final_window_ms,
                started=item_started,
                hotwords=hotword_config.words,
            )
        if events_output_dir:
            events_output_dir.mkdir(parents=True, exist_ok=True)
            (events_output_dir / f"{audio_path.stem}.funasr.batch-events.json").write_text(
                json.dumps([asdict(event) for event in events], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        transcript = build_provider_transcript_from_stream("funasr", events)
        latency_ms = int((time.monotonic() - item_started) * 1000)
        duration_seconds = max((event.end_ms for event in events), default=0) / 1000
        total_audio_duration_seconds += duration_seconds
        total_transcribe_latency_ms += latency_ms
        transcript["latency_ms"] = latency_ms
        transcript["raw"].update(
            {
                "provider": "funasr",
                "model_id": model_name,
                "model_resolution": "local_model_dir",
                "model_download_status": "not_performed",
                "safe_to_download_models": False,
                "device": device,
                "chunk_size": chunk_size,
                "encoder_chunk_look_back": encoder_chunk_look_back,
                "decoder_chunk_look_back": decoder_chunk_look_back,
                "final_window_ms": final_window_ms,
                "finalization_strategy": "fixed_window_from_partial_hypotheses",
                "provider_endpoint_finals": False,
                "mode": "file_replayed_streaming_events_batch",
                "batch_model_load_excluded_from_latency": True,
                **hotword_config.raw_metadata(),
            }
        )
        items.append(
            {
                "status": "ok",
                "audio_id": audio_path.stem,
                "text": transcript["text"],
                "latency_ms": latency_ms,
                "audio_duration_seconds": round(duration_seconds, 6),
                "rtf": round((latency_ms / 1000) / duration_seconds, 6) if duration_seconds else 0.0,
                "entities": [],
                "segments": transcript["segments"],
                "raw": transcript["raw"],
            }
        )

    return {
        "status": "ok",
        "batch_mode": "single_process_reused_funasr_model",
        "model_id": model_name,
        "model_resolution": "local_model_dir",
        "model_download_status": "not_performed",
        **hotword_config.raw_metadata(),
        "model_load_latency_ms": model_load_latency_ms,
        "item_count": len(items),
        "total_audio_duration_seconds": round(total_audio_duration_seconds, 6),
        "total_transcribe_latency_ms": total_transcribe_latency_ms,
        "transcribe_only_rtf": round((total_transcribe_latency_ms / 1000) / total_audio_duration_seconds, 6)
        if total_audio_duration_seconds
        else 0.0,
        "items": items,
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
    }


def transcribe_offline_batch(
    *,
    audio_paths: list[Path],
    model_name: str,
    vad_model: str | None = "fsmn-vad",
    punc_model: str | None = "ct-punc",
    device: str,
    batch_size_s: int = 60,
    merge_vad: bool = True,
    merge_length_s: int = 15,
) -> dict[str, Any]:
    """Transcribe files with one reused offline FunASR model.

    This is intended for imported recordings and post-meeting transcript repair,
    where accuracy and punctuation matter more than sub-second live latency.
    """
    model_started = time.monotonic()
    model_kwargs: dict[str, Any] = {
        "model": model_name,
        "device": device,
        "disable_update": True,
    }
    if vad_model:
        model_kwargs["vad_model"] = vad_model
    if punc_model:
        model_kwargs["punc_model"] = punc_model

    with contextlib.redirect_stdout(sys.stderr):
        from funasr import AutoModel

        model = AutoModel(**model_kwargs)

    model_load_latency_ms = int((time.monotonic() - model_started) * 1000)
    items: list[dict[str, Any]] = []
    total_audio_duration_seconds = 0.0
    total_transcribe_latency_ms = 0

    for audio_path in audio_paths:
        item_started = time.monotonic()
        with contextlib.redirect_stdout(sys.stderr):
            result = model.generate(
                input=str(audio_path),
                batch_size_s=batch_size_s,
                merge_vad=merge_vad,
                merge_length_s=merge_length_s,
            )
        latency_ms = int((time.monotonic() - item_started) * 1000)
        text = "".join(item.get("text", "") for item in result)
        segments = _segments_from_result(result)
        duration_seconds = _audio_duration_seconds(audio_path, segments)
        total_audio_duration_seconds += duration_seconds
        total_transcribe_latency_ms += latency_ms
        items.append(
            {
                "status": "ok",
                "audio_id": _audio_id_from_path(audio_path),
                "text": text,
                "latency_ms": latency_ms,
                "audio_duration_seconds": round(duration_seconds, 6),
                "rtf": round((latency_ms / 1000) / duration_seconds, 6) if duration_seconds else 0.0,
                "entities": [],
                "segments": segments,
                "raw": {
                    "provider": "funasr",
                    "model_id": _safe_model_id(model_name),
                    "model_resolution": "offline_model_argument",
                    "model_download_status": "not_performed",
                    "device": device,
                    "mode": "file_batch_offline_transcript",
                    "vad_model_status": "enabled" if vad_model else "disabled",
                    "punc_model_status": "enabled" if punc_model else "disabled",
                    "batch_model_load_excluded_from_latency": True,
                    "batch_size_s": batch_size_s,
                    "merge_vad": merge_vad,
                    "merge_length_s": merge_length_s,
                },
            }
        )

    return {
        "status": "ok",
        "batch_mode": "single_process_reused_funasr_offline_model",
        "model_id": _safe_model_id(model_name),
        "model_resolution": "offline_model_argument",
        "model_download_status": "not_performed",
        "vad_model_status": "enabled" if vad_model else "disabled",
        "punc_model_status": "enabled" if punc_model else "disabled",
        "model_load_latency_ms": model_load_latency_ms,
        "item_count": len(items),
        "total_audio_duration_seconds": round(total_audio_duration_seconds, 6),
        "total_transcribe_latency_ms": total_transcribe_latency_ms,
        "transcribe_only_rtf": round((total_transcribe_latency_ms / 1000) / total_audio_duration_seconds, 6)
        if total_audio_duration_seconds
        else 0.0,
        "items": items,
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
    }


def stream_events(
    audio_path: Path,
    model_name: str,
    local_model_dir: Path | None,
    device: str,
    chunk_size: list[int],
    encoder_chunk_look_back: int = 4,
    decoder_chunk_look_back: int = 1,
    final_window_ms: int = 3_000,
    hotwords: list[str] | None = None,
) -> list[StreamingTranscriptEvent]:
    started = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        model = _load_streaming_model(
            model_name=model_name,
            local_model_dir=local_model_dir,
            device=device,
        )
        events = _stream_events_with_loaded_model(
            model=model,
            audio_path=audio_path,
            chunk_size=chunk_size,
            encoder_chunk_look_back=encoder_chunk_look_back,
            decoder_chunk_look_back=decoder_chunk_look_back,
            final_window_ms=final_window_ms,
            started=started,
            hotwords=hotwords or [],
        )

    return events


def _load_streaming_model(
    *,
    model_name: str,
    local_model_dir: Path | None,
    device: str,
) -> Any:
    resolved_model = _resolve_offline_model(model_name, local_model_dir)
    from funasr import AutoModel

    return AutoModel(model=resolved_model, device=device, disable_update=True)


def _stream_events_with_loaded_model(
    *,
    model: Any,
    audio_path: Path,
    chunk_size: list[int],
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
    final_window_ms: int,
    started: float,
    hotwords: list[str],
) -> list[StreamingTranscriptEvent]:
    import numpy as np
    import soundfile as sf

    samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
    if sample_rate != 16_000:
        raise ValueError("FunASR streaming file replay requires 16kHz mono PCM input")
    if getattr(samples, "ndim", 1) > 1:
        samples = samples[:, 0]
    samples = np.ascontiguousarray(samples)

    chunk_stride = max(1, chunk_size[1] * 960)
    chunk_count = (len(samples) - 1) // chunk_stride + 1 if len(samples) else 0
    cache: dict[str, Any] = {}
    events: list[StreamingTranscriptEvent] = []
    segment_index = 1
    segment_start_sample = 0
    segment_hypothesis = ""
    last_partial_text = ""

    for chunk_index in range(chunk_count):
        chunk_start = chunk_index * chunk_stride
        chunk_end = min(chunk_start + chunk_stride, len(samples))
        chunk = samples[chunk_start:chunk_end]
        is_final_chunk = chunk_index == chunk_count - 1
        generate_kwargs: dict[str, Any] = {
            "input": chunk,
            "cache": cache,
            "is_final": is_final_chunk,
            "chunk_size": chunk_size,
            "encoder_chunk_look_back": encoder_chunk_look_back,
            "decoder_chunk_look_back": decoder_chunk_look_back,
        }
        if hotwords:
            generate_kwargs["hotword"] = hotwords
            generate_kwargs["hotwords"] = hotwords
        result = model.generate(**generate_kwargs)
        partial_text = _result_text(result)
        if partial_text and partial_text != last_partial_text:
            segment_hypothesis = _merge_partial_hypothesis(
                previous=segment_hypothesis,
                current=partial_text,
            )
            events.append(
                _event_from_samples(
                    event_type="partial",
                    segment_id=f"funasr_{segment_index:03d}",
                    text=partial_text,
                    start_sample=segment_start_sample,
                    end_sample=chunk_end,
                    sample_rate=sample_rate,
                    started=started,
                )
            )
            last_partial_text = partial_text

        if _should_emit_window_final(
            chunk_end=chunk_end,
            segment_start_sample=segment_start_sample,
            sample_rate=sample_rate,
            final_window_ms=final_window_ms,
            is_final_chunk=is_final_chunk,
        ):
            final_text = segment_hypothesis.strip()
            if final_text:
                events.append(
                    _event_from_samples(
                        event_type="final",
                        segment_id=f"funasr_{segment_index:03d}",
                        text=final_text,
                        start_sample=segment_start_sample,
                        end_sample=chunk_end,
                        sample_rate=sample_rate,
                        started=started,
                    )
                )
                segment_index += 1
            segment_start_sample = chunk_end
            segment_hypothesis = ""
            last_partial_text = ""

    final_ms = _sample_to_ms(len(samples) if "samples" in locals() else 0, sample_rate if "sample_rate" in locals() else 0)
    events.append(
        StreamingTranscriptEvent(
            event_type="end_of_stream",
            segment_id="funasr_eos",
            text="",
            start_ms=final_ms,
            end_ms=final_ms,
            received_at_ms=_elapsed_ms(started),
        )
    )
    return events


def load_hotword_manifest(path: Path | None) -> HotwordConfig:
    if path is None:
        return HotwordConfig.disabled()

    path_errors = _hotword_manifest_path_errors(path)
    if path_errors:
        raise OfflineModelGuardError(
            "FunASR hotword manifest failed path validation",
            path_errors,
            "blocked_invalid_hotword_manifest",
        )

    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise OfflineModelGuardError(
            "FunASR hotword manifest is missing",
            ["hotword manifest path does not exist"],
            "blocked_invalid_hotword_manifest",
        ) from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise OfflineModelGuardError(
            "FunASR hotword manifest must contain valid JSON",
            ["hotword manifest must contain valid JSON"],
            "blocked_invalid_hotword_manifest",
        ) from exc

    words = _extract_hotwords(payload)
    if not words:
        raise OfflineModelGuardError(
            "FunASR hotword manifest contains no hotwords",
            ["hotword manifest must contain at least one non-empty string"],
            "blocked_invalid_hotword_manifest",
        )
    return HotwordConfig(
        words=words,
        status="enabled",
        count=len(words),
        sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )


def _extract_hotwords(payload: Any) -> list[str]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict) and isinstance(payload.get("hotwords"), list):
        candidates = payload["hotwords"]
    elif isinstance(payload, dict) and isinstance(payload.get("terms"), list):
        candidates = [
            item.get("canonical")
            for item in payload["terms"]
            if isinstance(item, dict)
        ]
    else:
        candidates = []

    words: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        word = str(item).strip() if isinstance(item, str) else ""
        if not word or len(word) > 80:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        words.append(word)
    return words[:128]


def _hotword_manifest_path_errors(path: Path) -> list[str]:
    errors: list[str] = []
    for candidate in (path, path.resolve(strict=False)):
        if candidate.suffix.casefold() == ".m4a":
            errors.append("hotword manifest path is blocked: audio file")
        if _is_forbidden_repo_path(candidate):
            errors.append("hotword manifest path is forbidden")
    relative = _repo_relative_path(path)
    resolved_relative = _repo_relative_path(path.resolve(strict=False))
    if relative is None or resolved_relative is None:
        errors.append("hotword manifest path must be inside repository")
        return errors
    if not _is_under_any_repo_root(relative, ALLOWED_HOTWORD_MANIFEST_REPO_ROOTS):
        errors.append("hotword manifest path is not under an approved hotword root")
    if not _is_under_any_repo_root(resolved_relative, ALLOWED_HOTWORD_MANIFEST_REPO_ROOTS):
        errors.append("hotword manifest resolved path is not under an approved hotword root")
    if path.suffix.casefold() != ".json":
        errors.append("hotword manifest path must be a JSON file")
    return errors


def _repo_relative_path(path: Path) -> Path | None:
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    try:
        return resolved.relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_any_repo_root(relative: Path, roots: tuple[Path, ...]) -> bool:
    return any(relative == root or root in relative.parents for root in roots)


def _resolve_offline_model(model_name: str, local_model_dir: Path | None) -> str:
    if local_model_dir is None:
        raise OfflineModelGuardError(
            "FunASR streaming requires an explicit local model dir in offline-only mode",
            ["local model dir is required for FunASR streaming offline execution"],
            "blocked_missing_local_model_dir",
        )

    validation_errors = _validate_local_model_dir(local_model_dir)
    if validation_errors:
        raise OfflineModelGuardError(
            "FunASR streaming local model dir failed offline guard validation",
            validation_errors,
            "blocked_invalid_local_model_dir",
        )
    return str(local_model_dir)


def _validate_local_model_dir(local_model_dir: Path) -> list[str]:
    errors: list[str] = []
    if not local_model_dir.is_absolute():
        errors.append("local model dir must be an absolute path")
    if _is_forbidden_repo_path(local_model_dir):
        errors.append("local model dir is under a forbidden project root")
    if not local_model_dir.is_dir():
        errors.append("local model dir is missing")
        return errors
    for filename in REQUIRED_LOCAL_MODEL_FILES:
        if not (local_model_dir / filename).is_file():
            errors.append(f"local model dir is missing required file: {filename}")
    return errors


def _is_forbidden_repo_path(path: Path) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    return any(relative == root or root in relative.parents for root in FORBIDDEN_LOCAL_MODEL_REPO_ROOTS)


def _blocked_streaming_result(
    *,
    model_name: str,
    device: str,
    chunk_size: list[int],
    encoder_chunk_look_back: int,
    decoder_chunk_look_back: int,
    final_window_ms: int,
    latency_ms: int,
    guard_error: OfflineModelGuardError,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "provider": "funasr",
        "model_id": model_name,
        "model_resolution": "local_model_dir",
        "model_resolution_status": guard_error.model_resolution_status,
        "model_download_status": "blocked_or_not_started",
        "device": device,
        "chunk_size": chunk_size,
        "encoder_chunk_look_back": encoder_chunk_look_back,
        "decoder_chunk_look_back": decoder_chunk_look_back,
        "final_window_ms": final_window_ms,
        "text": "",
        "latency_ms": latency_ms,
        "audio_duration_seconds": 0.0,
        "rtf": 0.0,
        "entities": [],
        "segments": [],
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "validation_errors": guard_error.validation_errors,
        "raw": {
            "provider": "funasr",
            "model_id": model_name,
            "model_resolution": "local_model_dir",
            "model_resolution_status": guard_error.model_resolution_status,
            "model_download_status": "blocked_or_not_started",
            "safe_to_download_models": False,
            "path_redaction": "local_model_dir_not_reported",
        },
    }


def _result_text(result: Any) -> str:
    if not result:
        return ""
    if isinstance(result, list):
        return "".join(str(item.get("text", "")) for item in result if isinstance(item, dict)).strip()
    if isinstance(result, dict):
        return str(result.get("text", "")).strip()
    return ""


def _merge_partial_hypothesis(previous: str, current: str) -> str:
    previous = previous.strip()
    current = current.strip()
    if not previous:
        return current
    if not current:
        return previous
    if current.startswith(previous):
        return current
    overlap = _suffix_prefix_overlap(previous, current)
    if overlap:
        return previous + current[overlap:]
    if _common_prefix_length(previous, current) > 0 and len(current) >= len(previous):
        return current
    return previous + current


def _suffix_prefix_overlap(previous: str, current: str) -> int:
    max_overlap = min(len(previous), len(current))
    for size in range(max_overlap, 0, -1):
        if previous[-size:] == current[:size]:
            return size
    return 0


def _common_prefix_length(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def _should_emit_window_final(
    chunk_end: int,
    segment_start_sample: int,
    sample_rate: int,
    final_window_ms: int,
    is_final_chunk: bool,
) -> bool:
    if is_final_chunk:
        return True
    if final_window_ms <= 0:
        return False
    elapsed_ms = _sample_to_ms(chunk_end - segment_start_sample, sample_rate)
    return elapsed_ms >= final_window_ms


def _sample_to_ms(sample_index: int, sample_rate: int) -> int:
    return int((sample_index / sample_rate) * 1000) if sample_rate else 0


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _event_from_samples(
    event_type: str,
    segment_id: str,
    text: str,
    start_sample: int,
    end_sample: int,
    sample_rate: int,
    started: float,
) -> StreamingTranscriptEvent:
    return StreamingTranscriptEvent(
        event_type=event_type,
        segment_id=segment_id,
        text=text,
        start_ms=_sample_to_ms(start_sample, sample_rate),
        end_ms=_sample_to_ms(end_sample, sample_rate),
        received_at_ms=_elapsed_ms(started),
    )


def _parse_chunk_size(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError("chunk size must use the form left,current,right, for example 0,10,5")
    try:
        parsed = [int(part) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("chunk size values must be integers") from exc
    if parsed[1] <= 0:
        raise argparse.ArgumentTypeError("current chunk size must be positive")
    return parsed


def _segments_from_result(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for index, item in enumerate(result, start=1):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start_ms = 0
        end_ms = 0
        timestamps = item.get("timestamp") or []
        if timestamps:
            start_ms = int(timestamps[0][0])
            end_ms = int(timestamps[-1][-1])
        segments.append(
            {
                "id": f"funasr_{index:03d}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "is_final": True,
            }
        )
    return segments


def _audio_duration_seconds(audio_path: Path, segments: list[dict[str, Any]]) -> float:
    try:
        import soundfile as sf

        info = sf.info(str(audio_path))
        if info.samplerate:
            return float(info.frames) / float(info.samplerate)
    except Exception:
        pass
    return max((float(segment.get("end_ms") or 0) for segment in segments), default=0.0) / 1000.0


def _audio_id_from_path(audio_path: Path) -> str:
    stem = audio_path.stem
    return stem.removesuffix(".16k")


def _safe_model_id(model_id: str) -> str:
    value = str(model_id)
    if "/" in value or "\\" in value:
        return Path(value).name
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transcribe audio with local FunASR.")
    parser.add_argument("audio", type=Path, nargs="+")
    parser.add_argument("--model", default="paraformer-zh")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--punc-model", default="ct-punc")
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Replay the file through FunASR streaming chunks and emit streaming events.",
    )
    parser.add_argument(
        "--offline-batch",
        action="store_true",
        help="Load one offline FunASR model and transcribe all input files for post-meeting transcripts.",
    )
    parser.add_argument(
        "--chunk-size",
        type=_parse_chunk_size,
        default=[0, 10, 5],
        help="FunASR streaming chunk size as left,current,right. Default: 0,10,5.",
    )
    parser.add_argument("--encoder-chunk-look-back", type=int, default=4)
    parser.add_argument("--decoder-chunk-look-back", type=int, default=1)
    parser.add_argument(
        "--final-window-ms",
        type=int,
        default=3_000,
        help="Emit a formal final segment after this much replayed audio; 0 disables window finals.",
    )
    parser.add_argument("--events-output", type=Path)
    parser.add_argument(
        "--local-model-dir",
        type=Path,
        help="Required for --streaming offline-only execution; passed to FunASR instead of a model alias.",
    )
    parser.add_argument(
        "--hotword-manifest",
        type=Path,
        help="Optional approved JSON manifest of technical hotwords for FunASR streaming.",
    )
    parser.add_argument(
        "--no-punc",
        action="store_true",
        help="Disable punctuation model loading for a lighter first-pass run.",
    )
    args = parser.parse_args(argv)

    if args.offline_batch:
        punc_model = None if args.no_punc else args.punc_model
        print(
            json.dumps(
                transcribe_offline_batch(
                    audio_paths=args.audio,
                    model_name=args.model,
                    vad_model=args.vad_model,
                    punc_model=punc_model,
                    device=args.device,
                ),
                ensure_ascii=False,
            )
        )
        return

    if args.streaming:
        if len(args.audio) != 1:
            parser.error("--streaming accepts exactly one audio input")
        print(
            json.dumps(
                transcribe_streaming(
                    args.audio[0],
                    args.model,
                    args.device,
                    chunk_size=args.chunk_size,
                    local_model_dir=args.local_model_dir,
                    encoder_chunk_look_back=args.encoder_chunk_look_back,
                    decoder_chunk_look_back=args.decoder_chunk_look_back,
                    final_window_ms=args.final_window_ms,
                    events_output=args.events_output,
                    hotword_manifest_path=args.hotword_manifest,
                ),
                ensure_ascii=False,
            )
        )
        return

    if len(args.audio) != 1:
        parser.error("single-file transcription accepts exactly one audio input; use --offline-batch for multiple files")
    punc_model = None if args.no_punc else args.punc_model
    print(
        json.dumps(
            transcribe(args.audio[0], args.model, args.device, punc_model=punc_model),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
