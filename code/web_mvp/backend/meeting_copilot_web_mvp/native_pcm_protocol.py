from __future__ import annotations

from dataclasses import dataclass
import struct

from meeting_copilot_web_mvp.audio_assets import (
    Float32PcmPayloadError,
    validate_float32_pcm_payload,
)


PROTOCOL_NAME = "native_pcm_v2"
MAGIC = b"MCPCM2\0\0"
VERSION = 2
SAMPLE_RATE_HZ = 16_000
MAX_PCM_BYTES = 4_800 * 4
FLAG_FINAL_PARTIAL = 0x0001
_ALLOWED_FLAGS = FLAG_FINAL_PARTIAL
_TRACK_TO_CODE = {"microphone": 1, "system_audio": 2}
_CODE_TO_TRACK = {value: key for key, value in _TRACK_TO_CODE.items()}
_HEADER = struct.Struct(">8sBBHQQQII")
HEADER_SIZE = _HEADER.size


class NativePcmProtocolError(Float32PcmPayloadError):
    """A native PCM envelope violated its authenticated stream contract."""


@dataclass(frozen=True)
class NativePcmFrame:
    payload: bytes
    track_id: str
    capture_epoch: int
    sequence: int
    timestamp_ms: int
    sample_rate_hz: int
    final_partial: bool


class NativePcmV2Decoder:
    def __init__(self, *, expected_track_id: str, expected_capture_epoch: int) -> None:
        if expected_track_id not in _TRACK_TO_CODE:
            raise ValueError("expected_track_id must be microphone or system_audio")
        expected_capture_epoch = int(expected_capture_epoch)
        if expected_capture_epoch <= 0:
            raise ValueError("expected_capture_epoch must be positive")
        self._expected_track_id = expected_track_id
        self._expected_capture_epoch = expected_capture_epoch
        self._last_sequence = 0
        self._last_timestamp_ms: int | None = None

    def decode(self, envelope: bytes | bytearray | memoryview) -> NativePcmFrame:
        if not isinstance(envelope, (bytes, bytearray, memoryview)):
            raise NativePcmProtocolError(
                "native_pcm_type_invalid",
                "本地音频传输格式无效，请重新开始会议。",
            )
        raw = bytes(envelope)
        if len(raw) < HEADER_SIZE:
            raise NativePcmProtocolError(
                "native_pcm_length_invalid",
                "本地音频帧不完整，请重新开始会议。",
            )
        (
            magic,
            version,
            track_code,
            flags,
            capture_epoch,
            sequence,
            timestamp_ms,
            sample_rate_hz,
            payload_length,
        ) = _HEADER.unpack_from(raw)
        if magic != MAGIC:
            raise NativePcmProtocolError(
                "native_pcm_magic_invalid",
                "本地音频协议不匹配，请更新或重新启动客户端。",
            )
        if version != VERSION:
            raise NativePcmProtocolError(
                "native_pcm_version_invalid",
                "本地音频协议版本不受支持，请更新客户端。",
            )
        track_id = _CODE_TO_TRACK.get(track_code)
        if track_id != self._expected_track_id:
            raise NativePcmProtocolError(
                "native_pcm_track_mismatch",
                "本地音频轨道身份不一致，采集已停止。",
            )
        if capture_epoch != self._expected_capture_epoch:
            raise NativePcmProtocolError(
                "native_pcm_epoch_mismatch",
                "本地音频采集批次不一致，采集已停止。",
            )
        if flags & ~_ALLOWED_FLAGS:
            raise NativePcmProtocolError(
                "native_pcm_flags_invalid",
                "本地音频帧包含未知标记，请更新客户端。",
            )
        expected_sequence = self._last_sequence + 1
        if sequence != expected_sequence:
            raise NativePcmProtocolError(
                "native_pcm_sequence_invalid",
                "本地音频帧发生重复或丢失，采集已停止并保留已有录音。",
            )
        if self._last_timestamp_ms is not None and timestamp_ms < self._last_timestamp_ms:
            raise NativePcmProtocolError(
                "native_pcm_timestamp_invalid",
                "本地音频时间戳倒退，采集已停止并保留已有录音。",
            )
        if sample_rate_hz != SAMPLE_RATE_HZ:
            raise NativePcmProtocolError(
                "native_pcm_sample_rate_invalid",
                "本地音频采样率不受支持，请重新连接音频设备。",
            )
        payload = raw[HEADER_SIZE:]
        final_partial = bool(flags & FLAG_FINAL_PARTIAL)
        _validate_payload_length(payload, payload_length, final_partial=final_partial)
        try:
            payload = validate_float32_pcm_payload(payload)
        except Float32PcmPayloadError as exc:
            raise NativePcmProtocolError(exc.code, exc.user_message) from exc

        self._last_sequence = sequence
        self._last_timestamp_ms = timestamp_ms
        return NativePcmFrame(
            payload=payload,
            track_id=track_id,
            capture_epoch=capture_epoch,
            sequence=sequence,
            timestamp_ms=timestamp_ms,
            sample_rate_hz=sample_rate_hz,
            final_partial=final_partial,
        )


