"""Transcript normalizer — fixes common ASR misrecognitions via a term dictionary.

Loads configs/asr_terms.json (raw fragment -> normalized). Applied to ASR final
text so downstream state/LLM see corrected technical entities (e.g. 't九九' ->
'P99'). Conservative: only exact-fragment replacements from the dict, longest
first to avoid partial overlaps. Raw text is always preserved alongside.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_TERMS_PATH = _REPO_ROOT / "configs" / "asr_terms.json"


@lru_cache(maxsize=8)
def load_terms(terms_path: str | None = None) -> dict[str, str]:
    path = Path(terms_path) if terms_path else _DEFAULT_TERMS_PATH
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("terms") or {})


def normalize(raw_text: str, terms_path: str | None = None) -> str:
    """Apply term-dictionary replacements to raw ASR text (longest first)."""
    if not raw_text:
        return raw_text
    terms = load_terms(terms_path)
    if not terms:
        return raw_text
    # longest keys first to avoid partial overlaps (e.g. '九九延迟' before '九九')
    out = raw_text
    for key in sorted(terms, key=len, reverse=True):
        if key and key in out:
            out = out.replace(key, terms[key])
    return out


def hotwords(terms_path: str | None = None) -> list[str]:
    """Return the hotword list for ASR engines that support them (FunASR)."""
    path = Path(terms_path) if terms_path else _DEFAULT_TERMS_PATH
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("hotwords") or [])
