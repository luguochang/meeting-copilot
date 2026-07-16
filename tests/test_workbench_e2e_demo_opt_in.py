from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_SMOKE = REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_smoke.mjs"


def test_workbench_smoke_uses_explicit_demo_opt_in_url():
    script = WORKBENCH_SMOKE.read_text(encoding="utf-8")

    assert "/workbench?demo=1" in script
    assert "demoHidden === false" in script
