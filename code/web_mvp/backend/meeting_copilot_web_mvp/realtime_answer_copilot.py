"""Low-latency question detection and prompt construction for Answer Copilot."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence


_SPACE_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_READABLE_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")
_QUESTION_MARK_RE = re.compile(r"[?？]")
_CHINESE_QUESTION_RE = re.compile(
    r"(?:为什么|为何|怎么|怎样|如何|什么|哪些|哪个|哪种|是否|是不是|能否|能不能|可否|"
    r"有没有|多少|多久|何时|什么时候|谁|哪里|哪儿|区别|差异|原因|怎么看|你认为|你觉得)"
)
_CHINESE_REQUEST_RE = re.compile(
    r"(?:请|麻烦)?(?:你)?(?:介绍|说说|谈谈|讲讲|解释|说明|描述|分享|分析|比较|对比|举例|"
    r"复盘|展开|概括|总结)(?:一下|下)?"
)
_ENGLISH_QUESTION_RE = re.compile(
    r"\b(?:who|what|when|where|why|how|which|can|could|would|will|do|does|did|is|are|"
    r"was|were|have|has|should|may)\b",
    re.IGNORECASE,
)
_ENGLISH_REQUEST_RE = re.compile(
    r"\b(?:tell me|walk me through|explain|describe|introduce|compare|share|give me an example|"
    r"talk about)\b",
    re.IGNORECASE,
)
_PERSONAL_EXPERIENCE_RE = re.compile(
    r"(?:你(?:负责|做过|参与|解决|遇到|经历|承担)|"
    r"你们(?:做过|解决|遇到)|"
    r"你的(?:项目|经历|成果|贡献|职责)|"
    r"这次项目|最困难|最有挑战|核心职责|个人贡献)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AnswerTrigger:
    should_answer: bool
    question_text: str | None
    reason: str
    confidence: float


def detect_answer_trigger(
    *,
    text: Any,
    source_track: Any,
    preset_id: Any = "general",
    allow_single_track_microphone: bool = False,
    single_track_focus_terms: Sequence[Any] = (),
) -> AnswerTrigger:
    """Detect a likely question on a remote or explicitly mixed single track.

    System audio is the authoritative remote lane. Browser-only meetings have
    no system-audio adapter, so their microphone can be treated as a mixed room
    track only when the meeting preparation explicitly says it is the sole
    input. Dual-track meetings never enable this fallback.
    """

    normalized = _normalize(text)
    track = str(source_track or "").strip().lower()
    preset = str(preset_id or "general").strip().lower()
    single_track_mixed = track == "microphone" and allow_single_track_microphone
    if track != "system_audio" and not single_track_mixed:
        return AnswerTrigger(False, None, "source_track_not_remote", 1.0)
    if len(normalized) < 3:
        return AnswerTrigger(False, None, "too_short", 1.0)
    if not _is_readable(normalized):
        return AnswerTrigger(False, None, "low_readability", 0.98)

    # A browser microphone is an intentionally conservative fallback: it mixes
    # the user, remote speakers and unrelated room audio. When the user has
    # supplied meeting vocabulary, require at least one of those terms before
    # allowing mixed audio to replace the visible Answer. Desktop/system audio
    # remains unrestricted because it has an authoritative remote lane.
    focus_keys = {
        key
        for value in single_track_focus_terms
        for key in _focus_keys(value)
    }
    if single_track_mixed and focus_keys:
        question_key = "".join(_READABLE_RE.findall(normalized.casefold()))
        if not any(key in question_key for key in focus_keys):
            return AnswerTrigger(False, None, "single_track_off_focus", 0.96)

    has_mark = bool(_QUESTION_MARK_RE.search(normalized))
    has_chinese_question = bool(_CHINESE_QUESTION_RE.search(normalized))
    has_chinese_request = bool(_CHINESE_REQUEST_RE.search(normalized))
    has_english_question = bool(_ENGLISH_QUESTION_RE.search(normalized))
    has_english_request = bool(_ENGLISH_REQUEST_RE.search(normalized))

    reason_prefix = "single_track_" if single_track_mixed else ""
    confidence_penalty = 0.10 if single_track_mixed else 0.0
    if has_mark:
        return AnswerTrigger(
            True,
            normalized,
            f"{reason_prefix}question_mark",
            0.98 - confidence_penalty,
        )
    if has_chinese_question or has_english_question:
        return AnswerTrigger(
            True,
            normalized,
            f"{reason_prefix}question_expression",
            0.92 - confidence_penalty,
        )
    if has_chinese_request or has_english_request:
        confidence = 0.94 if preset == "interview" else 0.86
        return AnswerTrigger(
            True,
            normalized,
            f"{reason_prefix}answer_request",
            confidence - confidence_penalty,
        )
    return AnswerTrigger(False, None, "no_question_boundary", 0.82)


def build_realtime_answer_messages(
    *,
    question_text: Any,
    context_segments: Sequence[Mapping[str, Any]],
    preset_id: Any = "general",
    meeting_goal: Any = None,
    participant_role: Any = None,
    focus_points: Sequence[Any] = (),
    single_track_mixed: bool = False,
) -> list[dict[str, str]]:
    """Build a bounded prompt whose first responsibility is a speakable answer."""

    question = _normalize(question_text)
    if not question:
        raise ValueError("question_text must not be empty")
    preset = str(preset_id or "general").strip().lower()
    if preset not in {"general", "decision", "project", "interview", "brainstorm"}:
        preset = "general"

    transcript: list[dict[str, str]] = []
    character_budget = 6_000
    for segment in reversed(list(context_segments)[-16:]):
        if not isinstance(segment, Mapping):
            continue
        content = _normalize(segment.get("normalized_text") or segment.get("text"))
        if not content:
            continue
        track = str(segment.get("source_track") or "").strip().lower()
        speaker = (
            "对方"
            if track == "system_audio"
            else "参会者（单轨混合）"
            if track == "microphone" and single_track_mixed
            else "我"
            if track == "microphone"
            else "参会者"
        )
        remaining = character_budget - sum(len(item["text"]) for item in transcript)
        if remaining <= 0:
            break
        transcript.append({"speaker": speaker, "text": content[-remaining:]})
    transcript.reverse()

    profile_guidance = {
        "interview": "采用结论先行或 STAR；优先给可直接口述的回答，不把回答写成面试教程。",
        "decision": "先给结论，再说明依据、约束和决策边界。",
        "project": "先回答当前状态，再给责任、风险和下一步。",
        "brainstorm": "先回应问题，再给最多三个有区分度的方向。",
        "general": "先直接回应，再补充必要依据和下一步。",
    }[preset]
    focus = [_normalize(item) for item in focus_points]
    focus = [item for item in focus if item][:12]
    payload = {
        "scene": preset,
        "current_question": question,
        "meeting_goal": _normalize(meeting_goal) or None,
        "my_role": _normalize(participant_role) or None,
        "focus_points": focus,
        "recent_dialogue": transcript,
        "personal_experience_question": bool(_PERSONAL_EXPERIENCE_RE.search(question)),
        "speaker_attribution": "mixed_single_track" if single_track_mixed else "track_based",
        "user_fact_available": (
            not single_track_mixed
            and any(item["speaker"] == "我" for item in transcript)
        ),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是会议中的实时回答副驾。只输出用户现在可以直接说出口的中文回答，第一句就回答问题；"
                "不要输出标题、JSON、分析过程、会议摘要，也不要说‘你可以说’或‘建议回答’。"
                "默认 1 到 2 句、不超过 180 个汉字，适合 10 到 20 秒口述；"
                "只保留结论、最关键依据和一个边界，其他扩展交给深度教练。"
                "只能把上下文明确提供的经历、数据、公司和项目当成用户事实；资料不足时使用限定表达，"
                "明确需要补充的信息，绝不编造个人经历或数字。"
                "speaker_attribution= mixed_single_track 时，转写无法区分用户和对方，"
                "不得把任何单轨发言归因为用户的个人经历。"
                "如果 personal_experience_question 为 true 且 user_fact_available 为 false，"
                "不得写成‘我负责’‘我解决了’或‘这次项目’等既成事实；"
                "只能给出限定式回答并简短说明还需补充哪些真实信息。"
                + profile_guidance
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value)).strip()


def _is_readable(text: str) -> bool:
    compact = "".join(character for character in text if not character.isspace())
    if not compact:
        return False
    readable = len(_READABLE_RE.findall(compact))
    if readable / len(compact) < 0.55:
        return False
    cjk_count = len(_CJK_RE.findall(compact))
    if cjk_count >= 2:
        return True
    words = re.findall(r"[A-Za-z]+", compact)
    return bool(words) and max((len(word) for word in words), default=0) >= 2


def _focus_keys(value: Any) -> set[str]:
    """Return ASR-tolerant tokens for conservative single-track gating."""

    text = _normalize(value).casefold()
    keys = {"".join(_READABLE_RE.findall(text))}
    keys.update(word for word in re.findall(r"[a-z0-9]+", text) if len(word) >= 3)
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        if len(sequence) <= 2:
            keys.add(sequence)
            continue
        keys.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return {key for key in keys if len(key) >= 2}
