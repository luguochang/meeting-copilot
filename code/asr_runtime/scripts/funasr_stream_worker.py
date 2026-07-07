"""FunASR streaming ASR sidecar worker (subprocess, funasr 3.11 venv).

Reads 16kHz mono float32 PCM chunks from stdin, runs FunASR paraformer-streaming
incrementally (with cache), emits JSON partial/final events to stdout. On EOF,
finalizes. Used by FunasrSidecarRecognizer for the real-time-meeting use case
(G2) — FunASR streaming has better Chinese accuracy than sherpa for real-time.
"""
import argparse
import contextlib
import json
import sys
from pathlib import Path

_REAL_STDOUT = sys.stdout


def _emit(event_type: str, text: str, idx: int) -> None:
    _REAL_STDOUT.write(
        json.dumps(
            {"event_type": event_type, "segment_id": f"funasr_sc_{idx:03d}", "text": text, "sample_rate": 16000},
            ensure_ascii=False,
        )
        + "\n"
    )
    _REAL_STDOUT.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description="FunASR streaming ASR sidecar.")
    ap.add_argument("--model", default="paraformer-zh-streaming")
    ap.add_argument("--hotwords", default="")
    args = ap.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        from funasr import AutoModel

        model = AutoModel(
            model=args.model,
            device="cpu",
            disable_update=True,
            chunk_size=[0, 10, 5],
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
        )
        hotwords = [w for w in args.hotwords.split() if w]
        chunk_size = [0, 10, 5]
        chunk_stride = max(1, chunk_size[1] * 960)  # 9600 samples per chunk
        cache: dict = {}
        seg = 1
        last_text = ""
        last_chunk = None
        stdin = sys.stdin.buffer
        while True:
            data = stdin.read(chunk_stride * 4)  # float32 = 4 bytes
            if not data:
                break
            chunk = np.frombuffer(data, dtype="float32")
            last_chunk = chunk
            kw = {
                "input": chunk,
                "cache": cache,
                "is_final": False,
                "chunk_size": chunk_size,
                "encoder_chunk_look_back": 4,
                "decoder_chunk_look_back": 1,
            }
            if hotwords:
                kw["hotword"] = hotwords
            res = model.generate(**kw)
            text = "".join(r.get("text", "") for r in res).strip()
            if text and text != last_text:
                _emit("partial", text, seg)
                last_text = text
        # finalize: the is_final generate call is unreliable on long audio (produces
        # garbage); use the last partial (accumulated hypothesis) as the final text.
        if last_text:
            _emit("final", last_text, seg)


if __name__ == "__main__":
    main()
