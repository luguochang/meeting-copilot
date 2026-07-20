import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { isMeetingAudioContentUrl } from "./meeting_audio_url_contract.mjs";

const e2eDir = import.meta.dirname;
const mainlineScript = path.join(e2eDir, "workbench_v2_real_mic_mainline.mjs");
const gatewayScript = path.join(e2eDir, "fake_llm_gateway.mjs");
const fakeScope = "non_acceptance_fake_audio_fake_llm_mainline";
const realScope = "acceptance_real_mic_real_local_asr_real_relay_mainline";

test("meeting audio URL contract accepts overall, track, and mixed local playback", () => {
  const base = "http://127.0.0.1:8782";
  assert.equal(isMeetingAudioContentUrl("/v2/meetings/m-1/audio/content", base), true);
  assert.equal(
    isMeetingAudioContentUrl("/v2/meetings/m-1/audio/tracks/microphone/content?epoch=0", base),
    true,
  );
  assert.equal(
    isMeetingAudioContentUrl("/v2/meetings/m-1/audio/tracks/system_audio/content?epoch=1", base),
    true,
  );
  assert.equal(isMeetingAudioContentUrl("/v2/meetings/m-1/audio/mixed/mixed-1/content", base), true);
});

test("meeting audio URL contract rejects remote, unknown-track, and non-content URLs", () => {
  const base = "http://127.0.0.1:8782";
  assert.equal(isMeetingAudioContentUrl("https://example.com/v2/meetings/m-1/audio/content", base), false);
  assert.equal(isMeetingAudioContentUrl("/v2/meetings/m-1/audio/tracks/other/content", base), false);
  assert.equal(isMeetingAudioContentUrl("/v2/meetings/m-1/audio/tracks/microphone", base), false);
  assert.equal(isMeetingAudioContentUrl("/audio/content", base), false);
});

function passingReport() {
  return {
    acceptance_scope: fakeScope,
    live_partial_observed: true,
    live_final_observed: true,
    live_suggestion_observed: true,
    live_correction_observed: true,
    transcript_segment_count: 2,
    transcript_revision_count: 1,
    transcript_revision_event_count: 1,
    revised_segment_ids: ["scripted_segment_1"],
    transcript_segments: [
      {
        segment_id: "scripted_segment_1",
        text: "我们灰度百分之五验证 cheout outservice，异常时立即回滚。",
        normalized_text: "我们灰度百分之五验证 checkout-service，异常时立即回滚。",
        started_at_ms: 0,
        ended_at_ms: 600,
        revision: 2,
      },
      {
        segment_id: "scripted_segment_2",
        text: "如果 P99 延迟超过九百毫秒，需要确认回滚负责人和监控 owner。",
        normalized_text: "如果 P99 延迟超过九百毫秒，需要确认回滚负责人和监控 owner。",
        started_at_ms: 6_000,
        ended_at_ms: 6_600,
        revision: 1,
      },
    ],
    transcript_ui: {
      row_count: 2,
      all_rows_visible: true,
      canonical_text_match: true,
      corrected_ids_match: true,
      corrected_row_count: 1,
    },
    committed_suggestion_count: 0,
    follow_up_ready: true,
    review_jobs_complete: true,
    minutes_ready: true,
    approach_card_count: 1,
    index_ready: true,
    audio: {
      assembled: true,
      duration_ms: 1_000,
      chunk_count: 4,
      content_http_status: 200,
      content_bytes: 1_024,
    },
    provider: {
      asr_provider: "scripted_chinese_e2e_asr",
      asr_provider_mode: "mock",
      asr_is_mock: true,
      llm_called: true,
      llm_is_mock: true,
      gateway_base_url_kind: "local",
    },
    history_reopened: true,
    diagnostics: {
      runtime_exceptions: [],
      console_errors: [],
      network_failures: [],
      http_5xx: [],
    },
  };
}

