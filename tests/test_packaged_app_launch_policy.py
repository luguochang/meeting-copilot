from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
PACKAGED_RUNNERS = (
    "packaged_runtime_supervisor_smoke.py",
    "packaged_ai_mainline_smoke.py",
    "full_roadmap_packaged_acceptance.py",
    "packaged_native_mic_smoke.py",
    "packaged_tauri_ipc_smoke.py",
    "macos_dmg_install_smoke.py",
)


def _load_tool(name: str):
    path = REPO_ROOT / "tools" / name
    spec = importlib.util.spec_from_file_location(f"launch_policy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_packaged_runners_disable_macos_state_restoration_prompts():
    binary = Path("/Applications/Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop")

    for runner in PACKAGED_RUNNERS:
        module = _load_tool(runner)
        command = module.packaged_app_launch_command(binary)

        assert command == [
            str(binary),
            "-ApplePersistenceIgnoreState",
            "YES",
            "-NSQuitAlwaysKeepsWindows",
            "NO",
        ], runner
