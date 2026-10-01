#!/usr/bin/env python3
"""Prepare and launch the complete local web experience with explicit ASR paths.

Run with the backend Python environment. Model downloads use the separate
FunASR Python environment. No credentials or existing meeting settings are copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = ROOT / "data/local_runtime/web-models"
DEFAULT_DATA = ROOT / "data/local_runtime/web-quality"
ASR_ROOT = ROOT / "code/asr_runtime"
BRIDGE = ROOT / "code/agent_runtime/pi_coach_bridge"


def python_in(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def model_specs() -> dict:
    packs = ASR_ROOT / "model_packs"
    file_pack = json.loads((packs / "file-asr-zh-cn-20260718.manifest.json").read_text())
    speaker_pack = json.loads((packs / "diarization-camplus-zh-cn.manifest.json").read_text())
    models = {
        "online": {
            "model_id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
            "files": {
                "model.pt": "4fdfb48ed4471777c9a511e96a2acae17f77cac9d709cc756634622769192a64",
                "config.yaml": "35e6bf41f8c7eaf9a0f787af7fdc8fc5ed75fa8009ade7d3c2f3ef5bce20c648",
                "configuration.json": "1aae0f45e3e503f52512eee4798d241611a44cc732a7a91d2e2d2f65dd9755be",
                "am.mvn": "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5",
                "tokens.json": "2b20c2b12572d682afff84ce1c8d560f67b8b32a4c1f21567411d141ed352127",
                "seg_dict": "59a2ef803a3f1648ad03a2e1480db1c1ee0c0d7dc4ef4dbd16cea33944329022",
            },
        },
        **{name: file_pack["models"][name] for name in ("offline", "vad", "punc")},
        "camplus": {**speaker_pack["models"]["camplus"], "files": speaker_pack["models"]["camplus"]["required_files"]},
    }
    # Upstream 'master' is a download locator, never our version identity.
    # Every inference file must match the recorded SHA-256 after download.
    for name, spec in models.items():
        spec["revision"] = "v2.0.2" if name == "camplus" else "v2.0.4" if name == "vad" else "master"
    return models


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def model_errors(directory: Path, spec: dict) -> list[str]:
    errors = []
    for name, expected in spec["files"].items():
        path = directory / name
        if not path.is_file():
            errors.append(f"missing:{name}")
        elif sha256(path) != expected:
            errors.append(f"sha256_mismatch:{name}")
    return errors


def download_models(model_root: Path, cache_root: Path) -> None:
    interpreter = python_in(ASR_ROOT / ".venv-funasr")
    if not interpreter.is_file():
        raise RuntimeError("Install code/asr_runtime/.venv-funasr first; see docs/web-deployment.md")
    for name, spec in model_specs().items():
        target = model_root / name
        if not model_errors(target, spec):
            print(f"{name}: verified", flush=True)
            continue
        cached = cache_root / spec["model_id"]
        if not model_errors(cached, spec):
            target.mkdir(parents=True, exist_ok=True)
            for filename in [*spec["files"], "README.md", "LICENSE"]:
                if (cached / filename).is_file():
                    shutil.copy2(cached / filename, target / filename)
            print(f"{name}: copied verified cache", flush=True)
        else:
            print(f"{name}: downloading {spec['model_id']} @ {spec['revision']}", flush=True)
            subprocess.run([
                str(interpreter), "-c",
                "import sys; from modelscope.hub.snapshot_download import snapshot_download; "
                "snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])",
                spec["model_id"], spec["revision"], str(target),
            ], check=True)
        errors = model_errors(target, spec)
        if errors:
            raise RuntimeError(f"{name}: {errors}; upstream content differs from the verified baseline; do not bypass verification")


def quality_environment(model_root: Path, data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    runtime = str(python_in(ASR_ROOT / ".venv-funasr"))
    scripts = ASR_ROOT / "scripts"
    env.update({
        "MEETING_COPILOT_DATA_DIR": str(data_dir),
        "MEETING_COPILOT_FUNASR_PYTHON": runtime,
        "MEETING_COPILOT_FUNASR_WORKER": str(scripts / "funasr_stream_worker.py"),
        "MEETING_COPILOT_FUNASR_ENGINE": "pytorch",
        "MEETING_COPILOT_FUNASR_MODEL_DIR": str(model_root / "online"),
        "MEETING_COPILOT_BATCH_FUNASR_PYTHON": runtime,
        "MEETING_COPILOT_BATCH_TRANSCRIBE_WORKER": str(scripts / "transcribe_funasr.py"),
        "MEETING_COPILOT_FILE_ASR_MODEL_DIR": str(model_root / "offline"),
        "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR": str(model_root / "vad"),
        "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR": str(model_root / "punc"),
        "MEETING_COPILOT_REALTIME_REFINER_PYTHON": runtime,
        "MEETING_COPILOT_REALTIME_REFINER_WORKER": str(scripts / "funasr_offline_refiner_worker.py"),
        "MEETING_COPILOT_REALTIME_REFINER_MODEL": str(model_root / "offline"),
        "MEETING_COPILOT_REALTIME_REFINER_VAD_MODEL": str(model_root / "vad"),
        "MEETING_COPILOT_REALTIME_REFINER_PUNC_MODEL": str(model_root / "punc"),
        "MEETING_COPILOT_REALTIME_REFINER_HOTWORDS": str(ROOT / "configs/asr_hotwords.json"),
        "MEETING_COPILOT_REALTIME_REFINER_POLICY": "prewarm",
        "MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS": "120",
        "MEETING_COPILOT_DIARIZATION_WORKER": str(scripts / "funasr_diarization_worker.py"),
        "MEETING_COPILOT_DIARIZATION_VAD_DIR": str(model_root / "vad"),
        "MEETING_COPILOT_DIARIZATION_CAMPLUS_DIR": str(model_root / "camplus"),
        "MEETING_COPILOT_REALTIME_COACH_RUNTIME": "pi",
        "MEETING_COPILOT_REALTIME_COACH_ENABLED": "1",
        "MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST": "0",
        "MEETING_COPILOT_PI_BRIDGE_PREWARM": "1",
        "MEETING_COPILOT_PI_BRIDGE_ENTRY": str(BRIDGE / "src/bridge.mjs"),
        "MEETING_COPILOT_NODE_EXECUTABLE": shutil.which("node") or "",
        "MEETING_COPILOT_REALTIME_READY_CUTOFF_MS": "8000",
    })
    # Source deployment must not inherit an installed app's manifest/Python home
    # or an experimental ONNX worker path from the developer's machine.
    for key in ("MEETING_COPILOT_RUNTIME_MANIFEST", "PYTHONHOME",
                "MEETING_COPILOT_FUNASR_PYTHON_HOME", "MEETING_COPILOT_FUNASR_PYTHONPATH",
                "MEETING_COPILOT_REALTIME_REFINER_PYTHON_HOME", "MEETING_COPILOT_REALTIME_REFINER_PYTHONPATH"):
        env.pop(key, None)
    return env


def doctor(model_root: Path, data_dir: Path) -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    env = quality_environment(model_root, data_dir)
    check("frontend", (ROOT / "code/web_mvp/frontend_v2/dist/index.html").is_file(), "npm ci && npm run build")
    for name, spec in model_specs().items():
        errors = model_errors(model_root / name, spec)
        check(f"model.{name}", not errors, ", ".join(errors))
    commands = [
        ("backend", [str(python_in(ROOT / "code/web_mvp/backend/.venv")), "-c",
                     "import fastapi,uvicorn,httpx,sounddevice; import imageio_ffmpeg as f; "
                     "import subprocess; subprocess.run([f.get_ffmpeg_exe(), '-version'],check=True,capture_output=True)"]),
        ("asr.dependencies", [str(python_in(ASR_ROOT / ".venv-funasr")), "-c", "import funasr,torch,torchaudio,soundfile"]),
        ("pi.node_version", [env["MEETING_COPILOT_NODE_EXECUTABLE"] or "node", "-e",
                             "const [a,b]=process.versions.node.split('.').map(Number);process.exit(a>22||(a===22&&b>=19)?0:1)"]),
        ("pi.bridge", [env["MEETING_COPILOT_NODE_EXECUTABLE"] or "node", str(BRIDGE / "src/bridge.mjs"), "--smoke"]),
    ]
    for name, command in commands:
        try:
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
            # Never dump inherited configuration or provider secrets in diagnostics.
            check(name, result.returncode == 0, "ok" if result.returncode == 0 else f"exit={result.returncode}; check installation")
        except (OSError, subprocess.TimeoutExpired) as exc:
            check(name, False, type(exc).__name__)
    return {"ready": all(item["ok"] for item in checks), "checks": checks,
            "profile": "web-quality-v1", "refiner_policy": "prewarm", "runtime": "pi",
            "provider": "configure and test in AI settings; doctor makes no remote AI call",
            "scope": "dependency/hash/bridge checks; not microphone or recognition-quality acceptance"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["models", "doctor", "start", "stop", "status"])
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--cache-root", type=Path, default=Path.home() / ".cache/modelscope/hub/models")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    model_root, data_dir = args.model_root.expanduser().resolve(), args.data_dir.expanduser().resolve()
    if args.command == "models":
        download_models(model_root, args.cache_root.expanduser().resolve())
        return 0
    if args.command in {"doctor", "start"}:
        report = doctor(model_root, data_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if not report["ready"]:
            return 1
        if args.command == "doctor":
            return 0
    command = [str(python_in(ROOT / "code/web_mvp/backend/.venv")),
               str(ROOT / "tools/workbench_server.py"), args.command,
               "--port", str(args.port), "--data-dir", str(data_dir),
               "--pid-file", str(data_dir / "server.pid"), "--log-file", str(data_dir / "server.log")]
    if args.command == "start":
        command += ["--provider-mode", "inherit", "--realtime-refiner-policy", "prewarm", "--realtime-coach-cutoff-ms", "8000"]
    return subprocess.run(command, cwd=ROOT, env=quality_environment(model_root, data_dir), check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
