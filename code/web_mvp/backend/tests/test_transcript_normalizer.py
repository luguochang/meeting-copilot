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


def test_normalize_does_not_inject_unspoken_service_owner_or_middleware():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "是技术建立去些第合性一度外分之五如果错误率超过零点一旧回<unk> "
        "缓存穿透需要网母今天处理科消费堆积导致<unk> P95延迟升高，"
        "第四股监控看板 payment gate 和<unk>看板"
    )

    for injected in ("checkout-service", "Redis", "Kafka", "王五", "李四", "SLO"):
        assert injected not in normalized
    assert "零点一就回滚" in normalized
    assert "缓存穿透" in normalized
    assert "消费堆积" in normalized
    assert "P95延迟升高" in normalized


def test_normalize_keeps_safe_local_term_fixes_without_entity_injection():
    tn.load_terms.cache_clear()

    normalized = tn.normalize("先恢度百分之五，零点一旧回滚，要确认限流合密等，P 九九延迟")

    assert normalized == "先灰度百分之五，零点一就回滚，要确认限流和幂等，P99延迟"


def test_normalize_collapses_spaces_inside_chinese_words_before_term_matching():
    tn.load_terms.cache_clear()

    normalized = tn.normalize("P95 延迟升高第四个监 控看板，需要一起观 察")

    assert "P95 延迟升高第四个监控看板" in normalized
    assert "一起观察" in normalized
    assert "监 控" not in normalized
    assert "观 察" not in normalized


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


def test_normalize_recovers_observed_real_browser_mic_chinese_technical_terms():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "华成是技术会议，checkout-service 先灰度百分之五。"
        "P 九五延迟升高，P 九九延迟和<unk>看板需要一起观察，"
        "这个风险如果没有<unk>会上要马上追问"
    )

    assert "我们开始技术会议" in normalized
    assert "P95延迟升高" in normalized
    assert "P99延迟和<unk>看板" in normalized
    assert "SLO" not in normalized
    assert "没有owner会上" in normalized


def test_normalize_recovers_contextual_entities_from_real_browser_mic_no_cost_run():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "是技术会里这个接口先灰度百分之五如果错误率超过零点一就回滚<unk> "
        "缓存穿透需要王五今天处理<unk> 消费堆积导致 P95 延迟升高第四个监"
        "控看板数据库连接池打满 要确认限流和幂等 第二段发布前请张三确认"
        "负责人和值班窗口明天上午补充回归测试提 P99 延迟和<unk> 看板需要一起观"
        "察这个风险如果没有会上要马上追问"
    )

    assert "这个接口先灰度" in normalized
    assert "零点一就回滚" in normalized
    assert "缓存穿透" in normalized
    assert "王五今天处理" in normalized
    assert "消费堆积" in normalized
    assert "P95 延迟升高第四个监控看板" in normalized
    assert "P99 延迟和<unk> 看板" in normalized
    assert "风险如果没有owner会上" in normalized
    for injected in ("checkout-service", "Redis", "Kafka", "李四", "SLO"):
        assert injected not in normalized


def test_normalize_recovers_current_sherpa_resource_probe_near_misses():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "是技术会理缺接口先恢度百分之五如果错误率超过零点一旧回滚<unk> "
        "款存穿透需要往五今天处理<unk> 消费堆积导致九五延迟升高第四股监控看板"
        "数据库连接持打满要确认现流和密等第二段发布前请张三确任负责人和值班窗口"
        "明天上午补充回归测试提九九延迟和<unk> 看板需要一起观察"
    )

    assert "先灰度百分之五" in normalized
    assert "如果错误率超过零点一就回滚" in normalized
    assert "缓存穿透" in normalized
    assert "需要往五今天处理" in normalized
    assert "消费堆积" in normalized
    assert "P95延迟升高第四股监控看板" in normalized
    assert "数据库连接池打满要确认限流和幂等" in normalized
    assert "请张三确认负责人和值班窗口" in normalized
    assert "提 P99延迟和<unk> 看板" in normalized
    for injected in ("checkout-service", "Redis", "Kafka", "王五", "李四", "SLO"):
        assert injected not in normalized


def test_normalize_recovers_order_worker_only_in_backlog_context():
    tn.load_terms.cache_clear()

    normalized = tn.normalize("a凌晨autoker消费堆积lag最高到了八万，告警延迟六分钟")

    assert "order-worker消费堆积lag" in normalized
    assert tn.normalize("autoker 普通闲聊") == "autoker 普通闲聊"


