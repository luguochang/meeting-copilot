from __future__ import annotations

import re
from typing import Any


BLOCKER = "asr_semantic_quality_blocked"

ENTITY_GROUPS: dict[str, list[str]] = {
    "release_control": ["接口", "API", "灰度", "回滚", "发布", "上线", "变更", "开关", "feature flag", "限流", "降级"],
    "reliability": [
        "错误率", "SLO", "SLA", "P99", "P 99", "P95", "P 95", "P90", "P 90", "延迟",
        "告警", "监控", "看板", "超时", "QPS", "吞吐", "堆积", "可用性", "连接池",
    ],
    "data_infra": [
        "数据库", "MySQL", "PostgreSQL", "Redis", "缓存", "Kafka", "MQ", "消息队列",
        "连接池", "索引", "慢查询", "幂等", "消费堆积", "缓存穿透", "缓存击穿", "缓存雪崩",
    ],
    "ownership": ["owner", "负责人", "张三", "李四", "王五", "谁负责", "值班", "oncall"],
    "deadline": ["deadline", "今天", "明天", "周一", "周二", "周三", "周四", "周五", "下午", "上午", "下周", "本周", "排期"],
    "action": ["确认", "补充", "修复", "测试", "回归", "处理", "评估", "排查", "跟进", "验证", "复盘", "对齐", "落地"],
    "software_architecture": [
        "SDK", "toolkit", "tool", "工具", "封装", "组件", "模块", "服务", "架构",
        "插件", "依赖", "代码库", "类库", "框架",
    ],
    "development_workflow": [
        "bug", "出错", "报错", "调试", "编译", "构建", "部署", "开发", "代码", "Git", "PR",
        "CI", "CD", "登录", "权限", "配置", "环境", "版本", "自动化",
    ],
    "product_design": [
        "客户端", "页面", "交互", "流程", "需求", "产品", "功能", "用户", "App", "Web",
        "移动端", "桌面端",
    ],
}

MIN_TECHNICAL_ENTITY_HITS = 2
MIN_TECHNICAL_GROUP_HITS = 2
MIN_FRAGMENTED_LATIN_TOKENS = 8
MIN_UNKNOWN_LATIN_TOKENS = 5
MIN_UNKNOWN_LATIN_RATIO = 0.45
FOCUSED_TECHNICAL_GROUPS = {
    "release_control",
    "reliability",
    "data_infra",
    "software_architecture",
    "development_workflow",
    "product_design",
}

# Chinese meetings routinely contain English technical terms. Only tokens in
# this vocabulary are treated as expected; unknown fragments are a signal, not
# proof, that the ASR output is phonetic noise.
KNOWN_LATIN_TOKENS = {
    "agent", "ai", "api", "app", "architecture", "asr", "backend", "browser",
    "build", "bug", "cache", "card", "calling", "cd", "ci", "client", "code",
    "component", "components", "config", "context", "cpu", "database", "demo",
    "deploy", "deployment", "desktop", "docker", "event", "fastapi", "feature",
    "flag", "frontend", "framework", "funasr", "git", "github", "gpt", "gpu",
    "hitl", "http", "https", "json", "kafka", "language", "llm", "mac", "mcp",
    "meeting", "middleware", "model", "module", "monitor", "mysql", "node",
    "oauth", "openai", "owner", "p95", "p99", "paraformer", "postgres",
    "postgresql", "pr", "production", "prompt", "provider", "python", "qps",
    "react", "redis", "release", "request", "response", "retry", "rollback",
    "runtime", "rust", "sdk", "sdd", "server", "service", "session", "slo",
    "sql", "sse", "state", "stream", "streaming", "system", "tauri", "tdd",
    "test", "timeout", "token", "tool", "toolkit", "transcript", "typescript",
    "ui", "url", "ux", "version", "web", "websocket", "windows", "worker",
    "checkout", "error", "rate", "order", "lag",
    "workbench",
    # Common function words are expected when a Chinese meeting quotes an API
    # or reads an English identifier aloud.
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "can", "do",
    "for", "from", "go", "in", "is", "it", "long", "no", "not", "of", "ok",
    "on", "one", "or", "still", "the", "to", "two", "with",
}
LATIN_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def evaluate_semantic_quality(text: str) -> dict[str, Any]:
    transcript = str(text or "").strip()
    normalized = _normalize(transcript)
    matched_entities: list[str] = []
    matched_groups: list[str] = []

    for group, entities in ENTITY_GROUPS.items():
        group_matches: list[str] = []
        for entity in entities:
            if _normalize(entity) in normalized:
                group_matches.append(entity)
        if group_matches:
            matched_groups.append(group)
            matched_entities.extend(group_matches)

    matched_entities = _dedupe_preserving_order(matched_entities)
    technical_entity_hits = len(matched_entities)
    technical_group_hits = len(matched_groups)
    gibberish_score = _gibberish_score(transcript, technical_entity_hits=technical_entity_hits)
    fragmentation = _mixed_language_fragmentation(transcript)
    multi_group_passed = (
        technical_entity_hits >= MIN_TECHNICAL_ENTITY_HITS
        and technical_group_hits >= MIN_TECHNICAL_GROUP_HITS
    )
    focused_single_group_passed = (
        technical_entity_hits >= MIN_TECHNICAL_ENTITY_HITS
        and technical_group_hits == 1
        and matched_groups[0] in FOCUSED_TECHNICAL_GROUPS
    )
    quality_failure_reasons: list[str] = []
    if fragmentation["blocked"]:
        quality_failure_reasons.append("mixed_language_fragmentation")
    likely_non_speech = _likely_non_speech(
        transcript,
        gibberish_score=gibberish_score,
    )
    if likely_non_speech:
        quality_failure_reasons.append("likely_non_speech")

    hard_quality_failure = bool(quality_failure_reasons)
    technical_context_detected = multi_group_passed or focused_single_group_passed
    if hard_quality_failure:
        status = "blocked"
        blocker = BLOCKER
        reason = quality_failure_reasons[0]
        quality_warning = None
    elif technical_context_detected:
        status = "passed"
        blocker = None
        reason = (
            "technical_entity_threshold_met"
            if multi_group_passed
            else "technical_single_group_threshold_met"
        )
        quality_warning = None
    else:
        # Domain coverage is useful for choosing a better prompt, but it is not
        # evidence that the ASR audio is unusable. General meetings must still
        # be allowed to reach correction, suggestions, and minutes.
        status = "warning"
        blocker = None
        reason = "technical_context_not_detected"
        quality_warning = "technical_context_not_detected"
    return {
        "schema_version": "asr_semantic_quality.v1",
        "policy_version": "general_chinese_technical_meeting.v3",
        "status": status,
        "blocker": blocker,
        "matched_entities": matched_entities,
        "matched_entity_groups": matched_groups,
        "missing_entity_groups": [
            group for group in ENTITY_GROUPS.keys() if group not in set(matched_groups)
        ],
        "technical_entity_hit_count": technical_entity_hits,
        "technical_group_hit_count": technical_group_hits,
        "gibberish_score": gibberish_score,
        "latin_token_count": fragmentation["latin_token_count"],
        "unknown_latin_token_count": fragmentation["unknown_latin_token_count"],
        "unknown_latin_tokens": fragmentation["unknown_latin_tokens"],
        "mixed_language_fragmentation_score": fragmentation["score"],
        "quality_failure_reasons": quality_failure_reasons,
        "reason": reason,
        "quality_warning": quality_warning,
    }


