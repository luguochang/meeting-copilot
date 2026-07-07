"""FunASR batch transcribe worker (subprocess, runs in funasr 3.11 venv).

Reads --audio, prints JSON {"text": ...} to stdout. Used by the web backend's
batch_transcribe service for the meeting-recording-file-conversion use case
(G1). FunASR batch (paraformer-zh) is more accurate than streaming sherpa
(recall 0.86 vs 0.43 on clean audio) — best for offline file conversion.
"""
import argparse
import contextlib
import json
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="FunASR batch transcribe worker.")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--model", default="paraformer-zh")
    args = ap.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        from funasr import AutoModel  # noqa: delayed import (slow model load)

        model = AutoModel(
            model=args.model,
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device="cpu",
            disable_update=True,
        )
        result = model.generate(
            input=str(args.audio),
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        text = "".join(item.get("text", "") for item in result)
    sys.stdout.write(json.dumps({"text": text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
