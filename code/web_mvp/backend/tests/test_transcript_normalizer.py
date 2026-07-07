"""Tests for transcript_normalizer."""
from meeting_copilot_web_mvp import transcript_normalizer as tn


def test_normalize_fixes_known_asr_misrecognitions():
    # terms are cached via lru_cache; force reload with the real config
    tn.load_terms.cache_clear()
    assert tn.normalize("还需要确认t九九和错误率") == "还需要确认P99和错误率"
    assert tn.normalize("兼容性测试用力") == "兼容性测试用例"
    assert tn.normalize("先挥百分之十") == "灰度百分之十"
    assert tn.normalize("旧回軚") == "旧回滚"


def test_normalize_longest_first_avoids_partial_overlap():
    tn.load_terms.cache_clear()
    # '九九延迟' should map as a unit, not '九九'->P99 first leaving '延迟'
    assert tn.normalize("看九九延迟") == "看P99延迟"


def test_normalize_preserves_text_without_terms():
    tn.load_terms.cache_clear()
    assert tn.normalize("普通中文无术语") == "普通中文无术语"
    assert tn.normalize("") == ""


def test_normalize_with_explicit_terms_path(tmp_path):
    p = tmp_path / "terms.json"
    p.write_text('{"terms": {"foo": "bar"}}', encoding="utf-8")
    tn.load_terms.cache_clear()
    assert tn.normalize("foo baz", str(p)) == "bar baz"


def test_hotwords_returns_list():
    tn.load_terms.cache_clear()
    hw = tn.hotwords()
    assert isinstance(hw, list)
    assert "P99" in hw