def test_normalize_recovers_release_review_visible_near_misses_in_metric_context():
    tn.load_terms.cache_clear()

    normalized = tn.normalize("这次 check outservice 周五晚上灰度 10% 先看 error r ate 和 P99")

    assert normalized == "这次 checkout-service 周五晚上灰度 10% 先看 error_rate 和 P99"
    assert tn.normalize("check outservice 只是一个无上下文短语") == "check outservice 只是一个无上下文短语"
    assert tn.normalize("error r ate 只是一个拼写练习") == "error r ate 只是一个拼写练习"


def test_normalize_recovers_short_fun_asr_release_endpoint_fragments():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "a这次tracout out service周五晚上灰度百分十十看error rate和p九b"
        "如果指标异常我们暂停扩量但回滚脚本还没有在凌晨older ker消费堆积lag最高到了八万"
    )

    assert "checkout-service" in normalized
    assert "百分之十" in normalized
    assert "error_rate" in normalized
    assert "P99" in normalized
    assert "order-worker消费堆积lag" in normalized


def test_normalize_recovers_speech_active_real_mic_near_misses():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "但所以这就是是技术建立去些第合性一度外分之五如果错误率超过零点一旧回<unk> "
        "缓存穿透需要网母今天处理科消费堆积导致<unk> P95延迟升高，"
        "李四补监控看板数据控连坚持打满要确认限流合密等"
    )

    assert "是技术建立去些第合性一度外分之五" in normalized
    assert "如果错误率超过零点一就回滚" in normalized
    assert "缓存穿透" in normalized
    assert "需要网母今天处理科消费堆积" in normalized
    assert "P95延迟升高" in normalized
    assert "李四补监控看板数据库连接池打满要确认限流和幂等" in normalized
    for injected in ("checkout-service", "Redis", "Kafka", "王五"):
        assert injected not in normalized


def test_normalize_recovers_latest_real_browser_mic_release_review_terms():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "发布评审payment gate为先灰度百分之五如果p九延迟超过九百毫秒"
        "或者错误率超过百分之零点一就要立即回滚张三负责今天补slow看板"
        "李四确认ure flag和roll backor王五补充list和自动化测试"
    )

    assert "payment-gateway为先灰度百分之五" in normalized
    assert "P99延迟超过九百毫秒" in normalized
    assert "SLO看板" in normalized
    assert "feature flag" in normalized
    assert "rollback owner" in normalized
    assert "补充checklist和自动化测试" in normalized


def test_normalize_recovers_observed_truncated_ment_gate_term():
    tn.load_terms.cache_clear()

    normalized = tn.normalize("我们这次ment gate为先灰度百分之十")

    assert "payment-gateway" in normalized
    assert "ment gate" not in normalized


def test_normalize_payment_gateway_aliases_without_duplicate_prefix():
    tn.load_terms.cache_clear()

    truncated = tn.normalize("ment gate")
    complete = tn.normalize("payment gate")

    assert truncated == "payment-gateway"
    assert complete == "payment-gateway"
    assert "paypayment-gateway" not in complete


def test_normalize_keeps_latest_release_terms_contextual():
    tn.load_terms.cache_clear()

    assert tn.normalize("slow 慢一点看板先别改") == "slow 慢一点看板先别改"
    assert tn.normalize("这是普通 list，不是测试清单") == "这是普通 list，不是测试清单"


def test_normalize_recovers_second_real_browser_mic_release_review_variants():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "后发布评审paymentate为先灰度百分之五如果p九九延迟超过九百毫秒"
        "或者错误率超过百分之零点一就要立即回滚张三负责今天补slow碳板"
        "李四确认ure flag和onner王五补充cklist和自动化测试"
    )

    assert "payment-gateway为先灰度百分之五" in normalized
    assert "P99延迟超过九百毫秒" in normalized
    assert "pP99" not in normalized
    assert "SLO看板" in normalized
    assert "feature flag和owner" in normalized
    assert "补充checklist和自动化测试" in normalized


def test_normalize_recovers_third_real_browser_mic_release_review_variants():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "发布庭审为先灰度百分之五如果t九九延迟超过九百毫秒"
        "或者错误率超过百分之零点一就要立即回滚张三负责今天补斯隆看板"
        "李四确认ure flag和roll backor王五补充list和自动化测试"
    )

    assert "发布评审为先灰度百分之五" in normalized
    assert "P99延迟超过九百毫秒" in normalized
    assert "tP99" not in normalized
    assert "SLO看板" in normalized
    assert "feature flag和rollback owner" in normalized
    assert "补充checklist和自动化测试" in normalized


