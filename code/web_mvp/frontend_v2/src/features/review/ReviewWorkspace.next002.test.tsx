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

describe("ReviewWorkspace NEXT-002 recording tracks", () => {
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
