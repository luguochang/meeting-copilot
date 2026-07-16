import { StreamingPcmFramer } from "./audioPcm";

describe("StreamingPcmFramer", () => {
  it("resamples 48 kHz microphone input to bounded 300 ms 16 kHz frames", () => {
    const framer = new StreamingPcmFramer(48_000);
    const source = new Float32Array(48_000).fill(0.25);
    const frames = [
      ...framer.push(source.subarray(0, 12_000)),
      ...framer.push(source.subarray(12_000, 36_000)),
      ...framer.push(source.subarray(36_000)),
      ...framer.flush(),
    ];

    expect(frames.slice(0, -1).every((frame) => frame.length === 4_800)).toBe(true);
    expect(frames.reduce((total, frame) => total + frame.length, 0)).toBeGreaterThanOrEqual(15_999);
    expect(frames.reduce((total, frame) => total + frame.length, 0)).toBeLessThanOrEqual(16_001);
    expect(frames[0][100]).toBeCloseTo(0.25, 5);
  });

  it("keeps one pending tail and emits it only when flushed", () => {
    const framer = new StreamingPcmFramer(16_000);

    expect(framer.push(new Float32Array(1_600).fill(0.1))).toHaveLength(0);
    const tail = framer.flush();
    expect(tail).toHaveLength(1);
    expect(tail[0].length).toBeGreaterThanOrEqual(1_599);
    expect(tail[0].length).toBeLessThanOrEqual(1_600);
  });
});