def _normalize(value: str) -> str:
    return "".join(str(value or "").lower().split())


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = _normalize(value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _gibberish_score(text: str, *, technical_entity_hits: int) -> float:
    stripped = "".join(str(text or "").split())
    if not stripped:
        return 1.0
    filler_terms = ["啊", "嗯", "额", "这个", "那个", "然后", "就是", "可以吧"]
    filler_hits = sum(stripped.count(term) for term in filler_terms)
    repeated_penalty = 0.25 if len(set(stripped)) <= max(2, len(stripped) // 5) else 0.0
    score = min(1.0, filler_hits / max(len(stripped), 1) + repeated_penalty)
    if technical_entity_hits:
        score = max(0.0, score - 0.2)
    return round(score, 3)


def _likely_non_speech(text: str, *, gibberish_score: float) -> bool:
    """Detect the narrow class of filler/noise that should stop paid analysis.

    Missing technical vocabulary is intentionally not part of this decision.
    A general meeting can be perfectly intelligible while containing no
    software terms. This guard only catches the existing fixture shape: many
    fillers, repeated filler phrases, and a long numeric run.
    """
    stripped = "".join(str(text or "").split())
    if not stripped:
        return True
    filler_terms = ["啊", "嗯", "额", "这个", "那个", "然后", "就是", "可以吧"]
    repeated_filler = any(stripped.count(term) >= 2 for term in filler_terms)
    numeric_count = sum(
        character.isdigit() or character in "一二三四五六七八九十百千万"
        for character in stripped
    )
    return bool(
        gibberish_score >= 0.65
        or (gibberish_score >= 0.25 and repeated_filler and numeric_count >= 4)
    )


def _mixed_language_fragmentation(text: str) -> dict[str, Any]:
    tokens = [
        token.lower()
        for token in LATIN_TOKEN_RE.findall(str(text or ""))
        if len(token) > 1
    ]
    unknown_tokens = [token for token in tokens if token not in KNOWN_LATIN_TOKENS]
    token_count = len(tokens)
    unknown_count = len(unknown_tokens)
    unknown_ratio = unknown_count / token_count if token_count else 0.0
    blocked = (
        token_count >= MIN_FRAGMENTED_LATIN_TOKENS
        and unknown_count >= MIN_UNKNOWN_LATIN_TOKENS
        and unknown_ratio >= MIN_UNKNOWN_LATIN_RATIO
    )
    return {
        "blocked": blocked,
        "latin_token_count": token_count,
        "unknown_latin_token_count": unknown_count,
        "unknown_latin_tokens": _dedupe_preserving_order(unknown_tokens)[:24],
        "score": round(unknown_ratio, 3),
    }
