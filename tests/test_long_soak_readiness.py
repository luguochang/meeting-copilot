import pytest

from tools import long_soak_readiness as tool


def test_startup_readiness_cannot_overwrite_prior_source_hash_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "__file__", str(tmp_path / "tools/readiness.py"))
    output = tmp_path / "artifacts/realtime-participation-20261007/long-soak-r03"
    output.mkdir(parents=True)
    report = output / "readiness.json"
    report.write_text('{"production_source_sha256_at_start":"preserve"}')

    def forbidden(**kwargs):
        raise AssertionError("preserved readiness should stop before service access")

    monkeypatch.setattr(tool, "status_report", forbidden)
    with pytest.raises(ValueError, match="preserved startup"):
        tool.main(["--output-dir", str(output)])
    assert report.read_text() == '{"production_source_sha256_at_start":"preserve"}'


def test_readiness_rejects_unrelated_output_before_service_access(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "__file__", str(tmp_path / "tools/readiness.py"))

    def forbidden(**kwargs):
        raise AssertionError("invalid readiness path attempted service access")

    monkeypatch.setattr(tool, "status_report", forbidden)
    with pytest.raises(ValueError, match="explicit isolated"):
        tool.main(["--output-dir", str(tmp_path / "unrelated")])
