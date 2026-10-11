// One evaluation owns one channel. No model-supplied session or request ids.
export class HostToolChannel {
  constructor(requestId, write, { maxCalls = 4 } = {}) {
    this.requestId = requestId;
    this.write = write;
    this.pending = new Map();
    this.sequence = 0;
    this.closed = false;
    this.maxCalls = [3, 5, 6].includes(maxCalls) ? maxCalls : 4;
  }

  search(params) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId,
          call_id: callId, tool: "search_prior_evidence", arguments: params });
      } catch (error) {
        this.pending.delete(callId);
        reject(error);
      }
    });
  }

  readSpan(params) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId,
          call_id: callId, tool: "read_transcript_span", arguments: params });
      } catch (error) {
        this.pending.delete(callId);
        reject(error);
      }
    });
  }

  searchDocuments(params) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId,
          call_id: callId, tool: "search_project_documents", arguments: params });
      } catch (error) {
        this.pending.delete(callId);
        reject(error);
      }
    });
  }

  readProjectState(params = {}) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId,
          call_id: callId, tool: "read_project_state", arguments: params });
      } catch (error) {
        this.pending.delete(callId);
        reject(error);
      }
    });
  }

  receive(message) {
    if (this.closed || message.request_id !== this.requestId) return false;
    const pending = this.pending.get(message.call_id);
    if (!pending) return false;
    this.pending.delete(message.call_id);
    if (message.ok === true && (Array.isArray(message.results) || pending.proposal && message.results && typeof message.results === "object")) pending.resolve(message.results);
    else pending.reject(new Error("host evidence query rejected"));
    return true;
  }

  proposeProjectChanges(params) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject, proposal: true });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId,
          call_id: callId, tool: "propose_project_changes", arguments: params });
      } catch (error) {
        this.pending.delete(callId);
        reject(error);
      }
    });
  }

  proposeMemoryUpdate(params) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject, proposal: true });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId,
          call_id: callId, tool: "propose_memory_update", arguments: params });
      } catch (error) {
        this.pending.delete(callId);
        reject(error);
      }
    });
  }

  submitPreparationBrief(params) {
    if (this.closed || this.sequence >= this.maxCalls) return Promise.reject(new Error("host tool unavailable"));
    const callId = `evidence-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      this.pending.set(callId, { resolve, reject, proposal: true });
      try {
        this.write({ event: "host_tool_request", request_id: this.requestId, call_id: callId, tool: "submit_preparation_brief", arguments: params });
      } catch (error) { this.pending.delete(callId); reject(error); }
    });
  }

  close() {
    this.closed = true;
    for (const pending of this.pending.values()) pending.reject(new Error("host evidence request cancelled"));
    this.pending.clear();
  }
}
