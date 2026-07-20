import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SemanticParagraph, TranscriptSegment } from "../../domain/events";
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

describe("TranscriptPane", () => {
  it("assembles adjacent ASR checkpoints into readable natural paragraphs without repeating text", () => {
    render(
      <TranscriptPane
        {...baseProps}
        segments={[
          segment("s1", 1, "我们先确认发布范围，", 0, 2_000),
          segment("s2", 2, "然后安排灰度和回滚负责人。", 2_500, 5_000),
          segment("s3", 3, "第二个议题是数据库迁移。", 8_000, 10_000),
        ]}
      />,
    );

    const paragraphs = document.querySelectorAll(".transcript-segment");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]).toHaveTextContent("我们先确认发布范围，然后安排灰度和回滚负责人。");
    expect(screen.getAllByText(/灰度和回滚负责人/)).toHaveLength(1);
    expect(paragraphs[1]).toHaveTextContent("第二个议题是数据库迁移。");
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
    expect(screen.getByText("AI 已校正")).toBeVisible();
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

  it("keeps different speakers in separate paragraphs and only hints at low confidence", () => {
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
      />,
    );

    expect(document.querySelectorAll(".transcript-segment")).toHaveLength(2);
    expect(screen.getByText("Speaker 1")).toBeVisible();
    expect(screen.getByText("Speaker 2")).toBeVisible();
    expect(screen.getByLabelText("说话人区分置信度较低")).toBeVisible();
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
      <TranscriptPane {...baseProps} segments={[original]} semanticParagraphs={paragraphs} />,
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
        onRenameSpeaker={onRenameSpeaker}
        onSeekAudio={onSeekAudio}
      />,
    );

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
      segment("s2", 2, "第二段", 4_000, 5_000),
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
        segments={[...initial, segment("s3", 3, "第三段新内容", 8_000, 9_000)]}
      />,
    );

    const notice = await screen.findByTestId("transcript-new-content");
    expect(notice).toHaveTextContent("有 1 段新内容，回到最新");
    expect(scroll.scrollTop).toBe(100);

    await user.click(notice);
    expect(scroll.scrollTop).toBe(1_000);
    expect(within(scroll).queryByTestId("transcript-new-content")).not.toBeInTheDocument();
  });
});
