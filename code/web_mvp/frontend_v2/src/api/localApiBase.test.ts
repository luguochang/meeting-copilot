import { describe, expect, it } from "vitest";
import {
  isLoopbackHostname,
  resolveLocalApiBase,
  resolveLocalApiUrl,
  resolveLocalWebSocketUrl,
} from "./localApiBase";

describe("local API base contract", () => {
  it("treats an empty base as same-origin", () => {
    expect(resolveLocalApiBase(undefined)).toBe("");
    expect(resolveLocalApiBase("   ")).toBe("");
    expect(resolveLocalApiUrl("/v2/health", "", "http://localhost:5174/workbench")).toBe(
      "http://localhost:5174/v2/health",
    );
  });

  it("rejects an empty base when the effective same-origin page is remote", () => {
    expect(() => resolveLocalApiBase("", "https://meeting.example.test/workbench")).toThrow(
      /本机|loopback|local/i,
    );
    expect(() => resolveLocalApiUrl(
      "/v2/health",
      "",
      "https://meeting.example.test/workbench",
    )).toThrow(/本机|loopback|local/i);
  });

  it.each([
    ["localhost", "http://localhost:8767"],
    ["127.0.0.1", "http://127.0.0.1:8767"],
    ["[::1]", "https://[::1]:8767/runtime"],
  ])("accepts the %s loopback base and removes trailing slashes", (_host, base) => {
    expect(resolveLocalApiBase(`${base}///`)).toBe(base);
  });

  it("keeps the configured loopback path when resolving business URLs", () => {
    expect(resolveLocalApiUrl("/v2/health", "http://127.0.0.1:8767/runtime/")).toBe(
      "http://127.0.0.1:8767/runtime/v2/health",
    );
  });

  it.each([
    "https://api.example.test",
    "http://192.168.1.7:8767",
    "//127.0.0.1:8767",
    "/runtime",
    "http://user:password@localhost:8767",
    "ftp://localhost:8767",
  ])("rejects a non-local or non-absolute base: %s", (base) => {
    expect(() => resolveLocalApiBase(base)).toThrow(/本机|loopback|local/i);
  });

  it("does not treat a lookalike hostname as loopback", () => {
    expect(isLoopbackHostname("evil.localhost.example")).toBe(false);
    expect(isLoopbackHostname("localhost")).toBe(true);
    expect(isLoopbackHostname("127.0.0.1")).toBe(true);
    expect(isLoopbackHostname("[::1]")).toBe(true);
  });

  it("allows ASR websocket URLs only when the effective target is loopback", () => {
    expect(resolveLocalWebSocketUrl(
      "/live/asr/stream/ws/meeting-1",
      "",
      "http://127.0.0.1:5174/",
    )).toBe("ws://127.0.0.1:5174/live/asr/stream/ws/meeting-1");
    expect(resolveLocalWebSocketUrl(
      "/live/asr/stream/ws/meeting-1",
      "https://localhost:8767/",
    )).toBe("wss://localhost:8767/live/asr/stream/ws/meeting-1");
    expect(() => resolveLocalWebSocketUrl(
      "/live/asr/stream/ws/meeting-1",
      "https://remote.example.test/",
    )).toThrow(/麦克风|本机|loopback|local/i);
  });
});
