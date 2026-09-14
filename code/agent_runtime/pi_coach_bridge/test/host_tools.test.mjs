import assert from "node:assert/strict";
import test from "node:test";
import { HostToolChannel } from "../src/host_tools.mjs";

test("host tools bind request and call ids and reject stale or duplicate responses", async () => {
  const sent = [];
  const channel = new HostToolChannel("run-1", (message) => sent.push(message));
  const result = channel.search({ query: "approval", max_results: 2 });
  assert.equal(sent[0].request_id, "run-1");
  const reply = { request_id: "run-1", call_id: sent[0].call_id, ok: true, results: [] };
  assert.equal(channel.receive({ ...reply, request_id: "other" }), false);
  assert.equal(channel.receive({ ...reply, call_id: "invented" }), false);
  assert.equal(channel.receive(reply), true);
  assert.deepEqual(await result, []);
  assert.equal(channel.receive(reply), false);
  channel.close();
  await assert.rejects(channel.search({ query: "approval" }));
});

test("host tools close pending work and do not leak host error text", async () => {
  const channel = new HostToolChannel("run-1", () => {});
  const pending = channel.search({ query: "approval" });
  const rejected = assert.rejects(pending, /cancelled/);
  channel.close();
  await rejected;
  const next = new HostToolChannel("run-2", () => {});
  const failure = next.search({ query: "approval" });
  next.receive({ request_id: "run-2", call_id: "evidence-1", ok: false, error: "private text" });
  await assert.rejects(failure, { message: "host evidence query rejected" });
});

test("host tools enforce four calls per evaluation", async () => {
  const channel = new HostToolChannel("run-1", () => {});
  for (let index = 1; index <= 4; index++) {
    const promise = channel.search({ query: "approval" });
    channel.receive({ request_id: "run-1", call_id: `evidence-${index}`, ok: true, results: [] });
    await promise;
  }
  await assert.rejects(channel.search({ query: "approval" }));
});

test("host tools expose a bounded transcript span on the same request channel", async () => {
  const sent = [];
  const channel = new HostToolChannel("run-span", (message) => sent.push(message));
  const pending = channel.readSpan({ segment_id: "seg-2", before: 1, after: 2 });
  assert.deepEqual(sent[0], {
    event: "host_tool_request",
    request_id: "run-span",
    call_id: "evidence-1",
    tool: "read_transcript_span",
    arguments: { segment_id: "seg-2", before: 1, after: 2 },
  });
  const result = [{ id: "seg-2", text: "exact" }];
  assert.equal(channel.receive({ request_id: "run-span", call_id: "evidence-1", ok: true, results: result }), true);
  assert.deepEqual(await pending, result);
});
