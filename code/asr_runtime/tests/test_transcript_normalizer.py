import json
from pathlib import Path

from scripts.transcript_normalizer import load_glossary, normalize_transcript_text


REPO_ROOT = Path(__file__).resolve().parents[3]
TECHNICAL_GLOSSARY = REPO_ROOT / "data" / "asr_eval" / "glossaries" / "technical-terms.zh.json"


def test_normalize_transcript_text_applies_numeric_rules_and_glossary_aliases(tmp_path):
    glossary = tmp_path / "glossary.json"
    glossary.write_text(
        json.dumps(
            {
                "terms": [
                    {
                        "canonical": "payment-gateway",
                        "aliases": ["payment gate 为", "payment gate"],
                    },
                    {"canonical": "P99", "aliases": ["t 九九", "九九"]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = normalize_transcript_text(
        "我 们 这 次 payment gate 为 先 灰 度 百 分 之 十，"
        "如 果 错 误 率 超 过 百 分 之 零 点 一，旧 回 滚，"
        "还 需 要 确 认 t 九 九。",
        glossary_terms=load_glossary(glossary),
    )

    assert "我们这次" in result.text
    assert "payment-gateway" in result.text
    assert "10%" in result.text
    assert "0.1%" in result.text
    assert "P99" in result.text
    assert "百 分 之 十" not in result.text
    assert {change["canonical"] for change in result.changes} >= {
        "payment-gateway",
        "10%",
        "0.1%",
        "P99",
    }


def test_normalize_transcript_text_recovers_spoken_error_code_digits():
    result = normalize_transcript_text("错误码先用四万零一十二，但是灰度期间需要看九九延迟")

    assert "错误码先用 40012" in result.text
    assert "P99" in result.text
    assert {change["canonical"] for change in result.changes} >= {"40012", "P99"}


def test_committed_technical_glossary_recovers_common_mixed_term_aliases():
    result = normalize_transcript_text(
        "需要确认 request id、error rate、redis cluster 和 payment gateway",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "request_id" in result.text
    assert "error_rate" in result.text
    assert "redis cluster" in result.text
    assert "payment-gateway" in result.text
    assert {change["canonical"] for change in result.changes} >= {
        "request_id",
        "error_rate",
        "payment-gateway",
    }


def test_normalize_transcript_text_recovers_lag_from_backlog_metric_context_without_guessing_missing_terms():
    result = normalize_transcript_text(
        "<unk> 凌晨欧<unk> <unk> 消费堆积<unk> 最高到了八万告警延迟了六分钟"
        "<unk> 临时扩容已经止血跟因可能是库存接口",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "消费堆积 lag" in result.text
    assert "timeout" not in result.text
    assert "order-worker" not in result.text
    assert "监控阈值" not in result.text
    assert {"alias": "消费堆积最高到了", "canonical": "lag"} in result.changes


def test_normalize_transcript_text_recovers_qps_from_capacity_peak_context_without_guessing_storage_entities():
    result = normalize_transcript_text(
        "新的<unk> 会依赖<unk> 和<unk> 峰值按两万估缓存穿透时可能会达到<unk> "
        "降级方案先写在设计文档里压测<unk> 还没安排",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "QPS 峰值按两万估" in result.text
    assert "mysql" not in result.text
    assert "feature-store" not in result.text
    assert "redis cluster" not in result.text
    assert {"alias": "峰值按", "canonical": "QPS"} in result.changes


def test_normalize_transcript_text_keeps_qps_separated_after_ascii_technical_terms():
    result = normalize_transcript_text(
        "新的 recommendation-service 会依赖 ure store 和 REDi coasterBQP峰值按两万估缓存穿透",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "redis cluster QPS 峰值按" in result.text
    assert "clusterQPS" not in result.text


def test_committed_technical_glossary_recovers_funasr_observed_near_misses_without_guessing_unseen_terms():
    result = normalize_transcript_text(
        "paymentgateway 字段 request 错误码 40012 需要看 p九九b继续。"
        "新的 dationservice 依赖 featurestore 和 REDIScostbQPS。"
        "凌晨 autoker 消费堆积 lag 最高到了八万。"
        "check koutservice 灰度先看 errorate pp九b如果。",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "payment-gateway" in result.text
    assert "request_id" in result.text
    assert "P99" in result.text
    assert "recommendation-service" in result.text
    assert "feature-store" in result.text
    assert "redis cluster" in result.text
    assert "pp 九" not in result.text
    assert "pP99" not in result.text
    assert "P99" in result.text
    assert "order-worker" in result.text
    assert "checkout-service" in result.text
    assert "error_rate" in result.text
    assert "timeout" not in result.text
    assert "监控阈值" not in result.text
    assert {change["canonical"] for change in result.changes} >= {
        "payment-gateway",
        "request_id",
        "P99",
        "recommendation-service",
        "feature-store",
        "redis cluster",
        "order-worker",
        "checkout-service",
        "error_rate",
    }


def test_normalize_transcript_text_recovers_chunk20_hotword_visible_near_misses_without_backfilling_absent_entities():
    result = normalize_transcript_text(
        "我们先看 paymentway 的创建订单接口字段 quest 要兼容旧客户端。"
        "新的 recommendation-service 会依赖 ure store 和 REDi coasterBQP峰值按两万估。"
        "凌晨 auder 消费堆积 lag 最高到了八万。"
        "这次 trcoutservice service 周晚上灰度先看 errorate 和 p九九。",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "payment-gateway" in result.text
    assert "字段 request_id" in result.text
    assert "feature-store" in result.text
    assert "redis cluster" in result.text
    assert "order-worker" in result.text
    assert "checkout-service" in result.text
    assert "error_rate" in result.text
    assert "P99" in result.text
    assert "timeout" not in result.text
    assert "监控阈值" not in result.text
    assert "staging" not in result.text
    assert {change["canonical"] for change in result.changes} >= {
        "payment-gateway",
        "request_id",
        "feature-store",
        "redis cluster",
        "order-worker",
        "checkout-service",
        "error_rate",
        "P99",
    }


def test_normalize_transcript_text_does_not_convert_unscoped_quest_to_request_id():
    result = normalize_transcript_text(
        "普通英文 quest 只是在讨论任务名称，不是接口字段。",
        glossary_terms=load_glossary(TECHNICAL_GLOSSARY),
    )

    assert "request_id" not in result.text
