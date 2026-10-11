import assert from "node:assert/strict";
import test from "node:test";
import { safeFailureMetrics } from "../src/bridge.mjs";

test("JSONL failure metrics preserve only typed observed epoch boundaries", () => {
  const wire = JSON.parse(JSON.stringify(safeFailureMetrics({ metrics: { timings: {
    clock: "unix_epoch_ms", started_at_ms: 1000, first_token_at_ms: 1100, completed_at_ms: 1200,
    private_response: "must not be forwarded",
  }, private_body: "must not be forwarded" } })));
  assert.deepEqual(wire.timings, { clock: "unix_epoch_ms", started_at_ms: 1000,
    first_token_at_ms: 1100, completed_at_ms: 1200 });
  assert.equal(JSON.stringify(wire).includes("must not be forwarded"), false);
});

test("JSONL failure metrics retain absent tokens and reject arbitrary timing fields", () => {
  assert.deepEqual(safeFailureMetrics({ metrics: { timings: {
    clock: "unix_epoch_ms", started_at_ms: 1000, first_token_at_ms: null, completed_at_ms: 1200,
  } } }).timings, { clock: "unix_epoch_ms", started_at_ms: 1000,
    first_token_at_ms: null, completed_at_ms: 1200 });
  assert.deepEqual(safeFailureMetrics({ metrics: { timings: {
    clock: "unix_epoch_ms", started_at_ms: true, first_token_at_ms: "private", completed_at_ms: -1,
  } } }).timings, { clock: "unix_epoch_ms" });
});
