import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { MeetingSpeaker, SemanticParagraph, TranscriptSegment } from "../../domain/events";
import { TranscriptPane } from "./TranscriptPane";

function segment(
  id: string,
  transcriptSeq: number,
  text: string,
  startedAtMs: number,
  endedAtMs: number,
): TranscriptSegment {
  return {
    meetingId: "meeting-transcript",
    segmentId: id,
    finalId: `final-${id}`,
    transcriptSeq,
    text,
    normalizedText: text,
    startedAtMs,
    endedAtMs,
    revision: 1,
    evidenceHash: `hash-${id}`,
    createdAtMs: endedAtMs,
    updatedAtMs: endedAtMs,
  };
}

const baseProps = {
  archivedTranscript: "",
  archivedSegmentCount: 0,
  activePartial: null,
  connection: "live",
};

function meetingSpeaker(
  speakerId: string,
  speakerLabel: string,
  labelSource: "auto" | "user" = "auto",
): MeetingSpeaker {
  return {
    meetingId: "meeting-transcript",
    speakerId,
    speakerLabel,
    labelSource,
    labelLocked: labelSource === "user",
    ordinal: 1,
    createdAtMs: 1,
    updatedAtMs: 1,
  };
}

describe("TranscriptPane", () => {
  it("shows a floating selection toolbar and preserves the selected evidence scope", async () => {
    const onSelectionChange = vi.fn();
    const onAskSelection = vi.fn();
    const onSaveSelection = vi.fn();
    render(
      <TranscriptPane
        {...baseProps}
        onSelectionChange={onSelectionChange}
        onAskSelection={onAskSelection}
        onSaveSelection={onSaveSelection}
        segments={[segment("s1", 1, "先确认发布窗口和负责人。", 0, 2_000)]}
      />,
    );

    const paragraph = screen.getByText("先确认发布窗口和负责人。", { selector: "p" });
    const textNode = paragraph.firstChild;
    expect(textNode).toBeTruthy();
    const range = document.createRange();
    range.setStart(textNode!, 0);
    range.setEnd(textNode!, 7);
    const browserSelection = window.getSelection();
    browserSelection?.removeAllRanges();
    browserSelection?.addRange(range);
    fireEvent.pointerUp(screen.getByTestId("transcript-scroll"));

    expect(await screen.findByRole("toolbar", { name: "选中文字操作" })).toBeVisible();
    expect(onSelectionChange).toHaveBeenCalledWith({
      text: "先确认发布窗口",
      segmentIds: ["s1"],
    });
    fireEvent.click(screen.getByRole("button", { name: "保存到笔记" }));
    expect(onSaveSelection).toHaveBeenCalledWith({ text: "先确认发布窗口", segmentIds: ["s1"] });
    fireEvent.click(screen.getByRole("button", { name: "提炼行动项" }));
    await waitFor(() => expect(onAskSelection).toHaveBeenCalledWith(
      { text: "先确认发布窗口", segmentIds: ["s1"] },
      "extract_action_items",
    ));
  });

  it("assembles adjacent ASR checkpoints into readable natural paragraphs without repeating text", () => {
    render(
      <TranscriptPane
        {...baseProps}
        segments={[
          segment("s1", 1, "我们先确认发布范围，", 0, 2_000),
          segment("s2", 2, "然后安排灰度和回滚负责人。", 2_500, 5_000),
          segment("s3", 3, "第二个议题是数据库迁移。", 9_000, 11_000),
        ]}
      />,
    );

    const paragraphs = document.querySelectorAll(".transcript-segment");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent("我们先确认发布范围，然后安排灰度和回滚负责人。");
    expect(screen.getAllByText(/灰度和回滚负责人/)).toHaveLength(1);
    expect(paragraphs[1]).toHaveTextContent("第二个议题是数据库迁移。");
  });

  it("groups thirty seconds of uninterrupted speech in the only reading projection", () => {
    render(
      <TranscriptPane
        {...baseProps}
        segments={[
          segment("s1", 1, "我们先确认本次发布范围和主要负责人。", 0, 15_000),
          segment("s2", 2, "接下来讨论灰度指标以及异常时的回滚动作。", 15_000, 30_000),
        ]}
      />,
    );

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(1);
    expect(screen.getByText(/我们先确认本次发布范围.*接下来讨论灰度指标/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "逐句" })).not.toBeInTheDocument();
  });

  it("does not turn dense fifteen second checkpoints into singleton reading blocks", () => {
    render(
      <TranscriptPane
        {...baseProps}
        segments={[
          segment("s1", 1, "先说明当前问题。接着补充原因。最后说明影响。", 0, 15_000),
          segment("s2", 2, "继续讨论解决路径。然后确认约束。最后给出下一步。", 15_000, 30_000),
          segment("s3", 3, "第二个议题先说明背景。接着讨论风险。最后等待确认。", 30_000, 45_000),
        ]}
      />,
    );

    const paragraphs = document.querySelectorAll(".transcript-segment");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent("先说明当前问题");
    expect(paragraphs[0]).toHaveTextContent("继续讨论解决路径");
    expect(paragraphs[1]).toHaveTextContent("第二个议题先说明背景");
  });

  it("states that pending correction is showing raw recognition when AI is unavailable", () => {
    render(
      <TranscriptPane
        {...baseProps}
        segments={[{
          ...segment("s1", 1, "原始识别文字", 0, 2_000),
          correctionStatus: "pending",
        }]}
        aiIndicator={{
          state: "paused",
          label: "AI 已暂停",
          level: null,
          detail: "LLM Provider 不可用，实时理解已暂停",
        }}
      />,
    );

    expect(screen.getByText("识别原文")).toBeVisible();
    expect(screen.queryByText("等待校对")).not.toBeInTheDocument();
  });

  it("uses durable semantic paragraphs as the single visible transcript projection", () => {
    const segments = [
      segment("s1", 1, "原始第一段", 0, 1_000),
      { ...segment("s2", 2, "先挥百分之五", 1_200, 2_000), correctionStatus: "changed" as const },
    ];
    const paragraphs: SemanticParagraph[] = [{
      meetingId: "meeting-transcript",
      paragraphId: "paragraph-1",
      revision: 2,
      text: "先灰度百分之五，再观察错误率。",
      startMs: 0,
      endMs: 2_000,
      status: "stable",
      checkpointIds: ["s1", "s2"],
      createdAtMs: 1_000,
      updatedAtMs: 2_500,
    }];

    render(<TranscriptPane {...baseProps} segments={segments} semanticParagraphs={paragraphs} />);

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(1);
    expect(screen.getByText("先灰度百分之五，再观察错误率。")).toBeVisible();
    expect(screen.queryByText("原始第一段")).not.toBeInTheDocument();
    expect(screen.getByText("已校对")).toBeVisible();
  });

  it("does not repeat an active partial already covered by a 45 second durable paragraph", () => {
    const checkpoints = [
      segment("checkpoint-00", 1, "我们先确认本次发布只覆盖核心链路，", 0, 15_000),
      segment("checkpoint-15", 2, "灰度期间持续观察错误率和延迟，", 15_000, 30_000),
      segment("checkpoint-30", 3, "如果指标越线就由值班负责人执行回滚。", 30_000, 45_000),
    ];
    const canonicalText = checkpoints.map((checkpoint) => checkpoint.text).join("");
    const paragraphs: SemanticParagraph[] = [{
      meetingId: "meeting-transcript",
      paragraphId: "paragraph-continuous-45s",
      revision: 3,
      text: canonicalText,
      startMs: 0,
      endMs: 45_000,
      status: "active",
      checkpointIds: checkpoints.map((checkpoint) => checkpoint.segmentId),
      createdAtMs: 15_000,
      updatedAtMs: 45_000,
    }];

    render(
      <TranscriptPane
        {...baseProps}
        segments={checkpoints}
        semanticParagraphs={paragraphs}
        activePartial={{
          segmentId: "checkpoint-30",
          text: canonicalText,
          startedAtMs: 30_000,
          updatedAtMs: 45_000,
        }}
      />,
    );

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(1);
    expect(screen.getAllByText(canonicalText)).toHaveLength(1);
    expect(document.querySelector(".active-partial")).not.toBeInTheDocument();
  });

  it("keeps automatic speaker labels hidden until the user enables the experimental view", async () => {
    const user = userEvent.setup();
    render(
      <TranscriptPane
        {...baseProps}
        segments={[
          {
            ...segment("s1", 1, "我们先确认发布范围，", 0, 2_000),
            speakerId: "cluster-a",
            speakerLabel: "Speaker 1",
            speakerConfidence: 0.91,
          },
          {
            ...segment("s2", 2, "我来负责回滚预案。", 2_200, 4_000),
            speakerId: "cluster-b",
            speakerLabel: "Speaker 2",
            speakerConfidence: 0.52,
          },
        ]}
        speakers={[
          meetingSpeaker("cluster-a", "Speaker 1"),
          { ...meetingSpeaker("cluster-b", "Speaker 2"), ordinal: 2 },
        ]}
      />,
    );

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(1);
    expect(screen.queryByText("Speaker 1")).not.toBeInTheDocument();
    expect(screen.queryByText("Speaker 2")).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "实验说话人" }));
    expect(screen.getByText("Speaker 1")).toBeVisible();
    expect(screen.getByText("Speaker 2")).toBeVisible();
    expect(screen.getByLabelText("自动说话人置信度较低")).toBeVisible();
    expect(screen.queryByText(/张工|李工|真实姓名/)).not.toBeInTheDocument();
  });

  it("updates a semantic paragraph speaker in place without repeating its text", () => {
    const original = {
      ...segment("s1", 1, "确认灰度比例。", 0, 2_000),
      speakerId: "speaker-a",
      speakerLabel: "发言人 1",
      speakerConfidence: 0.82,
      speakerAttributionRevision: 1,
    };
    const paragraphs: SemanticParagraph[] = [{
      meetingId: "meeting-transcript",
      paragraphId: "paragraph-speaker",
      revision: 1,
      text: "确认灰度比例。",
      startMs: 0,
      endMs: 2_000,
      status: "stable",
      checkpointIds: ["s1"],
      speakerId: "speaker-a",
      speakerLabel: "发言人 1",
      speakerConfidence: 0.82,
      createdAtMs: 1_000,
      updatedAtMs: 2_000,
    }];
    const { rerender } = render(
      <TranscriptPane
        {...baseProps}
        segments={[original]}
        semanticParagraphs={paragraphs}
        speakers={[meetingSpeaker("speaker-a", "发言人 1", "user")]}
      />,
    );

    rerender(
      <TranscriptPane
        {...baseProps}
        segments={[{
          ...original,
          speakerId: "speaker-b",
          speakerLabel: "发言人 2",
          speakerConfidence: 0.93,
          speakerAttributionRevision: 2,
        }]}
        semanticParagraphs={paragraphs}
        speakers={[meetingSpeaker("speaker-b", "发言人 2", "user")]}
      />,
    );

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(1);
    expect(screen.getAllByText("确认灰度比例。")).toHaveLength(1);
    expect(screen.getByText("发言人 2")).toBeVisible();
    expect(screen.queryByText("发言人 1")).not.toBeInTheDocument();
  });

  it("does not invent a speaker for a mixed or unknown semantic paragraph", () => {
    const paragraphs: SemanticParagraph[] = [{
      meetingId: "meeting-transcript",
      paragraphId: "paragraph-mixed",
      revision: 1,
      text: "两位参会者连续发言。",
      startMs: 0,
      endMs: 4_000,
      status: "stable",
      checkpointIds: ["s1", "s2"],
      speakerId: "stale-speaker",
      speakerLabel: "不应显示的旧标签",
      speakerConfidence: 0.9,
      createdAtMs: 1_000,
      updatedAtMs: 4_000,
    }];
    render(
      <TranscriptPane
        {...baseProps}
        segments={[
          {
            ...segment("s1", 1, "第一位发言。", 0, 2_000),
            speakerId: "speaker-a",
            speakerLabel: "发言人 1",
            speakerAttributionRevision: 1,
          },
          {
            ...segment("s2", 2, "第二位发言。", 2_000, 4_000),
            speakerId: "speaker-b",
            speakerLabel: "发言人 2",
            speakerAttributionRevision: 1,
          },
        ]}
        semanticParagraphs={paragraphs}
      />,
    );

    expect(screen.getByText("两位参会者连续发言。")).toBeVisible();
    expect(document.querySelector(".speaker-row")).not.toBeInTheDocument();
    expect(screen.queryByText(/不应显示的旧标签|未知说话人/)).not.toBeInTheDocument();
  });

  it("renames a stable speaker without breaking its transcript timestamp", async () => {
    const user = userEvent.setup();
    const onRenameSpeaker = vi.fn().mockResolvedValue(undefined);
    const onSeekAudio = vi.fn();
    render(
      <TranscriptPane
        {...baseProps}
        segments={[{
          ...segment("s1", 1, "确认灰度比例。", 5_000, 7_000),
          speakerId: "cluster-a",
          speakerLabel: "Speaker 1",
          speakerConfidence: 0.9,
        }]}
        speakers={[meetingSpeaker("cluster-a", "Speaker 1")]}
        onRenameSpeaker={onRenameSpeaker}
        onSeekAudio={onSeekAudio}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "实验说话人" }));
    await user.click(screen.getByRole("button", { name: "Speaker 1" }));
    const input = screen.getByRole("textbox", { name: "重命名 Speaker 1" });
    await user.clear(input);
    await user.type(input, "张工");
    await user.click(screen.getByRole("button", { name: "保存 Speaker 1 的名称" }));

    expect(onRenameSpeaker).toHaveBeenCalledWith("cluster-a", "张工");
    await user.click(screen.getByRole("button", { name: "在录音中定位到 00:05" }));
    expect(onSeekAudio).toHaveBeenCalledWith(5_000);
  });

  it("keeps the reader's historical scroll position and offers an explicit return to latest", async () => {
    const user = userEvent.setup();
    const initial = [
      segment("s1", 1, "第一段", 0, 1_000),
      segment("s2", 2, "第二段", 5_000, 6_000),
    ];
    const { rerender } = render(<TranscriptPane {...baseProps} segments={initial} />);
    const scroll = screen.getByTestId("transcript-scroll");
    Object.defineProperties(scroll, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 100 },
    });
    fireEvent.scroll(scroll);

    rerender(
      <TranscriptPane
        {...baseProps}
        segments={[...initial, segment("s3", 3, "第三段新内容", 10_000, 11_000)]}
      />,
    );

    const notice = await screen.findByTestId("transcript-new-content");
    expect(notice).toHaveTextContent("有 1 段新内容，回到最新");
    expect(scroll.scrollTop).toBe(100);

    await user.click(notice);
    expect(scroll.scrollTop).toBe(1_000);
    expect(within(scroll).queryByTestId("transcript-new-content")).not.toBeInTheDocument();
  });

  it("bounds a simulated one-hour meeting while search still finds an older loaded segment", () => {
    const segments = Array.from({ length: 1_200 }, (_, index) => segment(
      `long-${index + 1}`,
      index + 1,
      index === 20 ? "需要回看较早的容量结论" : `会议记录 ${index + 1}`,
      index * 3_000,
      index * 3_000 + 1_000,
    ));
    render(<TranscriptPane {...baseProps} segments={segments} mergeSegments={false} />);

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(500);
    expect(screen.getByText(/较早的 700 段已折叠/)).toBeVisible();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索会议文字" }), {
      target: { value: "容量结论" },
    });
    expect(screen.getByText("需要回看较早的容量结论")).toBeVisible();
    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(1);
  });
});
