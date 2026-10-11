"""Versioned role shared by realtime answers and bounded coach jobs."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence


COACH_ROLE_CONTRACT_VERSION = "talktrace.coach-role.v1"
COACH_ROLE_CONTRACT = (
    "你是当前用户的私人工作与对话副驾。根据用户角色、当前目标、对话场景和主动提示偏好，"
    "帮助用户推进正在讨论的事情；没有明确目标时，以当前问题或讨论事项为准。"
    "需要回答时给可直接说出口的回应；用户研究访谈的访谈者或主持者需要中立追问，"
    "不要替受访者编造答案。interview 表示用户研究访谈，不自动套用求职面试或 STAR。"
    "只有用户明确是求职候选人时，才帮助组织基于真实经历的面试回答。"
    "讨论方案时指出有意义的区别和权衡；日常交流不自动要求负责人、期限或验收标准。"
    "区分已核对的原话、待校对转写、有效资料、公共知识与 AI 旧答案。资料与原话保留来源；"
    "公共知识可用于解释和提议，不能伪装成用户经历或已达成决定；AI 旧答案不是事实权威。"
    "单轨、混音或匿名标签不能证明某句话由用户说出，身份不确定时使用中性表达。"
    "外放、录播、广告或第三方转述不能自动当成用户的发言或请求；不得替内容作者争论立场、"
    "训诫说教或把听到的议论改写成用户应表态的观点。只帮助用户本次真正待回应的事项。"
    "引述的提问、反问、同段已经自答的问题，以及尚未说完的碎片，先保持静默。"
    "关键姓名、数字、否定词、期限及完成状态未经核实时采用条件或澄清表达，不猜测丢失的语音。"
    "帮助应补充新依据、新判断或具体下一步；无增量时静默，问题解决后停止重复提醒。"
    "依据充分时直接回应，不固定使用‘先确认’。只使用当前允许的资料和工具，"
    "结果遵循当前作业输出契约，不展示内部推理过程，也不声称完成尚未执行的操作。"
)


def _text(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:maximum] or None


def bounded_context_items(
    values: Any, *, maximum_items: int = 12, maximum_characters: int = 200,
) -> list[str]:
    """Keep only explicit bounded text items, without interpreting arbitrary objects."""

    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    items: list[str] = []
    for value in values:
        item = _text(value, maximum=maximum_characters)
        if item and item not in items:
            items.append(item)
        if len(items) >= maximum_items:
            break
    return items


def extract_role_context(
    meeting_goal: Any,
    *,
    participant_role: Any = None,
    focus_points: Any = (),
) -> dict[str, Any]:
    """Resolve explicit fields first, then bounded legacy preparation fields.

    Older hosts encode labeled role/focus fields in a semicolon-separated goal.
    Only standalone, delimited labels are accepted. Plain prose is never used
    to infer who the user is, and the full goal is retained.
    """

    role = _text(participant_role, maximum=200)
    focus = bounded_context_items(focus_points)
    goal = _text(meeting_goal, maximum=2_000)
    if isinstance(meeting_goal, Mapping):
        role = role or _text(meeting_goal.get("participant_role"), maximum=200)
        focus = focus or bounded_context_items(meeting_goal.get("focus_points"))
        goal = next((
            value for key in ("meeting_goal", "goal", "objective")
            if (value := _text(meeting_goal.get(key), maximum=2_000))
        ), None)
        if goal is None:
            # Retain otherwise structured goals as data, never eval/repr input.
            goal = json.dumps(dict(meeting_goal), ensure_ascii=False, default=str)[:2_000]
    elif isinstance(meeting_goal, str):
        for part in re.split(r"[；;\n]", meeting_goal):
            match = re.fullmatch(r"\s*(参会角色|用户角色|participant_role|重点关注|关注点|focus_points)\s*[:：]\s*(.*?)\s*", part)
            if match is None:
                continue
            label, value = match.groups()
            if label in {"参会角色", "用户角色", "participant_role"}:
                role = role or _text(value, maximum=200)
            elif not focus:
                focus = bounded_context_items(re.split(r"[、,，]", value))
    return {"meeting_goal": goal, "participant_role": role, "focus_points": focus}


def explicit_job_candidate(participant_role: Any, meeting_goal: Any = None) -> bool:
    """A research-interview preset alone never establishes a job-candidate role."""

    role = _text(participant_role, maximum=200) or ""
    goal = _text(meeting_goal, maximum=2_000) or ""
    return bool(
        re.search(r"求职|应聘|面试候选人|job\s+(?:candidate|applicant)", role, re.IGNORECASE)
        or ("候选人" in role and re.search(r"求职|招聘|技术面试|求职面试", goal))
    )
