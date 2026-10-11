import { parseMeetingAudio, parseMeetingSnapshot } from "./schema";

const partialLabel = "部分录音未成功，已保存部分可回放";

it("preserves partial audio failure and its safe recording indicator in a meeting snapshot", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "synthetic-audio", last_seq: 0, segments: [], suggestions: [],
    audio: { status: "partial_failure", chunk_count: 4, duration_ms: 1_000, file_size_bytes: 32_000, tracks: ["microphone"] },
    runtime: { phase: "ended", recording: { state: "error", label: partialLabel } },
  });
  expect(snapshot.audio).toMatchObject({ status: "partial_failure", chunkCount: 4 });
  expect(snapshot.runtime.recording).toMatchObject({ state: "error", label: partialLabel });
});

it("preserves a partial recording's playable asset separately from its failed attempts", () => {
  const audio = parseMeetingAudio({
    meeting_id: "synthetic-audio", status: "partial_failure", assembled: true,
    playback_url: "/synthetic.wav", chunks: [], expected_tracks: ["microphone"],
    track_states: [
      { track_id: "microphone", epoch: 1, status: "ready", playback_url: "/synthetic-part.wav" },
      { track_id: "microphone", epoch: 2, status: "interrupted", chunk_count: 0, error_class: "recording_setup_failed" },
    ],
  });
  expect(audio).toMatchObject({ status: "partial_failure", overallStatus: "partial_failure", assembled: true, playbackUrl: "/synthetic.wav" });
  expect(audio.trackStates[0]).toMatchObject({ status: "ready", playbackUrl: "/synthetic-part.wav" });
  expect(audio.trackStates[1]).toMatchObject({ status: "interrupted", playbackUrl: null });
});

it("keeps a zero-chunk failure distinct from a real assembly or an active recording", () => {
  for (const status of ["failed", "assembling", "recording"] as const) {
    const snapshot = parseMeetingSnapshot({
      meeting_id: "synthetic-audio", last_seq: 0, segments: [], suggestions: [],
      audio: { status, chunk_count: 0 },
    });
    expect(snapshot.audio.status).toBe(status);
  }
});
