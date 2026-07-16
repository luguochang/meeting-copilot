const DEFAULT_TARGET_SAMPLE_RATE = 16_000;
const DEFAULT_FRAME_SAMPLES = 4_800;

function concatFloat32(left: Float32Array, right: Float32Array): Float32Array {
  if (!left.length) return new Float32Array(right);
  if (!right.length) return new Float32Array(left);
  const joined = new Float32Array(left.length + right.length);
  joined.set(left);
  joined.set(right, left.length);
  return joined;
}

export class StreamingPcmFramer {
  private readonly ratio: number;
  private readonly frameSamples: number;
  private sourceCarry = new Float32Array(0);
  private sourcePosition = 0;
  private frame: Float32Array;
  private frameLength = 0;

  constructor(
    inputSampleRate: number,
    targetSampleRate = DEFAULT_TARGET_SAMPLE_RATE,
    frameSamples = DEFAULT_FRAME_SAMPLES,
  ) {
    if (!Number.isFinite(inputSampleRate) || inputSampleRate <= 0) {
      throw new Error("麦克风采样率无效");
    }
    if (!Number.isFinite(targetSampleRate) || targetSampleRate <= 0) {
      throw new Error("目标采样率无效");
    }
    if (!Number.isInteger(frameSamples) || frameSamples <= 0) {
      throw new Error("PCM 分帧大小无效");
    }
    this.ratio = inputSampleRate / targetSampleRate;
    this.frameSamples = frameSamples;
    this.frame = new Float32Array(frameSamples);
  }

  push(input: Float32Array): Float32Array[] {
    if (!input.length) return [];
    return this.resample(concatFloat32(this.sourceCarry, input), false);
  }

  flush(): Float32Array[] {
    const frames: Float32Array[] = [];
    if (this.sourceCarry.length) {
      const padded = new Float32Array(this.sourceCarry.length + 1);
      padded.set(this.sourceCarry);
      padded[padded.length - 1] = this.sourceCarry[this.sourceCarry.length - 1];
      frames.push(...this.resample(padded, true));
    }
    if (this.frameLength) {
      frames.push(this.frame.slice(0, this.frameLength));
      this.frame = new Float32Array(this.frameSamples);
      this.frameLength = 0;
    }
    this.sourceCarry = new Float32Array(0);
    this.sourcePosition = 0;
    return frames;
  }

  private resample(source: Float32Array, flushing: boolean): Float32Array[] {
    const frames: Float32Array[] = [];
    while (this.sourcePosition + 1 < source.length) {
      const left = Math.floor(this.sourcePosition);
      const fraction = this.sourcePosition - left;
      const sample = source[left] + (source[left + 1] - source[left]) * fraction;
      this.frame[this.frameLength] = Math.max(-1, Math.min(1, sample));
      this.frameLength += 1;
      if (this.frameLength === this.frameSamples) {
        frames.push(this.frame);
        this.frame = new Float32Array(this.frameSamples);
        this.frameLength = 0;
      }
      this.sourcePosition += this.ratio;
    }

    if (flushing) {
      this.sourceCarry = new Float32Array(0);
      this.sourcePosition = 0;
      return frames;
    }

    const consumed = Math.floor(this.sourcePosition);
    this.sourceCarry = source.slice(Math.min(consumed, Math.max(0, source.length - 1)));
    this.sourcePosition -= consumed;
    return frames;
  }
}

export function pcmLevel(samples: Float32Array): number {
  if (!samples.length) return 0;
  let sumSquares = 0;
  for (const sample of samples) sumSquares += sample * sample;
  const rms = Math.sqrt(sumSquares / samples.length);
  return Math.max(0, Math.min(1, rms * 6));
}