def encode_native_pcm_v2_frame(
    *,
    track_id: str,
    capture_epoch: int,
    sequence: int,
    timestamp_ms: int,
    pcm: bytes | bytearray | memoryview,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
    final_partial: bool = False,
) -> bytes:
    track_code = _TRACK_TO_CODE.get(track_id)
    if track_code is None:
        raise NativePcmProtocolError(
            "native_pcm_track_mismatch",
            "本地音频轨道身份无效。",
        )
    capture_epoch = int(capture_epoch)
    sequence = int(sequence)
    timestamp_ms = int(timestamp_ms)
    sample_rate_hz = int(sample_rate_hz)
    if capture_epoch <= 0:
        raise NativePcmProtocolError(
            "native_pcm_epoch_mismatch",
            "本地音频采集批次无效。",
        )
    if sequence <= 0:
        raise NativePcmProtocolError(
            "native_pcm_sequence_invalid",
            "本地音频帧序号无效。",
        )
    if timestamp_ms < 0:
        raise NativePcmProtocolError(
            "native_pcm_timestamp_invalid",
            "本地音频时间戳无效。",
        )
    if sample_rate_hz != SAMPLE_RATE_HZ:
        raise NativePcmProtocolError(
            "native_pcm_sample_rate_invalid",
            "本地音频采样率不受支持。",
        )
    try:
        payload = validate_float32_pcm_payload(pcm)
    except Float32PcmPayloadError as exc:
        raise NativePcmProtocolError(exc.code, exc.user_message) from exc
    _validate_payload_length(payload, len(payload), final_partial=final_partial)
    flags = FLAG_FINAL_PARTIAL if final_partial else 0
    return _HEADER.pack(
        MAGIC,
        VERSION,
        track_code,
        flags,
        capture_epoch,
        sequence,
        timestamp_ms,
        sample_rate_hz,
        len(payload),
    ) + payload


def _validate_payload_length(
    payload: bytes,
    declared_length: int,
    *,
    final_partial: bool,
) -> None:
    if declared_length != len(payload) or not payload or len(payload) % 4:
        raise NativePcmProtocolError(
            "native_pcm_length_invalid",
            "本地音频帧长度无效，请重新开始会议。",
        )
    if len(payload) > MAX_PCM_BYTES:
        raise NativePcmProtocolError(
            "native_pcm_length_invalid",
            "本地音频帧超过长度限制，请更新客户端。",
        )
    if not final_partial and len(payload) != MAX_PCM_BYTES:
        raise NativePcmProtocolError(
            "native_pcm_length_invalid",
            "本地音频完整帧长度无效，请更新客户端。",
        )
