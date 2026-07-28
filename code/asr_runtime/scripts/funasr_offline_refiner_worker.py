"""Process-resident local FunASR worker for realtime endpoint refinement.

The JSONL protocol accepts base64 PCM16 at 16 kHz. The model, VAD and
punctuation components are loaded once and reused for every completed speech
segment. Provider logs stay on stderr so stdout remains protocol-only.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _load_hotwords(path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("hotwords") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        raise ValueError("hotword_manifest_invalid")
    words: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        word = str(candidate or "").strip()
        folded = word.casefold()
        if word and folded not in seen:
            words.append(word)
            seen.add(folded)
    return words


def _result_text(result: Any) -> str:
    if not isinstance(result, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in result
        if isinstance(item, dict)
    ).strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resident local FunASR endpoint refiner")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--vad-model", required=True, type=Path)
    parser.add_argument("--punc-model", required=True, type=Path)
    parser.add_argument("--hotword-manifest", type=Path)
    parser.add_argument("--device", default="cpu")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    os.environ.update({
        "MODELSCOPE_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    hotwords = _load_hotwords(args.hotword_manifest)
    started = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        from funasr import AutoModel

        model = AutoModel(
            model=str(args.model),
            vad_model=str(args.vad_model),
            punc_model=str(args.punc_model),
            device=args.device,
            disable_update=True,
        )
    _emit({
        "event_type": "ready",
        "model_id": args.model.name,
        "model_load_latency_ms": int((time.monotonic() - started) * 1000),
        "hotword_count": len(hotwords),
        "network_offline": True,
    })

    for line in sys.stdin:
        request_id = ""
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                return
            request_id = str(request.get("request_id") or "")
            if request.get("command") != "refine" or not request_id:
                raise ValueError("invalid_request")
            if int(request.get("sample_rate") or 0) != 16_000:
                raise ValueError("invalid_sample_rate")
            pcm16 = base64.b64decode(str(request.get("pcm16_base64") or ""), validate=True)
            if not pcm16 or len(pcm16) % 2:
                raise ValueError("invalid_pcm16")
            samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
            inference_started = time.monotonic()
            generate_kwargs: dict[str, Any] = {
                "input": samples,
                "batch_size_s": 60,
                "merge_vad": True,
                "merge_length_s": 15,
            }
            if hotwords:
                generate_kwargs["hotword"] = " ".join(hotwords)
            with contextlib.redirect_stdout(sys.stderr):
                result = model.generate(**generate_kwargs)
            _emit({
                "event_type": "result",
                "request_id": request_id,
                "status": "ok",
                "text": _result_text(result),
                "model_id": args.model.name,
                "latency_ms": int((time.monotonic() - inference_started) * 1000),
            })
        except Exception as exc:
            _emit({
                "event_type": "result",
                "request_id": request_id,
                "status": "failed",
                "error_code": str(exc) if isinstance(exc, ValueError) else "inference_failed",
            })


if __name__ == "__main__":
    main()
