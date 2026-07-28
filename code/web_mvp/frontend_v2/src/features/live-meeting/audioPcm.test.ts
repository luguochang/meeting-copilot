import { StreamingPcmFramer } from "./audioPcm";

describe("StreamingPcmFramer", () => {
  it("resamples 48 kHz microphone input to bounded 100 ms 16 kHz frames", () => {
    const framer = new StreamingPcmFramer(48_000);
    const source = new Float32Array(48_000).fill(0.25);
    const frames = [
      ...framer.push(source.subarray(0, 12_000)),
      ...framer.push(source.subarray(12_000, 36_000)),
      ...framer.push(source.subarray(36_000)),
      ...framer.flush(),
    ];

    expect(frames.slice(0, -1).every((frame) => frame.length === 1_600)).toBe(true);
    expect(frames.reduce((total, frame) => total + frame.length, 0)).toBeGreaterThanOrEqual(15_999);
    expect(frames.reduce((total, frame) => total + frame.length, 0)).toBeLessThanOrEqual(16_001);
    expect(frames[0][100]).toBeCloseTo(0.25, 5);
  });

  it("keeps one pending tail and emits it only when flushed", () => {
    const framer = new StreamingPcmFramer(16_000);

    expect(framer.push(new Float32Array(800).fill(0.1))).toHaveLength(0);
    const tail = framer.flush();
    expect(tail).toHaveLength(1);
    expect(tail[0].length).toBeGreaterThanOrEqual(799);
    expect(tail[0].length).toBeLessThanOrEqual(800);
  });

  it("preserves one continuous resampling timeline across audio-worklet callbacks", () => {
    const source = Float32Array.from(
      { length: 48_000 },
      (_, index) => 0.8 * Math.sin((2 * Math.PI * index) / 31),
    );
    const uninterrupted = new StreamingPcmFramer(48_000);
    const callbackFramer = new StreamingPcmFramer(48_000);
    const expected = [
      ...uninterrupted.push(source),
      ...uninterrupted.flush(),
    ].flatMap((frame) => [...frame]);
    const callbackFrames: Float32Array[] = [];
    for (let start = 0; start < source.length; start += 128) {
      callbackFrames.push(...callbackFramer.push(source.subarray(start, start + 128)));
    }
    callbackFrames.push(...callbackFramer.flush());
    const actual = callbackFrames.flatMap((frame) => [...frame]);

    expect(actual).toHaveLength(expected.length);
    expect(Math.max(...actual.map((sample, index) => Math.abs(sample - expected[index]))))
      .toBeLessThan(1e-5);
  });

  it("attenuates content above the target Nyquist frequency", () => {
    const source = Float32Array.from(
      { length: 48_000 },
      (_, index) => Math.sin((2 * Math.PI * 12_000 * index) / 48_000),
    );
    const framer = new StreamingPcmFramer(48_000);
    const output = [
      ...framer.push(source.subarray(0, 12_345)),
      ...framer.push(source.subarray(12_345)),
      ...framer.flush(),
    ].flatMap((frame) => [...frame]);
    const rms = Math.sqrt(output.reduce((sum, sample) => sum + sample * sample, 0) / output.length);

    expect(output).toHaveLength(16_000);
    expect(rms).toBeLessThan(0.02);
  });

  it("preserves speech-band content while downsampling", () => {
    const source = Float32Array.from(
      { length: 48_000 },
      (_, index) => Math.sin((2 * Math.PI * 1_000 * index) / 48_000),
    );
    const framer = new StreamingPcmFramer(48_000);
    const output = [...framer.push(source), ...framer.flush()].flatMap((frame) => [...frame]);
    const stable = output.slice(64, -64);
    const rms = Math.sqrt(stable.reduce((sum, sample) => sum + sample * sample, 0) / stable.length);

    expect(output).toHaveLength(16_000);
    expect(rms).toBeGreaterThan(0.68);
    expect(rms).toBeLessThan(0.73);
  });
});
