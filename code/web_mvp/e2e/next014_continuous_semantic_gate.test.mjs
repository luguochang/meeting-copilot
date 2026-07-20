import assert from "node:assert/strict";
import test from "node:test";

import {
  CONTINUOUS_WINDOW_MS,
  NEXT014_SCOPE,
  SYNTHETIC_TIMELINE_MS,
  buildNext014FixtureContract,
  evaluateNext014Report,
} from "./next014_continuous_semantic_gate.mjs";

function passingReport(overrides = {}) {
  const fixture = buildNext014FixtureContract();
  const continuousIds = fixture.continuous_checkpoint_ids;
  const paragraphs = [
    { paragraph_id: "history-1", text: "历史一" },
    { paragraph_id: "history-2", text: "历史二" },
    { paragraph_id: "history-3", text: "历史三" },
    { paragraph_id: "continuous", text: "连续发言全文" },
  ];
  return {
    acceptance_scope: NEXT014_SCOPE,
    synthetic: true,
    acceptance_eligible: false,
    natural_multi_speaker_replacement: false,
    timeline_ms: SYNTHETIC_TIMELINE_MS,
    continuous_window_ms: CONTINUOUS_WINDOW_MS,
    checkpoint_count: fixture.checkpoint_count,
    raw_checkpoint_count_before_end: fixture.checkpoint_count - 1,
    semantic_paragraphs_before_end: paragraphs,
    semantic_paragraphs_after_end: [...paragraphs, { paragraph_id: "tail", text: "尾部" }],
    continuous_projection: {
      paragraph_count: 1,
      paragraph_id: "continuous",
      checkpoint_count: continuousIds.length,
      checkpoint_ids: continuousIds,
      duration_ms: CONTINUOUS_WINDOW_MS,
    },
    active_partial_duplicate_count: 0,
    history_scroll: {
      before_top: 64,
      after_top: 64,
      locked: true,
      new_paragraph_count: 1,
      notice: "有 1 段新内容，回到最新",
    },
    full_text_before_end: "历史一历史二历史三连续发言全文",
    full_text_after_end: "历史一历史二历史三连续发言全文",
    ui_before_end: {
      row_count: paragraphs.length,
      raw_checkpoint_row_count: fixture.checkpoint_count - 1,
      rows: paragraphs,
    },
    ui_after_end: { row_count: 7, rows: [] },
    ...overrides,
  };
}

test("NEXT-014 fixture is a marked synthetic 45 second continuous window", () => {
  const fixture = buildNext014FixtureContract();

  assert.equal(fixture.scope, NEXT014_SCOPE);
  assert.equal(fixture.synthetic, true);
  assert.equal(fixture.acceptance_eligible, false);
  assert.equal(fixture.natural_multi_speaker_replacement, false);
  assert.equal(fixture.timeline_ms, 60_000);
  assert.equal(fixture.continuous_window_ms, 45_000);
  assert.deepEqual(fixture.continuous_checkpoint_ids, [
    "next014-continuous-00",
    "next014-continuous-15",
    "next014-continuous-30",
  ]);
});

test("passes the NEXT-014 non-acceptance contract when durable projection and scroll evidence close", () => {
  const report = evaluateNext014Report(passingReport());

  assert.equal(report.verdict, "passed_non_acceptance");
  assert.equal(report.acceptance_eligible, false);
  assert.equal(report.counts_as_real_release_go, false);
  assert.deepEqual(report.blockers, []);
});

test("fails when 15 second checkpoint rows leak into the live UI", () => {
  const candidate = passingReport({
    ui_before_end: {
      row_count: 7,
      raw_checkpoint_row_count: 7,
      rows: Array.from({ length: 7 }, (_, index) => ({ text: `checkpoint-${index}` })),
    },
    continuous_projection: {
      ...passingReport().continuous_projection,
      paragraph_count: 3,
    },
  });
  const report = evaluateNext014Report(candidate);

  assert.equal(report.verdict, "failed_non_acceptance");
  assert.ok(report.blockers.includes("mechanical_checkpoint_fragments_visible"));
  assert.ok(report.blockers.includes("live_ui_is_not_durable_semantic_projection"));
});

test("fails when active partial is observed as duplicate durable text", () => {
  const report = evaluateNext014Report(passingReport({ active_partial_duplicate_count: 1 }));

  assert.equal(report.verdict, "failed_non_acceptance");
  assert.ok(report.blockers.includes("active_partial_repeated_durable_text"));
});

test("fails when historical scroll is pulled to latest or notice is absent", () => {
  const report = evaluateNext014Report(passingReport({
    history_scroll: {
      before_top: 64,
      after_top: 900,
      locked: false,
      new_paragraph_count: 0,
      notice: "",
    },
  }));

  assert.equal(report.verdict, "failed_non_acceptance");
  assert.ok(report.blockers.includes("history_scroll_position_changed"));
  assert.ok(report.blockers.includes("new_paragraph_notice_missing"));
});

test("fails when end-time canonical text differs from the live text", () => {
  const report = evaluateNext014Report(passingReport({ full_text_after_end: "结束时被替换的全文" }));

  assert.equal(report.verdict, "failed_non_acceptance");
  assert.ok(report.blockers.includes("canonical_full_text_changed_at_end"));
});
