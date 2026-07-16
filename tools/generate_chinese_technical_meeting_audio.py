#!/usr/bin/env python3
"""Generate a reproducible, Chinese-only technical meeting fixture on macOS.

The fixture is for local long-meeting validation. It intentionally avoids Latin
identifiers so ASR quality gates measure Chinese speech rather than phonetic
fragments from synthesized English terms.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess


TOPICS = (
    "发布流程和灰度策略",
    "服务稳定性和告警处理",
    "数据一致性和缓存策略",
    "消息处理和任务堆积",
    "接口超时和重试边界",
    "数据库索引和慢查询",
    "权限管理和审计记录",
    "客户端交互和会议状态",
    "录音保存和历史恢复",
    "实时文字和段落修正",
    "实时建议和证据引用",
    "会后纪要和行动项",
    "测试覆盖和回归流程",
    "部署变更和回滚准备",
    "监控指标和容量规划",
    "风险复盘和责任确认",
)
SUBJECTS = (
    "产品负责人",
    "后端同学",
    "前端同学",
    "测试同学",
    "值班同学",
    "数据同学",
    "客户端同学",
    "项目负责人",
)
ACTIONS = (
    "补充验收口径并记录证据",
    "确认负责人和完成时间",
    "拆分风险并建立回滚步骤",
    "检查异常路径并补充自动化测试",
    "核对线上数据并保留复盘材料",
    "把讨论结论写入会议记录",
    "确认失败时仍然保存原始内容",
    "评估影响范围并给出处理顺序",
)
CONDITIONS = (
    "错误率持续升高",
    "延迟超过约定阈值",
    "任务出现重复执行",
    "缓存没有及时更新",
    "录音或文字暂时不可用",
    "负责人没有明确回复",
    "发布后指标出现异常",
    "恢复流程没有完成验证",
)
DELIVERABLES = (
    "发布清单",
    "监控看板",
    "回滚方案",
    "测试报告",
    "会议纪要",
    "录音索引",
    "风险清单",
    "验收记录",
)
QUESTIONS = (
    "这个结论是否需要现场确认",
    "谁负责在会议结束前补齐材料",
    "异常发生时先保护数据还是先停止发布",
    "这个方案是否能在重启后继续执行",
    "我们是否已经覆盖了失败和恢复两条路径",
    "这项工作完成后由谁进行最终验收",
)


def build_meeting_script(paragraph_count: int = 240) -> str:
    if paragraph_count <= 0:
        raise ValueError("paragraph_count must be positive")
    paragraphs: list[str] = []
    for index in range(paragraph_count):
        topic = TOPICS[index % len(TOPICS)]
        subject = SUBJECTS[(index * 3) % len(SUBJECTS)]
        action = ACTIONS[(index * 5) % len(ACTIONS)]
        condition = CONDITIONS[(index * 7) % len(CONDITIONS)]
        deliverable = DELIVERABLES[(index * 11) % len(DELIVERABLES)]
        question = QUESTIONS[(index * 13) % len(QUESTIONS)]
        paragraphs.append(
            f"第{index + 1}轮我们继续讨论{topic}。{subject}提出当前需要{action}，"
            f"如果{condition}，就按照既定顺序暂停高风险操作，并在约定时间内完成{deliverable}。"
            f"会议中请明确记录证据、影响范围和下一步动作。最后请确认，{question}。"
        )
    return "\n\n".join(paragraphs) + "\n"


def generate_audio(*, output_dir: Path, paragraph_count: int = 240, voice: str = "Tingting", rate: int = 190) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = output_dir / "chinese-technical-meeting-script.txt"
    aiff_path = output_dir / "chinese-technical-meeting.aiff"
    wav_path = output_dir / "chinese-technical-meeting-16k.wav"
    script = build_meeting_script(paragraph_count)
    if re.search(r"[A-Za-z]", script):
        raise ValueError("generated script unexpectedly contains Latin letters")
    script_path.write_text(script, encoding="utf-8")
    subprocess.run(
        ["say", "-v", voice, "-r", str(rate), "-o", str(aiff_path), script],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(aiff_path), "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(wav_path),
        ],
        check=True,
    )
    return {
        "script": str(script_path),
        "aiff": str(aiff_path),
        "wav": str(wav_path),
        "paragraph_count": paragraph_count,
        "script_char_count": len(script),
        "voice": voice,
        "rate": rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--paragraph-count", type=int, default=240)
    parser.add_argument("--voice", default="Tingting")
    parser.add_argument("--rate", type=int, default=190)
    args = parser.parse_args()
    print(generate_audio(
        output_dir=args.output_dir,
        paragraph_count=args.paragraph_count,
        voice=args.voice,
        rate=args.rate,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
