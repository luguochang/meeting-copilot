"""Batch transcribe an audio file via FunASR (subprocess in the funasr 3.11 venv).

For the meeting-recording-file-conversion use case (G1): upload a recorded
meeting audio file -> FunASR batch transcribe (more accurate than streaming
sherpa) -> text. The web backend (3.14) cannot import funasr directly, so it
spawns funasr_batch_worker.py in the funasr venv as a subprocess.

L0 audio preprocessing: ffmpeg converts MP4/MP3/M4A etc to 16kHz mono WAV
before passing to FunASR.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.batch_transcribe")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FUNASR_PY = _REPO_ROOT / "code" / "asr_runtime" / ".venv-funasr" / "bin" / "python"
_WORKER = _REPO_ROOT / "code" / "asr_runtime" / "scripts" / "funasr_batch_worker.py"
_TRANSCRIBE_WORKER = _REPO_ROOT / "code" / "asr_runtime" / "scripts" / "transcribe_funasr.py"
_MODELSCOPE_IIC = Path.home() / ".cache" / "modelscope" / "hub" / "models" / "iic"
_OFFLINE_MODEL_DIR = _MODELSCOPE_IIC / "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
_VAD_MODEL_DIR = _MODELSCOPE_IIC / "speech_fsmn_vad_zh-cn-16k-common-pytorch"
_PUNC_MODEL_DIR = _MODELSCOPE_IIC / "punc_ct-transformer_cn-en-common-vocab471067-large"

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv"}
_AUDIO_NEEDS_CONVERT = {".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".opus"}
_ffmpeg_path: str | None = None


def _get_ffmpeg() -> str | None:
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path
    try:
        import imageio_ffmpeg
        _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        _log.info("ffmpeg.resolved", path=_ffmpeg_path)
        return _ffmpeg_path
    except ImportError:
        _log.warning("ffmpeg.imageio_not_installed")
    except Exception as exc:
        _log.warning("ffmpeg.resolve_failed", error=str(exc))
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        _ffmpeg_path = system_ffmpeg
        _log.info("ffmpeg.resolved_system_binary", path=system_ffmpeg)
        return _ffmpeg_path
    return None


def ensure_wav_16k_mono(input_path: Path) -> Path:
    """Convert any audio/video file to 16kHz mono WAV using ffmpeg.

    All files (including .wav) are converted to ensure correct sample rate
    and channel count for FunASR's 16kHz mono requirement.
    """
    suffix = input_path.suffix.lower()
    if suffix not in _VIDEO_EXTENSIONS and suffix not in _AUDIO_NEEDS_CONVERT and suffix != ".wav":
        return input_path  # unknown format, let FunASR handle it

    ffmpeg = _get_ffmpeg()
    if ffmpeg is None:
        if suffix == ".wav":
            return input_path
        raise RuntimeError(
            f"文件格式 {suffix} 需要ffmpeg转换，但ffmpeg未安装。"
            "请运行 pip install imageio-ffmpeg 安装。"
        )
    output_path = input_path.with_suffix(".16k.wav")
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", str(input_path), "-vn", "-ar", "16000",
             "-ac", "1", "-f", "wav", "-y", str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg转码失败: {proc.stderr[-400:]}")
        _log.info("ffmpeg.converted", input=str(input_path), output=str(output_path))
        return output_path
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg转码超时（超过120秒）")


def is_available() -> bool:
    return (
        _FUNASR_PY.is_file()
        and _TRANSCRIBE_WORKER.is_file()
        and Path(_resolve_offline_model_arg()).is_dir()
        and Path(_resolve_vad_model_arg()).is_dir()
        and Path(_resolve_punc_model_arg()).is_dir()
    )


def transcribe_file_report(audio_path: Path, timeout: int = 180) -> dict[str, Any]:
    if not is_available():
        raise RuntimeError("FunASR offline batch path not ready - file conversion unavailable")
    wav_path = ensure_wav_16k_mono(audio_path)
    _log.info("batch.transcribe.start", audio=str(wav_path), original=str(audio_path))
    proc = subprocess.run(
        [str(_FUNASR_PY), str(_TRANSCRIBE_WORKER), str(wav_path),
         "--offline-batch", "--model", _resolve_offline_model_arg(),
         "--vad-model", _resolve_vad_model_arg(),
         "--punc-model", _resolve_punc_model_arg()],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        _log.error("batch.transcribe.failed", returncode=proc.returncode, stderr=proc.stderr[-400:])
        raise RuntimeError(f"FunASR offline batch transcribe failed: {proc.stderr[-300:]}")
    payload = json.loads(proc.stdout)
    items = list(payload.get("items") or [])
    if not items:
        raise RuntimeError("FunASR offline batch transcribe returned no items")
    item = dict(items[0])
    batch = {key: value for key, value in payload.items() if key not in {"items"}}
    item["batch"] = batch
    _log.info("batch.transcribe.end", chars=len(str(item.get("text") or "")))
    if wav_path != audio_path:
        wav_path.unlink(missing_ok=True)
    return item


def transcribe_file(audio_path: Path, timeout: int = 180) -> str:
    return str(transcribe_file_report(audio_path, timeout=timeout).get("text") or "")


def _resolve_offline_model_arg() -> str:
    return str(_OFFLINE_MODEL_DIR)


def _resolve_vad_model_arg() -> str:
    return str(_VAD_MODEL_DIR)


def _resolve_punc_model_arg() -> str:
    return str(_PUNC_MODEL_DIR)
