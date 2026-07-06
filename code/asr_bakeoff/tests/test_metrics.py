from asr_bakeoff.metrics import char_error_rate, entity_accuracy, latency_summary


def test_char_error_rate_counts_chinese_substitution_insertion_and_deletion():
    assert char_error_rate("接口新增 trace_id 字段", "接口新曾 trace_id 字段") == 1 / len("接口新增 trace_id 字段")
    assert char_error_rate("灰度回滚", "灰度回滚了") == 1 / len("灰度回滚")
    assert char_error_rate("兼容调用方", "兼容调方") == 1 / len("兼容调用方")


def test_char_error_rate_ignores_surrounding_newlines_from_reference_files():
    assert char_error_rate("接口新增 trace_id 字段\n", "接口新增 trace_id 字段") == 0


def test_entity_accuracy_requires_exact_normalized_entity_match():
    reference = ["Kafka", "trace_id", "灰度", "回滚"]
    hypothesis = ["Kafka", "trace_id", "灰度", "缓存"]

    result = entity_accuracy(reference, hypothesis)

    assert result.precision == 0.75
    assert result.recall == 0.75
    assert result.f1 == 0.75
    assert result.missing == ["回滚"]
    assert result.extra == ["缓存"]


def test_latency_summary_reports_p50_p95_and_max():
    result = latency_summary([100, 200, 300, 400, 500])

    assert result.count == 5
    assert result.p50_ms == 300
    assert result.p95_ms == 500
    assert result.max_ms == 500
