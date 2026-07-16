from meeting_copilot_web_mvp.asr_semantic_quality import evaluate_semantic_quality
from meeting_copilot_web_mvp.transcript_normalizer import normalize


def test_asr_semantic_quality_passes_chinese_technical_meeting_sentence():
    result = evaluate_semantic_quality(
        "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 和 P99 看板。"
    )

    assert result["status"] == "passed"
    assert result["blocker"] is None
    assert result["technical_entity_hit_count"] >= 5
    assert result["technical_group_hit_count"] >= 3
    for expected in ["接口", "灰度", "错误率", "回滚", "owner", "SLO", "P99"]:
        assert expected in result["matched_entities"]


def test_asr_semantic_quality_blocks_gibberish_transcript():
    result = evaluate_semantic_quality("啊嗯这个那个然后就是可以吧可以吧一二三四五六七八。")

    assert result["status"] == "blocked"
    assert result["blocker"] == "asr_semantic_quality_blocked"
    assert result["technical_entity_hit_count"] == 0


def test_asr_semantic_quality_blocks_fluent_but_non_technical_chinese():
    result = evaluate_semantic_quality("今天天气不错，我们吃饭聊天，然后大家都很开心，下午去散步。")

    assert result["status"] in {"passed", "warning"}
    assert result["blocker"] is None
    assert "release_control" not in result["matched_entity_groups"]
    assert "reliability" not in result["matched_entity_groups"]
    assert result["quality_warning"] == "technical_context_not_detected"


def test_asr_semantic_quality_allows_short_high_signal_incident_sentence():
    result = evaluate_semantic_quality("P99 超了，先回滚。")

    assert result["status"] == "passed"
    assert result["technical_entity_hit_count"] >= 2
    assert "reliability" in result["matched_entity_groups"]
    assert "release_control" in result["matched_entity_groups"]


def test_asr_semantic_quality_recognizes_common_chinese_backend_meeting_terms():
    result = evaluate_semantic_quality(
        "数据库连接池打满，Redis 缓存穿透，Kafka 消费堆积，P95 延迟飙升，王五今天处理限流和幂等。"
    )

    assert result["status"] == "passed"
    for expected in ["数据库", "Redis", "缓存", "Kafka", "P95", "限流", "幂等"]:
        assert expected in result["matched_entities"]
    assert "data_infra" in result["matched_entity_groups"]
    assert "reliability" in result["matched_entity_groups"]


def test_asr_semantic_quality_accepts_general_software_architecture_discussion():
    result = evaluate_semantic_quality(
        "可以啊，我记得有个 tool 出错了。公司里那些工具是封装成 SDK，还是提供一个 toolkit？"
    )

    assert result["status"] == "passed"
    assert result["blocker"] is None
    assert "software_architecture" in result["matched_entity_groups"]
    assert "development_workflow" in result["matched_entity_groups"]
    for expected in ["工具", "SDK", "toolkit", "出错"]:
        assert expected in result["matched_entities"]


def test_asr_semantic_quality_accepts_product_and_client_design_discussion():
    result = evaluate_semantic_quality(
        "这个客户端页面的交互流程需要调整，登录权限报错后要保留用户配置并补自动化测试。"
    )

    assert result["status"] == "passed"
    assert "product_design" in result["matched_entity_groups"]
    assert "development_workflow" in result["matched_entity_groups"]


def test_asr_semantic_quality_accepts_focused_permission_and_bug_discussion():
    result = evaluate_semantic_quality(
        "我不知道有没有这个版本的登录权限，最近是不是又出了一个 bug？"
    )

    assert result["status"] == "passed"
    assert result["reason"] == "technical_single_group_threshold_met"
    assert result["matched_entity_groups"] == ["development_workflow"]
    for expected in ["bug", "登录", "权限", "版本"]:
        assert expected in result["matched_entities"]


def test_asr_semantic_quality_blocks_single_isolated_technical_word():
    result = evaluate_semantic_quality("大家先等一下，那个 bug 回头再说。")

    assert result["status"] in {"passed", "warning"}
    assert result["blocker"] is None
    assert result["technical_entity_hit_count"] == 1
    assert result["quality_warning"] == "technical_context_not_detected"


def test_asr_semantic_quality_blocks_mixed_language_fragmented_real_asr_output():
    result = evaluate_semantic_quality(
        "下能脱稿画出a卷的全链路能说出每一个组件的位置和作用被属黑准的主循环"
        "request到contest xt moden downtwo calling to methoc ine ofdel背熟midiwell"
        "le的六值和位置背书三三状态一个短期机一个常见机一外一个任务状态"
        "用白纸画出从request到sponse的网点图把本文档中的关键数语写一遍"
    )

    assert result["status"] == "blocked"
    assert result["blocker"] == "asr_semantic_quality_blocked"
    assert "mixed_language_fragmentation" in result["quality_failure_reasons"]


def test_normalized_fun_asr_release_fragments_pass_compound_term_quality_gate():
    normalized = normalize(
        "a这次tracout out service周五晚上灰度百分十十看err rate和p九b"
        "如果指标异常我们暂停扩量但回滚脚本还没有在凌晨older ker消费堆积lag最高到了八万"
    )

    result = evaluate_semantic_quality(normalized)

    assert result["status"] == "passed"
    assert result["unknown_latin_tokens"] == []
