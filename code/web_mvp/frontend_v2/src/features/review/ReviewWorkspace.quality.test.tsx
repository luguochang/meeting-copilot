import { fireEvent, render, screen, within } from "@testing-library/react";
import { parseMeetingAudio, parseMeetingSnapshot } from "../../api/schema";
import type { MeetingEvent, MeetingViewState } from "../../domain/events";
import { createInitialMeetingState, meetingReducer } from "../../domain/reducer";
import { ReviewWorkspace } from "./ReviewWorkspace";

const mid = "review-quality";
const cards = [{ card_id: "c1", card_type: "missing_assumption", suggestion_text: "已保留的分析内容", evidence_segment_ids: ["s1"] }];
function rawJob(kind: string, overrides: Record<string, unknown> = {}) {
  return { id: `${kind}-job`, meeting_id: mid, kind, status: "succeeded", attempts: 1,
    completed_at_ms: 2_000, updated_at_ms: 2_000, output: { degraded: false, correction_degraded: true }, ...overrides };
}
function rawSnapshot(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { meeting_id: mid, last_seq: 10, suggestions: [], runtime: { phase: "ended" },
    segments: Array.from({ length: 9 }, (_, index) => ({ meeting_id: mid, segment_id: `s${index + 1}`,
      final_id: `f${index + 1}`, transcript_seq: index + 1, text: `原始会议文字 ${index + 1}`,
      normalized_text: `原始会议文字 ${index + 1}`, revision: 1, evidence_hash: `h${index + 1}`,
      correction_status: index < 8 ? "no_change" : "failed_preserved_original",
      correction_error_class: index < 8 ? null : "correction_speech_mode_changed" })),
    minutes: { meeting_id: mid, job_id: "minutes-job", markdown: "## 会议内容\n完整生成的纪要",
      version: 1, status: "degraded", created_at_ms: 1_000, updated_at_ms: 2_000 },
    approach_cards: cards,
    approach_artifact: { job_id: "approach-job", created_at_ms: 1_000, updated_at_ms: 3_657 },
    review_jobs: { minutes: rawJob("minutes"), index: rawJob("index"),
      approach: rawJob("approach", { status: "cancelled", error_class: "deadline_exceeded", output: null }) },
    ...overrides };
}
function stateFrom(raw: Record<string, unknown>): MeetingViewState {
  const snapshot = parseMeetingSnapshot(raw);
  const state = meetingReducer(createInitialMeetingState(mid), { type: "snapshot.received", snapshot, receivedAtMs: 4_000 });
  return { ...state, fullTranscript: snapshot.segments, fullTranscriptState: "ready",
    audioDetail: parseMeetingAudio({ meeting_id: mid, status: "saved", assembled: true, chunks: [] }) };
}
function show(raw: Record<string, unknown>) {
  const state = stateFrom(raw);
  const result = render(<ReviewWorkspace state={state} onReloadTranscript={vi.fn()} onReloadAudio={vi.fn()}
    onExport={vi.fn().mockResolvedValue(undefined)} />);
  return { ...result, state, minutes: within(result.container.querySelector<HTMLElement>(".review-document")!),
    approach: within(result.container.querySelector<HTMLElement>(".review-considerations")!) };
}
function readyEvent(kind: "minutes" | "approach", jobId: string): MeetingEvent {
  return { meetingId: mid, seq: 11, eventId: "ready", type: `meeting.${kind}.ready`, aggregateType: kind,
    aggregateId: mid, occurredAtMs: 3_657, correlationId: null, causationId: jobId, idempotencyKey: "ready",
    publishedAtMs: null, payload: kind === "approach"
      ? { approach: { job_id: jobId, cards, created_at_ms: 1_000, updated_at_ms: 3_657, degraded: true } }
      : { minutes: { job_id: jobId, markdown: "后到的AI纪要", created_at_ms: 1_000, updated_at_ms: 3_657 } } };
}

