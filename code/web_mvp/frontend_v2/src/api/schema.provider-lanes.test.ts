import { HttpMeetingApi } from "./client";
import { ContractError, parseProviderStatus, reconcileProviderStatus } from "./schema";

const circuit = { state: "open", reason: "rate_limit", failure_count: 1, retry_after_ms: 1000, half_open: false, identity_generation: 1, last_failure_class: "rate_limit" };
const lane = {
  configured: true, enabled: true, model: "fast", model_source: "environment_realtime_model",
  transport: "pi_tool_call", circuit_status: "open", circuit, admission_group: "deep",
  active_count: 0, pending_count: 1, max_active: 1, probe_scope: "not_probed", probe_status: "not_run",
  last_result: { outcome: "timeout", http_status: null, age_ms: 100, scope: "process_history", configuration_match: "unknown", privateText: "private" },
};
const status = {
  configured: true, runtime_synced: true, probe_status: "succeeded", model: "general", realtime_model: "fast",
  operational: true, realtime_ready: true, probe_latency_ms: 100, realtime_cutoff_ms: 2500,
  probe_usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 },
};

it("parses lane observations with a strict content-free result allowlist", () => {
  const parsed = parseProviderStatus({ ...status, provider_lanes: { pi_deep: lane, unexpected: { privateText: "private" } } });
  expect(parsed.provider_lanes?.pi_deep).toMatchObject({ pending_count: 1, probe_status: "not_run", circuit_status: "open" });
  expect(JSON.stringify(parsed)).not.toContain("privateText");
  expect(parsed.provider_lanes).not.toHaveProperty("unexpected");
  expect(parseProviderStatus(status).provider_lanes).toBeUndefined();
});

it("allows effective readiness to be blocked by a real open circuit", () => {
  expect(parseProviderStatus({ ...status, realtime_ready: false, realtime_circuit: circuit }).realtime_ready).toBe(false);
  expect(() => parseProviderStatus({ ...status, realtime_ready: false })).toThrow(ContractError);
});

it("does not carry stale runtime lanes across an unsynced desktop config", () => {
  const runtime = parseProviderStatus({ ...status, provider_lanes: { pi_deep: lane } });
  const unsynced = reconcileProviderStatus({ configured: true, model: "other", realtime_model: "other", runtime_synced: false }, runtime);
  expect(unsynced.provider_lanes).toBeUndefined();
});

it("parses real capability response fields without confusing runtime and package installation", async () => {
  const capability = { available: true, state: "idle", source: "source_configuration", reason: "worker_starts_on_demand", process_ready: false, process_running: false };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    schema_version: "meeting_copilot.local_capability_status.v1", platform: "windows-x86_64", base_app_ready: true,
    installed: false, realtime_asr_ready: false, file_asr_ready: false, errors: [], deployment_mode: "source",
    runtime_capabilities: { realtime_asr: capability, file_asr: { ...capability, state: "available", formats: { wav: true, mp3: false } }, offline_refinement: capability },
  }), { status: 200, headers: { "content-type": "application/json" } })));
  const parsed = await new HttpMeetingApi().getLocalCapabilities();
  expect(parsed.installed).toBe(false);
  expect(parsed.runtimeCapabilities?.realtimeAsr).toMatchObject({ available: true, state: "idle", processReady: false });
  expect(parsed.runtimeCapabilities?.fileAsr.formats).toEqual({ wav: true, mp3: false });
  vi.unstubAllGlobals();
});
