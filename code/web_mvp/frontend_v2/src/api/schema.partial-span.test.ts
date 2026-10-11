import { parseMeetingSnapshot } from "./schema";

const snapshot = { meeting_id: "public-draft", last_seq: 0, segments: [], suggestions: [] };

it.each(["ended_at_ms", "endedAtMs"])("preserves an owned snapshot partial's %s span", (key) => {
  const parsed = parseMeetingSnapshot({ ...snapshot, active_partial: {
    segment_id: "microphone:e2:public", text: "原句及新增尾部", started_at_ms: 1_000,
    [key]: 4_000, updated_at_ms: 10_000,
  } });
  expect(parsed.activePartial?.endedAtMs).toBe(4_000);
});

it("keeps legacy browser partials compatible when no end span is present", () => {
  const parsed = parseMeetingSnapshot({ ...snapshot, active_partial: {
    segment_id: "legacy", text: "未确认的原句", started_at_ms: 1_000, updated_at_ms: 2_000,
  } });
  expect(parsed.activePartial?.endedAtMs ?? null).toBeNull();
});

it.each(["snake", "camel"])("preserves %s truncated preview provenance", (format) => {
  const metadata = format === "snake"
    ? { text_truncated: true, source_text_characters: 5_000 }
    : { textTruncated: true, sourceTextCharacters: 5_000 };
  const parsed = parseMeetingSnapshot({ ...snapshot, active_partial: {
    segment_id: "same", text: "甲".repeat(4_000), started_at_ms: 1_000,
    ended_at_ms: 4_000, updated_at_ms: 5_000, ...metadata,
  } });
  expect(parsed.activePartial).toMatchObject({ textTruncated: true, sourceTextCharacters: 5_000 });
});
