"""Sherpa-onnx streaming ASR sidecar worker (Phase 4).

Reads 16kHz mono float32 PCM chunks from stdin (binary), runs sherpa-onnx
OnlineRecognizer incrementally, emits one JSON ASR event per line to stdout
(partial / final). On EOF, finalizes and exits.

This is the ASR sidecar: it runs in the sherpa-onnx Python 3.11 venv (separate
from the 3.14 web backend) and is spawned as a subprocess by the web backend's
SherpaSidecarRecognizer. PCM in -> ASR events out, over stdio.

Run:
  code/asr_runtime/.venv-sherpa/bin/python sherpa_stream_worker.py --model-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_model(model_dir: Path) -> Path:
    for p in Path(model_dir).glob("*.onnx"):
        return p
    raise FileNotFoundError(f"no .onnx model file in {model_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sherpa-onnx streaming ASR sidecar.")
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--sample-rate", type=int, default=16000)
    ap.add_argument("--chunk-ms", type=int, default=300)
    args = ap.parse_args()

    import numpy as np  # noqa: F401
    import sherpa_onnx

    model_path = _find_model(args.model_dir)
    tokens_path = args.model_dir / "tokens.txt"
    recognizer = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
        tokens=str(tokens_path),
        model=str(model_path),
        num_threads=2,
        provider="cpu",
        enable_endpoint_detection=True,
    )
    stream = recognizer.create_stream()
    sr = args.sample_rate
    chunk_samples = max(1, int(sr * args.chunk_ms / 1000))
    seg = 1
    last_partial = ""

    def emit(event_type: str, text: str, seg_idx: int) -> None:
        sys.stdout.write(
            json.dumps(
                {
                    "event_type": event_type,
                    "segment_id": f"sherpa_sidecar_{seg_idx:03d}",
                    "text": text,
                    "sample_rate": sr,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        sys.stdout.flush()

    stdin = sys.stdin.buffer
    while True:
        data = stdin.read(chunk_samples * 4)  # float32 = 4 bytes/sample
        if not data:
            break
        chunk = __import__("numpy").frombuffer(data, dtype="float32")
        stream.accept_waveform(sr, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result_all(stream)
        if recognizer.is_endpoint(stream):
            text = result.text.strip()
            if text:
                emit("final", text, seg)
                seg += 1
            recognizer.reset(stream)
            last_partial = ""
        else:
            text = result.text.strip()
            if text and text != last_partial:
                emit("partial", text, seg)
                last_partial = text

    # finalize on EOF
    import numpy as np
    tail = np.zeros(int(0.5 * sr), dtype=np.float32)
    stream.accept_waveform(sr, tail)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    text = recognizer.get_result_all(stream).text.strip()
    if text:
        emit("final", text, seg)


if __name__ == "__main__":
    main()
