import { describe, expect, it } from "vitest";
import { captureHealthSummary, resolveMeetingSessionState } from "./meetingSessionState";

const indicator = (state: "active" | "busy" | "paused" | "idle" | "error" | "offline", label = state) => ({
  state,
  label,
  level: null,
  detail: null,
});

describe("meetingSessionState", () => {
  it("treats a backend-live meeting without a browser MediaStream as recoverable", () => {
    expect(resolveMeetingSessionState({
      meetingPhase: "live",
      capturePhase: "idle",
      captureBelongsToMeeting: true,
    })).toBe("recoverable");
  });

  it("prioritizes terminal and local capture states", () => {
    expect(resolveMeetingSessionState({
      meetingPhase: "ended",
      capturePhase: "recording",
      captureBelongsToMeeting: true,
    })).toBe("ended");
    expect(resolveMeetingSessionState({
      meetingPhase: "live",
      capturePhase: "paused",
      captureBelongsToMeeting: true,
    })).toBe("paused");
    expect(resolveMeetingSessionState({
      meetingPhase: "live",
      capturePhase: "recording",
      captureBelongsToMeeting: false,
    })).toBe("recoverable");
  });

  it("distinguishes a fresh backend lease from a recoverable or never-started meeting", () => {
    expect(resolveMeetingSessionState({
      meetingPhase: "live",
      capturePhase: "idle",
      captureBelongsToMeeting: true,
      backendCaptureState: "active",
    })).toBe("capturing_elsewhere");
    expect(resolveMeetingSessionState({
      meetingPhase: "live",
      capturePhase: "idle",
      captureBelongsToMeeting: true,
      backendCaptureState: "recoverable",
    })).toBe("recoverable");
    expect(resolveMeetingSessionState({
      meetingPhase: "live",
      capturePhase: "idle",
      captureBelongsToMeeting: true,
      backendCaptureState: "inactive",
    })).toBe("idle");
  });

  it("summarizes actionable capture health instead of exposing every subsystem", () => {
    expect(captureHealthSummary(
      "capturing",
      indicator("active"),
      indicator("active"),
      indicator("active"),
    )).toEqual({ state: "active", label: "采集正常", aiLabel: "AI 可用" });
    expect(captureHealthSummary(
      "recoverable",
      indicator("idle"),
      indicator("idle"),
      indicator("error"),
    )).toEqual({ state: "paused", label: "录音待恢复", aiLabel: "AI 不可用" });
    expect(captureHealthSummary(
      "capturing_elsewhere",
      indicator("active"),
      indicator("active"),
      indicator("active"),
    )).toEqual({ state: "active", label: "另一窗口录音中", aiLabel: "AI 可用" });
  });
});
