#!/usr/bin/env python3
"""Launch isolated soak helpers hidden; credentials remain in child environments."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/realtime-participation-20261007/long-soak-r01"


def private_defaults() -> tuple[str, str]:
    # Read the existing ignored defaults in memory. Do not serialize them or
    # print error objects containing provider configuration.
    from dotenv import dotenv_values

    source = dotenv_values(ROOT / ".env")
    base = os.environ.get("LLM_GATEWAY_BASE_URL") or source.get("LLM_GATEWAY_BASE_URL")
    key = os.environ.get("LLM_GATEWAY_API_KEY") or source.get("LLM_GATEWAY_API_KEY")
    if not base or not key:
        raise ValueError("existing private provider defaults are unavailable")
    return str(base), str(key)


def start(component: str, launch_label: str | None = None, *, output_dir: Path = OUTPUT,
          allow_exhausted_proxy: bool = False, meeting_id: str = "long_audio_soak_20261008_r01",
          write_timeout_seconds: float = 5.0) -> dict:
    if os.name != "nt":
        raise ValueError("this hidden launcher is for the Windows acceptance host")
    if not math.isfinite(write_timeout_seconds) or not 1 <= write_timeout_seconds <= 10:
        raise ValueError("write timeout must be finite and between1and10seconds")
    if component != "replay" and write_timeout_seconds != 5.0:
        raise ValueError("write timeout is a replay transport option only")
    if launch_label is not None and (component not in {"server", "replay"} or not re.fullmatch(re.escape(component) + r"-r[0-9]{2}", launch_label)):
        raise ValueError("a fresh server/replay receipt label must have the component-rNN form")
    output = output_dir.resolve()
    accepted_parent = (ROOT / "artifacts/realtime-participation-20261007").resolve()
    if output.parent != accepted_parent or not re.fullmatch(r"long-soak-r[0-9]{2}", output.name):
        raise ValueError("isolated helper output must be an explicit long-soak-rNN acceptance directory")
    if component == "proxy" and output != OUTPUT.resolve():
        raise ValueError("a second proxy budget is forbidden; preserve the original boundary")
    if allow_exhausted_proxy and component != "replay":
        raise ValueError("exhausted boundary is an explicit replay mode only")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", meeting_id):
        raise ValueError("meeting ID is not safe")
    receipt_label = launch_label or component
    output.mkdir(parents=True, exist_ok=True)
    base, key = private_defaults()
    environment = dict(os.environ)
    environment["MEETING_SOAK_UPSTREAM_BASE_URL"] = base
    environment["MEETING_SOAK_UPSTREAM_API_KEY"] = key
    environment["MEETING_SOAK_PROXY_TOKEN"] = key
    # The isolated service authenticates to the loopback boundary; the proxy
    # privately authenticates to the same user-supplied upstream gateway.
    environment["LLM_GATEWAY_BASE_URL"] = "http://127.0.0.1:8790/v1"
    environment["LLM_GATEWAY_API_KEY"] = key
    environment["LLM_GATEWAY_MODEL"] = "gpt-6-sol"
    environment["LLM_GATEWAY_REALTIME_MODEL"] = "gpt-6-sol"
    environment["LLM_GATEWAY_CORRECTION_MODEL"] = "gpt-6-sol"
    environment["LLM_GATEWAY_API_STYLE"] = "responses"
    environment["LLM_GATEWAY_TIMEOUT_SECONDS"] = "25"
    environment["MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS"] = "180"
    if component == "proxy":
        if (output / "provider-boundary.json").exists():
            raise ValueError("refusing to reset an existing proxy budget")
        arguments = ["tools/long_soak_provider_proxy.py", "--port", "8790", "--report",
                     str((output / "provider-boundary.json").relative_to(ROOT))]
    elif component == "server":
        arguments = ["tools/workbench_server.py", "start", "--port", "8769", "--provider-mode", "inherit",
                     "--realtime-refiner-policy", "prewarm", "--correction-pricing-mode", "unmetered",
                     "--data-dir", str(output / "runtime"), "--pid-file", str(output / "server.pid"),
                     "--log-file", str(output / "server.log")]
    else:
        arguments = ["tools/long_audio_soak.py", "--mode", "run", "--output-dir", str(output),
                     "--data-dir", str(output / "runtime"), "--meeting-id", meeting_id,
                     "--write-timeout-seconds", str(write_timeout_seconds)]
        if allow_exhausted_proxy:
            arguments.append("--allow-exhausted-proxy")
    environment["MEETING_SOAK_HELPER_PYTHON"] = sys.executable
    environment["MEETING_SOAK_HELPER_ROOT"] = str(ROOT)
    environment["MEETING_SOAK_HELPER_STDOUT"] = str(output / f"{receipt_label}-stdout.log")
    environment["MEETING_SOAK_HELPER_STDERR"] = str(output / f"{receipt_label}-stderr.log")
    environment["MEETING_SOAK_HELPER_PID"] = str(output / f"{receipt_label}-launcher.pid")
    pid_file = Path(environment["MEETING_SOAK_HELPER_PID"])
    if pid_file.exists():
        raise ValueError("helper PID receipt already exists; root must inspect the prior owned launch")
    quoted = ",".join("'" + item.replace("'", "''") + "'" for item in arguments)
    script = (
        "$ErrorActionPreference='Stop';"
        "$soakProcess=Start-Process -FilePath $env:MEETING_SOAK_HELPER_PYTHON "
        f"-ArgumentList @({quoted}) -WorkingDirectory $env:MEETING_SOAK_HELPER_ROOT -WindowStyle Hidden "
        "-RedirectStandardOutput $env:MEETING_SOAK_HELPER_STDOUT "
        "-RedirectStandardError $env:MEETING_SOAK_HELPER_STDERR -PassThru;"
        "$soakProcess.Id|Set-Content -LiteralPath $env:MEETING_SOAK_HELPER_PID"
    )
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        raise ValueError("Windows hidden process launcher is unavailable")
    # PowerShell may remain alive draining redirected child output. Observe its
    # explicit PID receipt, not inheritance of an output pipe, as launch proof.
    wrapper = subprocess.Popen([executable, "-NoProfile", "-Command", script], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    (output / f"{receipt_label}-wrapper.pid").write_text(str(wrapper.pid), encoding="utf-8")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text(encoding="utf-8-sig").strip().isdigit():
            return {"component": component, "status": "spawned",
                    "launcher_pid": int(pid_file.read_text(encoding="utf-8-sig").strip()),
                    "wrapper_pid": wrapper.pid, "credentials_archived": False}
        if wrapper.poll() is not None:
            break
        time.sleep(0.1)
    raise ValueError("hidden helper did not produce an owned PID receipt")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("proxy", "server", "replay"))
    parser.add_argument("--launch-label")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--allow-exhausted-proxy", action="store_true")
    parser.add_argument("--meeting-id", default="long_audio_soak_20261008_r01")
    parser.add_argument("--write-timeout-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(start(args.component, args.launch_label, output_dir=args.output_dir,
                               allow_exhausted_proxy=args.allow_exhausted_proxy, meeting_id=args.meeting_id,
                               write_timeout_seconds=args.write_timeout_seconds)), flush=True)
    except Exception as error:
        print(json.dumps({"status": "launch_failed", "error_class": type(error).__name__,
                          "credentials_archived": False}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
