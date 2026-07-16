class MeetingCopilotAudioCapture extends AudioWorkletProcessor {
  process(inputs, outputs) {
    const channel = inputs[0]?.[0];
    if (channel?.length) {
      const copy = new Float32Array(channel);
      this.port.postMessage(copy, [copy.buffer]);
    }
    const output = outputs[0]?.[0];
    if (output) output.fill(0);
    return true;
  }
}

registerProcessor("meeting-copilot-audio-capture", MeetingCopilotAudioCapture);
