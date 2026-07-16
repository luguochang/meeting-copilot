import importlib.util
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "generate_chinese_technical_meeting_audio.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("generate_chinese_technical_meeting_audio", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generated_meeting_script_is_deterministic_and_chinese_only():
    tool = _load_tool()
    first = tool.build_meeting_script(12)
    second = tool.build_meeting_script(12)

    assert first == second
    assert len(first.splitlines()) >= 12
    assert re.search(r"[A-Za-z]", first) is None
    for marker in ("发布流程和灰度策略", "录音保存和历史恢复", "会后纪要和行动项"):
        assert marker in tool.build_meeting_script(240)


def test_script_rejects_non_positive_paragraph_count():
    tool = _load_tool()

    try:
        tool.build_meeting_script(0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected non-positive paragraph count to fail")
