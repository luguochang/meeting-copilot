import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInitialMeetingState } from "../../domain/reducer";
import type { MeetingViewState } from "../../domain/events";
import type { MeetingAudioWithTracks } from "../../api/schema";
import { ReviewWorkspace } from "./ReviewWorkspace";

function endedState(audio: MeetingAudioWithTracks): MeetingViewState {
  const state = createInitialMeetingState(audio.meetingId);
  return {
    ...state,
    runtime: { ...state.runtime, phase: "ended" },
    audioDetail: audio,
    audioLoadState: "ready",
  };
}

function audio(overrides: Partial<MeetingAudioWithTracks> = {}): MeetingAudioWithTracks {
  return {
    meetingId: "meeting-next-002",
    status: "saved",
    overallStatus: "saved",
    assembled: false,
    playbackUrl: null,
    format: "wav",
    fileSizeBytes: 0,
    chunkCount: 10,
    durationMs: 120_000,
    tracks: ["microphone", "system_audio"],
    chunks: [],
    expectedTracks: ["microphone", "system_audio"],
    trackStates: [
      {
        trackId: "microphone",
        source: "microphone",
        epoch: 0,
        status: "ready",
        durationMs: 120_000,
        chunkCount: 10,
        fileSizeBytes: 320_000,
        playbackUrl: "/mic.wav",
        errorClass: null,
        firstSequence: 0,
        lastSequence: 9,
        firstTimestampMs: 0,
        lastTimestampMs: 119_000,
      },
      {
        trackId: "system_audio",
        source: "system_audio",
        epoch: 0,
        status: "ready",
        durationMs: 118_000,
        chunkCount: 10,
        fileSizeBytes: 315_000,
        playbackUrl: "/system.wav",
        errorClass: null,
        firstSequence: 0,
        lastSequence: 9,
        firstTimestampMs: 0,
        lastTimestampMs: 117_000,
      },
    ],
    derivedAssets: [],
    mixedCreateUrl: "/mixed",
    ...overrides,
  };
}

