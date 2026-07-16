from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "启动服务.sh"


def test_startup_script_uses_the_managed_local_server_entrypoint():
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "tools/workbench_server.py" in script
    assert "MEETING_COPILOT_DATA_DIR" in script
    assert "data/local_runtime/web_mvp" in script
    assert "artifacts/tmp/web_mvp_data" not in script
    assert "--host 0.0.0.0" not in script
    assert "pip install" not in script
    assert "kill $OLD_PID" not in script
    assert SCRIPT_PATH.stat().st_mode & 0o111