describe("review quality follows native task and artifact provenance", () => {
  it("distinguishes 8/9 input coverage from successful minutes generation and keeps actual80%", () => {
    const view = show(rawSnapshot());
    expect(view.minutes.getByText("AI 初稿已生成")).toBeVisible();
    expect(view.minutes.queryByText("结果不完整")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("已校对 8/9 段");
    expect(screen.getByText("80%")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看未完成校对的原文" }));
    expect(screen.getByRole("tab", { name: "会议文字" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("原始会议文字 9");
  });

  it("shows generation degradation independently of complete input coverage", () => {
    const raw = rawSnapshot();
    raw.segments = (raw.segments as Record<string, unknown>[]).map((segment) => ({ ...segment, correction_status: "no_change", correction_error_class: null }));
    raw.review_jobs = { minutes: rawJob("minutes", { output: { degraded: true, correction_degraded: false } }) };
    const view = show(raw);
    expect(view.minutes.getByText("AI 生成已降级，请核对初稿")).toBeVisible();
    expect(screen.queryByText("部分文字仍为原始 ASR")).toBeNull();
  });

  it.each(["ready", "degraded"])("does not infer generation quality from legacy aggregate %s", (status) => {
    const raw = rawSnapshot();
    raw.minutes = { ...(raw.minutes as object), status };
    raw.review_jobs = { minutes: rawJob("minutes", { output: null }) };
    const view = show(raw);
    expect(view.minutes.getByText("AI 生成质量需核对")).toBeVisible();
    expect(view.minutes.queryByText("AI 初稿已生成")).toBeNull();
  });

  it("does not apply a new failed job's flags to an older minutes artifact", () => {
    const raw = rawSnapshot({ review_jobs: { minutes: rawJob("minutes", { id: "new-minutes", status: "failed", output: { degraded: false } }) } });
    const view = show(raw);
    expect(view.minutes.getByText("AI 生成质量需核对")).toBeVisible();
    expect(view.minutes.getByText("本轮未完成，已保留内容请核对。")).toBeVisible();
    expect(screen.queryByText(/会议纪要未能生成/)).toBeNull();
    expect(view.minutes.getByText("完整生成的纪要")).toBeVisible();
  });

  it("labels proven same-job late arrival using current artifact update time, not first creation", () => {
    const view = show(rawSnapshot());
    expect(view.approach.getByText("本轮未完成，已保留内容请核对。")).toBeVisible();
    expect(view.approach.getByText("本轮产物在超时后到达，请核对后使用。")).toBeVisible();
    expect(view.approach.getByText("已保留的分析内容")).toBeVisible();
    expect(screen.queryByText(/分析建议未能生成/)).toBeNull();
    expect(view.state.approach).toMatchObject({ jobId: "approach-job", createdAtMs: 1_000, updatedAtMs: 3_657 });
  });

  it.each(["old-approach", null])("retains cards without claiming late arrival for unmatched or unknown artifact identity (%s)", (jobId) => {
    const view = show(rawSnapshot({ approach_artifact: jobId ? { job_id: jobId, updated_at_ms: 3_657 } : null }));
    expect(view.approach.getByText("本轮未完成，已保留内容请核对。")).toBeVisible();
    expect(view.approach.queryByText(/超时后到达/)).toBeNull();
    expect(view.state.approach.jobId).toBe(jobId);
    if (!jobId) expect(view.state.approach.updatedAtMs).toBeNull();
  });

  it("does not identify a stale or matching artifact as late without actual timeproof", () => {
    const view = show(rawSnapshot({ approach_artifact: { job_id: "approach-job", updated_at_ms: 1_999 } }));
    expect(view.approach.getByText("本轮未完成，已保留内容请核对。")).toBeVisible();
    expect(view.approach.queryByText(/超时后到达/)).toBeNull();
  });

  it("keeps a running new task separate from retained cards and does not promote progress", () => {
    const view = show(rawSnapshot({ review_jobs: { minutes: rawJob("minutes"), index: rawJob("index"),
      approach: rawJob("approach", { id: "new-approach", status: "running", completed_at_ms: null }) } }));
    expect(view.approach.getByText("本轮正在处理，当前展示已保留内容。")).toBeVisible();
    expect(screen.getByText("80%")).toBeVisible();
    expect(screen.queryByText("100%")).toBeNull();
  });

  it("does not put AI quality tags on a saved user final", () => {
    const raw = rawSnapshot({ documents: { minutes: { document_id: "human", meeting_id: mid, document_kind: "minutes",
      revision: 3, dirty_state: "saved", ai_generated: { version: 1, content: { markdown: "AI初稿" } },
      user_final: { version: 2, modified: true, content: { markdown: "人工确认终稿" } } } },
      review_jobs: { minutes: rawJob("minutes", { output: { degraded: true, correction_degraded: true } }) } });
    const view = show(raw);
    expect(view.minutes.getByText("用户最终稿")).toBeVisible();
    expect(view.minutes.getByText("人工确认终稿")).toBeVisible();
    expect(view.minutes.queryByText(/结果不完整|AI 生成|AI 初稿已生成/)).toBeNull();
  });

  it("does not attach a different artifact's quality to a preserved AI document", () => {
    const raw = rawSnapshot({ documents: { minutes: { document_id: "older-ai", document_kind: "minutes", revision: 2,
      ai_generated: { version: 2, content: { markdown: "单独保留的旧AI文档" } } } } });
    const view = show(raw);
    expect(view.minutes.getByText("单独保留的旧AI文档")).toBeVisible();
    expect(view.minutes.getByText("AI 生成质量需核对")).toBeVisible();
    expect(view.minutes.queryByText("AI 初稿已生成")).toBeNull();
    expect(view.minutes.queryByText("原文校对未完成")).toBeNull();
  });

  it("does not promote unknown legacy quality flags supplied as strings", () => {
    const view = show(rawSnapshot({ review_jobs: { minutes: rawJob("minutes", { output: { degraded: "false", correction_degraded: "true" } }) } }));
    expect(view.minutes.getByText("AI 生成质量需核对")).toBeVisible();
    expect(view.minutes.queryByText("原文校对未完成")).toBeNull();
  });

  it.each(["cancelled", "running", "unknown"])("ready preserves identified %s task status while retaining artifact", (status) => {
    const state = stateFrom(rawSnapshot({ review_jobs: { approach: rawJob("approach", { status }) } }));
    const next = meetingReducer(state, { type: "events.received", events: [readyEvent("approach", "approach-job")], receivedAtMs: 4_000 });
    expect(next.reviewJobs.approach?.status).toBe(status);
    expect(next.approach).toMatchObject({ jobId: "approach-job", updatedAtMs: 3_657 });
    expect(next.approach.cards[0].suggestionText).toBe("已保留的分析内容");
  });

  it("does not complete a newer identified job from an old ready event or overwrite user final", () => {
    const raw = rawSnapshot({ review_jobs: { minutes: rawJob("minutes", { id: "new-minutes", status: "running" }) },
      documents: { minutes: { document_id: "human", document_kind: "minutes", revision: 3,
        user_final: { version: 2, modified: true, content: { markdown: "人工确认终稿" } } } } });
    const state = stateFrom(raw);
    const next = meetingReducer(state, { type: "events.received", events: [readyEvent("minutes", "minutes-job")], receivedAtMs: 4_000 });
    expect(next.reviewJobs.minutes).toEqual(state.reviewJobs.minutes);
    expect(next.documents?.minutes).toEqual(state.documents?.minutes);
    expect(next.minutes?.jobId).toBe("minutes-job");
  });

  it("keeps normal ready content and observes task success from its next durable snapshot", () => {
    const raw = rawSnapshot({ review_jobs: { approach: rawJob("approach", { status: "running", completed_at_ms: null }) } });
    const state = stateFrom(raw);
    const ready = meetingReducer(state, { type: "events.received", events: [readyEvent("approach", "approach-job")], receivedAtMs: 4_000 });
    expect(ready.reviewJobs.approach?.status).toBe("running");
    const snapshot = parseMeetingSnapshot(rawSnapshot({ last_seq: 11, review_jobs: { approach: rawJob("approach") } }));
    const done = meetingReducer(ready, { type: "snapshot.received", snapshot, receivedAtMs: 5_000 });
    expect(done.reviewJobs.approach?.status).toBe("succeeded");
    expect(done.approach.cards[0].suggestionText).toBe("已保留的分析内容");
  });
});