function props(state: MeetingViewState, overrides: Partial<React.ComponentProps<typeof ReviewWorkspace>> = {}) {
  return {
    state,
    onReloadTranscript: vi.fn(),
    onReloadAudio: vi.fn(),
    onExport: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("ReviewWorkspace recording tracks", () => {
  it("shows zero-chunk setup failures as failures instead of pending assembly or saved audio", async () => {
    const failed = audio({
      status: "failed", overallStatus: "failed", assembled: false, playbackUrl: null,
      chunkCount: 0, durationMs: 0, fileSizeBytes: 0, expectedTracks: ["microphone"],
      trackStates: [9, 10, 11].map((epoch) => ({
        ...audio().trackStates[0], epoch, status: "interrupted", chunkCount: 0, durationMs: 0,
        fileSizeBytes: 0, playbackUrl: null, errorClass: "recording_setup_failed",
      })),
    });
    const state = endedState(failed); state.audio = { ...state.audio, status: "failed", chunkCount: 0 };
    render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByLabelText("录音整理状态")).toHaveTextContent("录音整理失败");
    expect(screen.getByLabelText("录音整理状态")).toHaveClass("review-progress-item--error");
    expect(screen.getByLabelText("录音整理状态")).not.toHaveTextContent("录音整理中");
    expect(screen.getByLabelText("录音整理状态")).not.toHaveTextContent("录音已保存");
    await userEvent.click(screen.getByRole("tab", { name: "录音" }));
    expect(screen.getByText("本次会议没有可用录音")).toBeVisible();
    expect(screen.queryByRole("button", { name: /^播放/ })).not.toBeInTheDocument();
  });

  it("warns about failed setup attempts while preserving the saved continuous replay", async () => {
    const partial = audio({
      status: "partial_failure", overallStatus: "partial_failure", assembled: true,
      playbackUrl: "/saved-continuous.wav", expectedTracks: ["microphone"],
      trackStates: [audio().trackStates[0], ...[9, 10, 11].map((epoch) => ({
        ...audio().trackStates[0], epoch, status: "interrupted", chunkCount: 0, durationMs: 0,
        fileSizeBytes: 0, playbackUrl: null, errorClass: "recording_setup_failed",
      }))],
    });
    const state = endedState(partial); state.audio = { ...state.audio, status: "partial_failure" };
    const view = render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByLabelText("录音整理状态")).toHaveTextContent("部分录音未成功，已保存部分可回放");
    expect(screen.getByLabelText("录音整理状态")).toHaveClass("review-progress-item--warning");
    await userEvent.click(screen.getByRole("tab", { name: "录音" }));
    expect(screen.getByText("本次录音不完整")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "播放会议全程" }));
    expect(view.container.querySelector("audio")).toHaveAttribute("src", "/saved-continuous.wav");
  });

  it("prioritizes a real active capture over saved or failed earlier attempts", () => {
    const state = endedState(audio({ status: "partial_failure", overallStatus: "partial_failure", assembled: true }));
    state.audio = { ...state.audio, status: "recording" };
    state.runtime = { ...state.runtime, phase: "live", recording: { ...state.runtime.recording, state: "active", label: "正在录音" } };
    render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByLabelText("录音整理状态")).toHaveTextContent("正在录音");
    expect(screen.getByLabelText("录音整理状态")).not.toHaveTextContent("已保存");
    expect(screen.getByLabelText("录音整理状态")).not.toHaveTextContent("未成功");
  });

  it.each([
    ["assembling", false, "录音整理中"],
    ["saved", true, "录音已保存"],
    ["unknown", false, "录音状态待读取"],
  ] as const)("uses the observed %s state for recording progress", (status, assembled, label) => {
    const state = endedState(audio({ status, overallStatus: status, assembled }));
    state.audio = { ...state.audio, status };
    render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByLabelText("录音整理状态")).toHaveTextContent(label);
  });

  it("opens saved audio from a failed text recovery notice without claiming the missing text was recovered", async () => {
    const user = userEvent.setup(); const state = endedState(audio());
    state.diagnostics = { transcript_backfill: { status: "failed", start_ms: 60_000, end_ms: 90_000, error_code: "asr_capacity_unavailable" } };
    render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByRole("alert", { name: "会议文字补齐状态" })).toHaveTextContent("1:00–1:30");
    expect(screen.getByText(/已有可播放的录音/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "查看录音与保存状态" }));
    expect(screen.getByRole("tab", { name: "录音" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "播放会议声音" })).toBeEnabled();
    expect(screen.queryByText("这段中断处文字已补齐")).not.toBeInTheDocument();
  });
  it("shows both user-facing tracks, durations, and lets the user choose a ready track", async () => {
    const user = userEvent.setup();
    const state = endedState(audio());
    const view = render(<ReviewWorkspace {...props(state)} />);

    await user.click(screen.getByRole("tab", { name: "录音" }));

    expect(screen.getAllByText("我的麦克风")).not.toHaveLength(0);
    expect(screen.getByText("会议声音")).toBeVisible();
    expect(screen.getAllByText("2:00")).not.toHaveLength(0);
    expect(screen.getByText("1:58")).toBeVisible();
    expect(screen.getByRole("button", { name: "播放会议声音" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "播放会议声音" }));

    expect(view.container.querySelector("audio")).toHaveAttribute("src", "/system.wav");
  });

  it("only offers mixed replay after both tracks are ready and keeps it explicit", async () => {
    const user = userEvent.setup();
    const onCreateMixedAudio = vi.fn().mockResolvedValue({
      assetId: "mixed-1",
      meetingId: "meeting-next-002",
      kind: "mixed",
      derivation: "local_pcm16_timeline_mix",
      status: "ready",
      durationMs: 121_000,
      playbackUrl: "/mixed.wav",
      sources: [],
      remoteUploadUsed: false,
    });
    const state = endedState(audio());
    const view = render(<ReviewWorkspace {...props(state, { onCreateMixedAudio })} />);

    await user.click(screen.getByRole("tab", { name: "录音" }));
    expect(screen.getByRole("button", { name: "生成混合回放" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "生成混合回放" }));
    expect(onCreateMixedAudio).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "播放混合回放" })).toBeVisible();
    expect(view.container.querySelector("audio")).toHaveAttribute("src", "/mic.wav");

    await user.click(screen.getByRole("button", { name: "播放混合回放" }));
    expect(view.container.querySelector("audio")).toHaveAttribute("src", "/mixed.wav");
  });

  it("explains partial failure without presenting it as a complete meeting replay", async () => {
    const user = userEvent.setup();
    const partial = audio({
      overallStatus: "partial_failure",
      status: "failed",
      trackStates: [
        {
          trackId: "microphone",
          source: "microphone",
          epoch: 0,
          status: "ready",
          durationMs: 120_000,
          chunkCount: 10,
          fileSizeBytes: 320_000,
          playbackUrl: "/mic.wav",
          errorClass: null,
          firstSequence: 0,
          lastSequence: 9,
          firstTimestampMs: 0,
          lastTimestampMs: 119_000,
        },
        {
          trackId: "system_audio",
          source: "system_audio",
          epoch: 0,
          status: "failed",
          durationMs: 0,
          chunkCount: 0,
          fileSizeBytes: 0,
          playbackUrl: null,
          errorClass: "screen_capture_permission_denied",
          firstSequence: null,
          lastSequence: null,
          firstTimestampMs: null,
          lastTimestampMs: null,
        },
      ],
    });
    render(<ReviewWorkspace {...props(endedState(partial))} />);

    await user.click(screen.getByRole("tab", { name: "录音" }));

    expect(screen.getByText("本次录音不完整")).toBeVisible();
    expect(screen.getByText("未获得会议声音权限")).toBeVisible();
    expect(screen.queryByRole("button", { name: "生成混合回放" })).not.toBeInTheDocument();
    expect(screen.getByText("可播放已保存的轨道，但这不是完整会议回放。")).toBeVisible();
  });
});
