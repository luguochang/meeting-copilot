#!/usr/bin/env python3
"""Meeting Copilot fail-closed local verification entrypoint."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent
WEB_BACKEND_ROOT = PROJECT_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = PROJECT_ROOT / "code" / "core"

for import_root in (WEB_BACKEND_ROOT, CORE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from meeting_copilot_web_mvp.batch_transcribe import _get_ffmpeg  # noqa: E402
from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller  # noqa: E402
from meeting_copilot_web_mvp.sqlite_repository import (  # noqa: E402
    SqliteAsrLiveSessionRepository,
    SqliteSessionRepository,
    migrate_json_to_sqlite,
)


def test_imports() -> bool:
    print("[check] 关键模块导入")
    required = (
        SqliteAsrLiveSessionRepository,
        SqliteSessionRepository,
        migrate_json_to_sqlite,
        get_degradation_controller,
    )
    passed = all(item is not None for item in required)
    print("  PASS" if passed else "  FAIL")
    return passed


def test_degradation_controller() -> bool:
    print("[check] 降级控制器")
    controller = get_degradation_controller()
    controller.reset()
    try:
        if controller.level != 0:
            return False
        controller.set_level(2, "verification_probe")
        if controller.level != 2:
            return False
        if controller.can_generate_suggestions():
            return False
        if not controller.can_run_asr():
            return False
        controller.set_level(1, "must_not_deescalate")
        if controller.level != 2:
            return False
        return True
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}")
        return False
    finally:
        controller.reset()


def test_sqlite_repository() -> bool:
    print("[check] SQLite Repository")
    try:
        with tempfile.TemporaryDirectory(prefix="meeting-copilot-verify-") as raw_dir:
            data_dir = Path(raw_dir)
            repo = SqliteAsrLiveSessionRepository(data_dir)
            repo.create(
                {
                    "session_id": "verification_session",
                    "events": [],
                    "provider": "verification_local",
                    "transcript_text": "测试转写",
                }
            )
            if repo.get("verification_session").get("session_id") != "verification_session":
                raise RuntimeError("created record could not be read back")
            updated = repo.update(
                "verification_session",
                lambda record: {**record, "transcript_text": "更新后的转写"},
            )
            if updated.get("transcript_text") != "更新后的转写":
                raise RuntimeError("updated record was not persisted")
            if repo.delete("verification_session") is not True:
                raise RuntimeError("record delete did not succeed")
            if repo.list() != []:
                raise RuntimeError("deleted record remained in repository")
            if not (data_dir / "meeting_copilot.db").is_file():
                raise RuntimeError("repository did not create meeting_copilot.db")
        return True
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False


def test_ffmpeg_integration() -> bool:
    print("[check] ffmpeg")
    try:
        ffmpeg = _get_ffmpeg()
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False
    if not ffmpeg:
        print("  FAIL: ffmpeg unavailable")
        return False
    print(f"  PASS: {ffmpeg}")
    return True


def test_api_endpoints(base_url: str = "http://127.0.0.1:8765") -> bool:
    print(f"[check] API {base_url}")
    payloads: dict[str, dict] = {}
    for path in ("/health", "/degradation/status", "/providers/health"):
        try:
            response = httpx.get(f"{base_url}{path}", timeout=3)
        except httpx.RequestError as exc:
            print(f"  FAIL {path}: {type(exc).__name__}: {exc}")
            return False
        if response.status_code != 200:
            print(f"  FAIL {path}: HTTP {response.status_code}")
            return False
        try:
            payload = response.json()
        except ValueError:
            print(f"  FAIL {path}: response is not JSON")
            return False
        if not isinstance(payload, dict):
            print(f"  FAIL {path}: response is not a JSON object")
            return False
        payloads[path] = payload
        print(f"  PASS {path}")

    if payloads["/health"].get("status") != "ok":
        print("  FAIL /health: service status is not ok")
        return False
    degradation_level = payloads["/degradation/status"].get("level")
    if type(degradation_level) is not int or degradation_level != 0:
        print("  FAIL /degradation/status: production verification requires Level 0")
        return False

    providers = payloads["/providers/health"]
    llm = dict(providers.get("llm") or {})
    asr = dict(providers.get("asr") or {})
    provider_failures = []
    if asr.get("file_asr_available") is not True:
        provider_failures.append("local file ASR unavailable")
    if asr.get("realtime_asr_available") is not True:
        provider_failures.append("local realtime ASR unavailable")
    if llm.get("credential_configured") is not True:
        provider_failures.append("LLM gateway not configured")
    if llm.get("is_mock") is not False:
        provider_failures.append("LLM provider mock flag missing or enabled")
    if provider_failures:
        print(f"  FAIL /providers/health: {', '.join(provider_failures)}")
        return False
    return True


def test_llm_gateway_probe(base_url: str = "http://127.0.0.1:8765") -> bool:
    print("[check] Running service LLM gateway operability")
    try:
        response = httpx.post(
            f"{base_url}/providers/llm/probe",
            headers={"X-Meeting-Copilot-Verification": "1"},
            timeout=20,
        )
    except httpx.RequestError as exc:
        print(f"  FAIL: service probe request failed ({type(exc).__name__})")
        return False
    if response.status_code != 200:
        print(f"  FAIL: service LLM probe returned HTTP {response.status_code}")
        return False
    try:
        payload = response.json()
    except ValueError:
        print("  FAIL: service LLM probe returned non-JSON response")
        return False
    if not isinstance(payload, dict) or payload.get("operational") is not True:
        print("  FAIL: service did not confirm LLM operability")
        return False
    print("  PASS: real OpenAI-compatible response received")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checks = [
        ("模块导入", test_imports()),
        ("降级控制器", test_degradation_controller()),
        ("SQLite Repository", test_sqlite_repository()),
        ("ffmpeg", test_ffmpeg_integration()),
        ("API", test_api_endpoints(args.base_url)),
        ("LLM gateway", test_llm_gateway_probe(args.base_url)),
    ]
    print("\nVerification summary")
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return 0 if all(passed for _, passed in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