def test_normalize_recovers_fourth_real_browser_mic_release_review_variants():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "发布评审辩论称为先灰度百分之五如果t九延迟超过九百毫秒"
        "或者错误率超过百分之零点一就要立即回滚张三负责今天补四low看板"
        "李四确认ure flap和roll backor王五补充list和自动化测试"
        "发布评审ment为先灰度百分之五如果t一九延迟迟超过百百秒"
    )

    assert "P99延迟超过九百毫秒" in normalized
    assert "t九延迟" not in normalized
    assert "P99延迟迟超过百百秒" in normalized
    assert "t一九延迟" not in normalized
    assert "SLO看板" in normalized
    assert "feature flag和rollback owner" in normalized
    assert "补充checklist和自动化测试" in normalized


def test_normalize_recovers_ten_minute_real_mic_release_review_variants():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "第二段讨论发布评审payment-gateway先灰度度分之之五如果era r ate超过百分之零点一"
        "或者P99延迟超过九百毫秒就打开featureflag降级然后执行rollbacklist"
        "张三负责回滚honor李四负责SLO看板王五负责兼容性测试这里还有风险"
        "rediiscloster缓存穿透可能影响推荐服务caf collect如果超过三分钟需要通知值班群"
    )

    assert "error_rate超过百分之零点一" in normalized
    assert "feature flag降级" in normalized
    assert "rollback checklist" in normalized
    assert "回滚owner" in normalized
    assert "Redis cluster缓存穿透" in normalized
    assert "Kafka lag如果超过三分钟" in normalized


def test_normalize_keeps_ten_minute_variants_contextual():
    tn.load_terms.cache_clear()

    assert tn.normalize("honor 是一个英文单词") == "honor 是一个英文单词"
    assert tn.normalize("caf collect 是咖啡收集练习") == "caf collect 是咖啡收集练习"
    assert tn.normalize("era r ate 只是拼写练习") == "era r ate 只是拼写练习"


def test_normalize_recovers_post_overview_jump_real_mic_release_review_variants():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "如果错误率超过百分之零点一P99延迟超过两百毫秒就先回滚"
        "二然而张三负责flow看板李四负责ure fly王五负责roll back checklist"
        "稍后又说ononor张三负责SLO看板李四负责feature fly王五负责roll backlist"
        "最后识别成featureflag王五负责ll back checklist请继续补齐兼容性测试"
    )

    assert "SLO看板" in normalized
    assert "feature flag王五" in normalized
    assert "rollback checklist" in normalized
    assert "owner张三负责SLO看板" in normalized
    assert "flow看板" not in normalized
    assert "ure fly" not in normalized
    assert "feature fly" not in normalized
    assert "roll backlist" not in normalized
    assert "ll back checklist" not in normalized
    assert tn.normalize("普通 flow 看板和 fly 练习不用改") == "普通 flow 看板和 fly 练习不用改"


def test_normalize_recovers_ten_minute_v5_real_mic_remaining_variants():
    tn.load_terms.cache_clear()

    normalized = tn.normalize(
        "张三负责SLO看板李四负责picture er王五负责row backst第六部分讨论工程风险"
        "dicloster缓存穿透需要观察havect如果超过三分钟通知值班群"
        "稍后又说李四负责featurelag王五负责ll back checklist第六部分讨论工程风险"
        "Redis cluster缓存穿透需要观察Kafka lag如果超过三分钟通知值班群orourer消费堆积"
        "第八部分讨论下一步honor要在明天上午补齐兼容性测试"
        "第八分讨论下一步那or要在明天上午补齐兼容性测试"
        "如果ror 超过百分之零点一张三负责low看板王五负责rall back checklist"
        "工程风险需要观察haveg如超超过三钟通知值值群群"
    )

    assert "李四负责feature flag王五负责rollback checklist" in normalized
    assert "如果error_rate超过百分之零点一张三负责SLO看板" in normalized
    assert "王五负责rollback checklist" in normalized
    assert "观察Kafka lag如超超过三钟通知值值群群" in normalized
    assert "Redis cluster缓存穿透" in normalized
    assert "Kafka lag如果超过三分钟通知值班群" in normalized
    assert "order-worker消费堆积" in normalized
    assert "下一步owner要在明天上午补齐兼容性测试" in normalized
    for near_miss in (
        "picture er",
        "row backst",
        "dicloster",
        "havect",
        "featurelag",
        "ll back checklist",
        "orourer",
        "honor要在",
        "那or要在",
        "ror 超过",
        "low看板",
        "rall back checklist",
        "haveg如超",
    ):
        assert near_miss not in normalized

    assert tn.normalize("picture er 是普通图片练习") == "picture er 是普通图片练习"
    assert tn.normalize("havect 是无上下文拼写") == "havect 是无上下文拼写"
    assert tn.normalize("那or 是聊天口误") == "那or 是聊天口误"