async function evaluateScopeContract(report) {
  const root = await mkdtemp(path.join(tmpdir(), "mc-v2-scope-contract-"));
  const fixture = path.join(root, "report.json");
  try {
    await writeFile(fixture, JSON.stringify(report));
    const result = spawnSync(process.execPath, [mainlineScript, "--evaluate-scope-contract", fixture], {
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test("fake-audio/fake-LLM scope passes only as non-acceptance evidence", async () => {
  const report = await evaluateScopeContract(passingReport());

  assert.equal(report.verdict, "passed_non_acceptance");
  assert.equal(report.acceptance_eligible, false);
  assert.equal(report.counts_as_real_release_go, false);
  assert.deepEqual(report.blockers, []);
  assert.deepEqual(report.scripted_fixture, {
    exactly_two_transcript_segments: true,
    timestamps_valid: true,
    expected_single_typo_correction: true,
  });
});

test("Web-saved local gateway is non-acceptance evidence even without an is_mock flag", async () => {
  const candidate = passingReport();
  candidate.provider.llm_is_mock = false;

  const report = await evaluateScopeContract(candidate);

  assert.equal(report.verdict, "passed_non_acceptance");
  assert.deepEqual(report.blockers, []);
});

test("fake scope rejects a non-positive second timestamp and provider masquerading", async () => {
  const candidate = passingReport();
  candidate.transcript_segments[1].ended_at_ms = 1_200;
  candidate.provider = {
    asr_provider: "real_provider_label",
    asr_provider_mode: "real",
    asr_is_mock: false,
    llm_called: true,
    llm_is_mock: false,
    gateway_base_url_kind: "remote",
  };

  const report = await evaluateScopeContract(candidate);

  assert.equal(report.verdict, "failed_non_acceptance");
  assert.equal(report.counts_as_real_release_go, false);
  assert.ok(report.blockers.includes("non_acceptance_fake_scripted_timestamp_invalid"));
  assert.ok(report.blockers.includes("non_acceptance_fake_scripted_asr_missing"));
  assert.ok(report.blockers.includes("non_acceptance_fake_local_llm_missing"));
});

test("real mic scope still fails closed on fake ASR, fake relay, or browser network errors", async () => {
  const candidate = passingReport();
  candidate.acceptance_scope = realScope;
  candidate.diagnostics.network_failures.push({ url: "http://127.0.0.1/api", error: "net::ERR_FAILED" });

  const report = await evaluateScopeContract(candidate);

  assert.equal(report.verdict, "no_go");
  assert.equal(report.acceptance_eligible, true);
  assert.equal(report.counts_as_real_release_go, false);
  assert.ok(report.blockers.includes("real_local_asr_missing"));
  assert.ok(report.blockers.includes("real_relay_missing"));
  assert.ok(report.blockers.includes("browser_runtime_errors"));
});

test("fake gateway revises only the scripted typo paragraph", async (t) => {
  const port = 20_000 + (process.pid % 20_000);
  const gateway = spawn(process.execPath, [gatewayScript], {
    env: { ...process.env, MEETING_COPILOT_FAKE_LLM_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(async () => {
    if (gateway.exitCode === null && gateway.signalCode === null) gateway.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => gateway.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 2_000)),
    ]);
  });
  await waitForGateway(gateway);

  const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [
        { role: "system", content: "你是中文会议实时理解引擎。" },
        {
          role: "user",
          content: JSON.stringify({
            new_paragraphs: [
              { id: "scripted_segment_1", revision: 1, text: "验证 cheout outservice 后回滚。" },
              { id: "scripted_segment_2", revision: 1, text: "P99 超过九百毫秒。" },
            ],
          }),
        },
      ],
    }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  const content = JSON.parse(body.choices[0].message.content);

  assert.deepEqual(content.paragraph_revisions, [{
    target_id: "scripted_segment_1",
    expected_revision: 1,
    corrected_text: "验证 checkout-service 后回滚。",
    change_count: 1,
  }]);
  assert.equal(content.follow_up.evidence_segment_ids[0], "scripted_segment_1");

  const minutesResponse = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [
        { role: "system", content: "你是中文技术会议纪要生成器。" },
        { role: "user", content: "scripted transcript" },
      ],
    }),
  });
  const minutesBody = await minutesResponse.json();
  const minutes = JSON.parse(minutesBody.choices[0].message.content);
  assert.match(minutes.background, /非验收 scripted audio/);
  assert.doesNotMatch(minutes.background, /真实麦克风/);
});

async function waitForGateway(gateway) {
  let stdout = "";
  let stderr = "";
  gateway.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
  gateway.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    if (stdout.includes("fake_llm_gateway_started")) return;
    if (gateway.exitCode !== null) throw new Error(`fake gateway exited early: ${stderr}`);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`timed out waiting for fake gateway: ${stderr}`);
}
