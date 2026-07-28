const DEFAULT_TARGET_SAMPLE_RATE = 16_000;
const DEFAULT_FRAME_SAMPLES = 1_600;
const FILTER_HALF_TAPS = 32;
const FILTER_PHASE_COUNT = 256;

function concatFloat32(left: Float32Array, right: Float32Array): Float32Array {
  if (!left.length) return new Float32Array(right);
  if (!right.length) return new Float32Array(left);
  const joined = new Float32Array(left.length + right.length);
  joined.set(left);
  joined.set(right, left.length);
  return joined;
}

function buildLowPassKernels(cutoff: number): Float64Array[] {
  return Array.from({ length: FILTER_PHASE_COUNT }, (_, phaseIndex) => {
    const fraction = phaseIndex / FILTER_PHASE_COUNT;
    const kernel = new Float64Array(FILTER_HALF_TAPS * 2);
    let weightSum = 0;
    for (let tap = 0; tap < kernel.length; tap += 1) {
      const sampleOffset = tap - FILTER_HALF_TAPS + 1;
      const distance = fraction - sampleOffset;
      const normalizedDistance = Math.abs(distance) / FILTER_HALF_TAPS;
      if (normalizedDistance >= 1) continue;
      const sincArgument = 2 * cutoff * distance;
      const sinc = Math.abs(sincArgument) < Number.EPSILON
        ? 1
        : Math.sin(Math.PI * sincArgument) / (Math.PI * sincArgument);
      const window = 0.42
        + 0.5 * Math.cos(Math.PI * normalizedDistance)
        + 0.08 * Math.cos(2 * Math.PI * normalizedDistance);
      const weight = 2 * cutoff * sinc * window;
      kernel[tap] = weight;
      weightSum += weight;
    }
    if (Math.abs(weightSum) > Number.EPSILON) {
      for (let tap = 0; tap < kernel.length; tap += 1) kernel[tap] /= weightSum;
    }
    return kernel;
  });
}

export class StreamingPcmFramer {
  private readonly ratio: number;
  private readonly frameSamples: number;
  private readonly bypassResampling: boolean;
  private readonly filterKernels: Float64Array[];
  private sourceBuffer: Float32Array = new Float32Array(0);
  private sourceStartIndex = 0;
  private inputSampleCount = 0;
  private nextOutputIndex = 0;
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
    this.bypassResampling = inputSampleRate === targetSampleRate;
    this.filterKernels = this.bypassResampling
      ? []
      : buildLowPassKernels(0.45 * Math.min(targetSampleRate / inputSampleRate, 1));
    this.frameSamples = frameSamples;
    this.frame = new Float32Array(frameSamples);
  }

  push(input: Float32Array): Float32Array[] {
    if (!input.length) return [];
    if (this.bypassResampling) return this.appendSamples(input);
    this.sourceBuffer = concatFloat32(this.sourceBuffer, input);
    this.inputSampleCount += input.length;
    return this.resample(false);
  }

  flush(): Float32Array[] {
    const frames = this.bypassResampling ? [] : this.resample(true);
    if (this.frameLength) {
      frames.push(this.frame.slice(0, this.frameLength));
      this.frame = new Float32Array(this.frameSamples);
      this.frameLength = 0;
    }
    this.sourceBuffer = new Float32Array(0);
    this.sourceStartIndex = 0;
    this.inputSampleCount = 0;
    this.nextOutputIndex = 0;
    return frames;
  }

  private appendSamples(samples: Float32Array): Float32Array[] {
    const frames: Float32Array[] = [];
    for (const sample of samples) {
      this.frame[this.frameLength] = Math.max(-1, Math.min(1, sample));
      this.frameLength += 1;
      if (this.frameLength === this.frameSamples) {
        frames.push(this.frame);
        this.frame = new Float32Array(this.frameSamples);
        this.frameLength = 0;
      }
    }
    return frames;
  }

  private resample(flushing: boolean): Float32Array[] {
    const output: number[] = [];
    const lastAvailableIndex = this.sourceStartIndex + this.sourceBuffer.length - 1;
    while (true) {
      const sourcePosition = this.nextOutputIndex * this.ratio;
      if (flushing) {
        if (sourcePosition >= this.inputSampleCount) break;
      } else if (Math.ceil(sourcePosition) + FILTER_HALF_TAPS > lastAvailableIndex) {
        break;
      }
      output.push(this.filteredSample(sourcePosition));
      this.nextOutputIndex += 1;
    }

    const nextSourcePosition = this.nextOutputIndex * this.ratio;
    const discardBefore = Math.floor(nextSourcePosition) - FILTER_HALF_TAPS - 1;
    const discardCount = Math.max(
      0,
      Math.min(this.sourceBuffer.length, discardBefore - this.sourceStartIndex),
    );
    if (discardCount > 0) {
      this.sourceBuffer = this.sourceBuffer.slice(discardCount);
      this.sourceStartIndex += discardCount;
    }
    return this.appendSamples(Float32Array.from(output));
  }

  private filteredSample(sourcePosition: number): number {
    const center = Math.floor(sourcePosition);
    const fraction = sourcePosition - center;
    const phaseIndex = Math.min(
      FILTER_PHASE_COUNT - 1,
      Math.floor(fraction * FILTER_PHASE_COUNT),
    );
    const kernel = this.filterKernels[phaseIndex];
    let sample = 0;
    for (let tap = 0; tap < kernel.length; tap += 1) {
      const sourceIndex = center + tap - FILTER_HALF_TAPS + 1;
      const bufferOffset = sourceIndex - this.sourceStartIndex;
      if (bufferOffset >= 0 && bufferOffset < this.sourceBuffer.length) {
        sample += this.sourceBuffer[bufferOffset] * kernel[tap];
      }
    }
    return Math.max(-1, Math.min(1, sample));
  }
}

export function pcmLevel(samples: Float32Array): number {
  if (!samples.length) return 0;
  let sumSquares = 0;
  for (const sample of samples) sumSquares += sample * sample;
  const rms = Math.sqrt(sumSquares / samples.length);
  return Math.max(0, Math.min(1, rms * 6));
}
