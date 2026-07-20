from __future__ import annotations

import struct

import pytest

from meeting_copilot_web_mvp.native_pcm_protocol import (
    HEADER_SIZE,
    NativePcmProtocolError,
    NativePcmV2Decoder,
    encode_native_pcm_v2_frame,
)


def _pcm(sample: float = 0.125, samples: int = 4_800) -> bytes:
    return struct.pack("<f", sample) * samples


def test_v2_decoder_returns_validated_pcm_and_transport_identity() -> None:
    decoder = NativePcmV2Decoder(
        expected_track_id="system_audio",
        expected_capture_epoch=7,
    )

    decoded = decoder.decode(
        encode_native_pcm_v2_frame(
            track_id="system_audio",
            capture_epoch=7,
            sequence=1,
            timestamp_ms=12_345,
            pcm=_pcm(),
        )
    )

    assert decoded.payload == _pcm()
    assert decoded.track_id == "system_audio"
    assert decoded.capture_epoch == 7
    assert decoded.sequence == 1
    assert decoded.timestamp_ms == 12_345
    assert decoded.sample_rate_hz == 16_000
    assert decoded.final_partial is False
    assert HEADER_SIZE == 44


def test_v2_decoder_allows_one_aligned_final_partial_frame() -> None:
    decoder = NativePcmV2Decoder(
        expected_track_id="microphone",
        expected_capture_epoch=3,
    )

    decoded = decoder.decode(
        encode_native_pcm_v2_frame(
            track_id="microphone",
            capture_epoch=3,
            sequence=1,
            timestamp_ms=1_000,
            pcm=_pcm(samples=800),
            final_partial=True,
        )
    )

    assert decoded.final_partial is True
    assert len(decoded.payload) == 3_200


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("track_id", "microphone", "native_pcm_track_mismatch"),
        ("capture_epoch", 8, "native_pcm_epoch_mismatch"),
        ("sequence", 0, "native_pcm_sequence_invalid"),
        ("sample_rate_hz", 48_000, "native_pcm_sample_rate_invalid"),
    ],
)
def test_v2_decoder_rejects_identity_and_format_mismatch(
    field: str,
    value: object,
    code: str,
) -> None:
    decoder = NativePcmV2Decoder(
        expected_track_id="system_audio",
        expected_capture_epoch=7,
    )
    values = {
        "track_id": "system_audio",
        "capture_epoch": 7,
        "sequence": 1,
        "timestamp_ms": 1_000,
        "sample_rate_hz": 16_000,
        "pcm": _pcm(),
    }
    values[field] = value

    with pytest.raises(NativePcmProtocolError) as raised:
        decoder.decode(encode_native_pcm_v2_frame(**values))

    assert raised.value.code == code


def test_v2_decoder_rejects_duplicate_gap_and_reversed_timestamp() -> None:
    decoder = NativePcmV2Decoder(
        expected_track_id="system_audio",
        expected_capture_epoch=7,
    )
    first = encode_native_pcm_v2_frame(
        track_id="system_audio",
        capture_epoch=7,
        sequence=1,
        timestamp_ms=2_000,
        pcm=_pcm(),
    )
    decoder.decode(first)

    with pytest.raises(NativePcmProtocolError) as duplicate:
        decoder.decode(first)
    assert duplicate.value.code == "native_pcm_sequence_invalid"

    with pytest.raises(NativePcmProtocolError) as gap:
        decoder.decode(
            encode_native_pcm_v2_frame(
                track_id="system_audio",
                capture_epoch=7,
                sequence=3,
                timestamp_ms=2_100,
                pcm=_pcm(),
            )
        )
    assert gap.value.code == "native_pcm_sequence_invalid"

    decoder = NativePcmV2Decoder(
        expected_track_id="system_audio",
        expected_capture_epoch=7,
    )
    decoder.decode(first)
    with pytest.raises(NativePcmProtocolError) as reversed_timestamp:
        decoder.decode(
            encode_native_pcm_v2_frame(
                track_id="system_audio",
                capture_epoch=7,
                sequence=2,
                timestamp_ms=1_999,
                pcm=_pcm(),
            )
        )
    assert reversed_timestamp.value.code == "native_pcm_timestamp_invalid"


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda frame: b"BADMAGIC" + frame[8:], "native_pcm_magic_invalid"),
        (lambda frame: frame[:8] + b"\x03" + frame[9:], "native_pcm_version_invalid"),
        (lambda frame: frame[:-4], "native_pcm_length_invalid"),
        (
            lambda frame: frame[:10] + (2).to_bytes(2, "big") + frame[12:],
            "native_pcm_flags_invalid",
        ),
    ],
)
def test_v2_decoder_rejects_corrupt_envelopes(mutator, code: str) -> None:
    decoder = NativePcmV2Decoder(
        expected_track_id="system_audio",
        expected_capture_epoch=7,
    )
    frame = encode_native_pcm_v2_frame(
        track_id="system_audio",
        capture_epoch=7,
        sequence=1,
        timestamp_ms=1_000,
        pcm=_pcm(),
    )

    with pytest.raises(NativePcmProtocolError) as raised:
        decoder.decode(mutator(frame))

    assert raised.value.code == code


def test_encoder_and_decoder_reject_non_finite_or_oversized_pcm() -> None:
    with pytest.raises(NativePcmProtocolError) as non_finite:
        encode_native_pcm_v2_frame(
            track_id="microphone",
            capture_epoch=1,
            sequence=1,
            timestamp_ms=0,
            pcm=struct.pack("<f", float("nan")),
            final_partial=True,
        )
    assert non_finite.value.code == "audio_payload_non_finite"

    with pytest.raises(NativePcmProtocolError) as oversized:
        encode_native_pcm_v2_frame(
            track_id="microphone",
            capture_epoch=1,
            sequence=1,
            timestamp_ms=0,
            pcm=_pcm(samples=4_801),
        )
    assert oversized.value.code == "native_pcm_length_invalid"
