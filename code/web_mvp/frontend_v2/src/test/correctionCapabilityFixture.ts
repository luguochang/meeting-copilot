import type { MeetingCorrectionMode } from "../domain/events";

export function correctionCapabilityFixture(mode: MeetingCorrectionMode = "local_and_llm") {
  return {
    schema_version: "meeting_correction_capability.v1", requested_mode: mode, effective_mode: mode,
    recommended_mode: mode, fallback_reason: null as string | null,
    local_refinement: { available: mode === "local_and_llm", worker_ready: false, reason: null as string | null },
    memory: { checked: true, commit_available_bytes: 12_000_000_000, physical_available_bytes: 4_000_000_000,
      total_physical_bytes: 32_000_000_000, required_commit_bytes: 8_589_934_592, required_physical_bytes: 1_073_741_824,
      profile: "offline_cold_start", sampled_at_ms: 1_791_000_000_000 },
    llm_correction: { enabled: true, configured: true },
  };
}

export function requestedCorrectionMode(url: string): MeetingCorrectionMode {
  return new URL(url, "http://localhost").searchParams.get("correction_mode") === "llm_only" ? "llm_only" : "local_and_llm";
}
