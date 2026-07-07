// Meeting Copilot workbench client — 3-zone layout, wired to the real backend API.
// Phase 3 frontend: loads a mock ASR session, renders transcript + suggestion
// candidates (gap cards), triggers real LLM execution for suggestion cards, and
// fetches approach-consideration cards.
const $ = (id) => document.getElementById(id);
let currentSession = null;
let currentEvents = [];

const MOCK_PAYLOAD = {
  provider: "local_mock_asr",
  streaming_events: [
    { event_type: "final", segment_id: "asr_seg_001", text: "先灰度 5%。", start_ms: 0, end_ms: 3200, received_at_ms: 3500, confidence: 0.91 },
    { event_type: "final", segment_id: "asr_seg_002", text: "谁负责回滚？", start_ms: 3400, end_ms: 6100, received_at_ms: 7000, confidence: 0.9 },
    { event_type: "final", segment_id: "asr_seg_003", text: "如果错误率超过 0.1% 就回滚。", start_ms: 6100, end_ms: 8200, received_at_ms: 8800, confidence: 0.9 },
    { event_type: "final", segment_id: "asr_seg_004", text: "张三下周三补充兼容性测试用例。", start_ms: 8200, end_ms: 10400, received_at_ms: 11200, confidence: 0.9 },
  ],
};

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(window._tt);
  window._tt = setTimeout(() => t.classList.remove("show"), 2200);
}

function fmtMs(ms) {
  const s = Math.floor(ms / 1000);
  return `14:${String(30 + Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail || r.statusText), { status: r.status, body });
  return body;
}

function renderTranscriptAndCandidates(events) {
  const stream = $("stream");
  stream.innerHTML = "";
  const counts = { DecisionCandidate: 0, ActionItem: 0, Risk: 0, OpenQuestion: 0 };
  let gapCount = 0;
  events.forEach((e) => {
    const p = e.payload || {};
    if (e.event_type === "transcript_final") {
      const div = document.createElement("div");
      div.className = "utterance";
      div.innerHTML = `<div class="ts">${fmtMs(p.start_ms || 0)}</div><div class="text"><span class="speaker">发言：</span>${escapeHtml(p.text || "")}</div>`;
      stream.appendChild(div);
    } else if (e.event_type === "suggestion_candidate_event") {
      gapCount++;
      const div = document.createElement("div");
      div.className = "suggestion";
      div.innerHTML = `<div class="sug-head gap">缺口 · ${escapeHtml(p.gap_rule_id || "")}</div>
        <div class="sug-body">${escapeHtml(p.trigger_reason || p.suggested_prompt || "")} <strong>(候选，未调用 LLM)</strong></div>
        <div class="sug-meta">confidence ${p.confidence ?? "—"} · evidence ${[...(p.evidence_span_ids || [])].join(",")}</div>`;
      stream.appendChild(div);
      const t = p.target_type;
      if (counts[t] !== undefined) counts[t]++;
    }
  });
  $("c-decision").textContent = counts.DecisionCandidate;
  $("c-action").textContent = counts.ActionItem;
  $("c-risk").textContent = counts.Risk;
  $("c-question").textContent = counts.OpenQuestion;
  $("c-gap").textContent = gapCount;
  $("s-cards").textContent = gapCount;
}

function renderRealCards(runs) {
  const stream = $("stream");
  let realCount = 0;
  runs.forEach((run) => {
    if (run.run_status !== "completed" || !run.card) return;
    realCount++;
    const card = run.card;
    const div = document.createElement("div");
    div.className = "suggestion";
    div.style.borderLeftColor = "var(--info)";
    div.innerHTML = `<div class="sug-head" style="color:var(--info)">建议卡片 · ${escapeHtml(card.gap_rule_id || "")} · LLM 已调用</div>
      <div class="sug-body">${escapeHtml(card.suggestion_text || "")}</div>
      <div class="evidence"><span class="quote">evidence: ${(card.evidence_span_ids || []).join(", ")}</span></div>
      <div class="sug-meta">confidence ${card.confidence} · model ${escapeHtml(card.llm_trace?.model || "?")} · tokens ${card.llm_trace?.usage?.total_tokens || 0}</div>`;
    stream.appendChild(div);
  });
  $("s-cards").textContent = realCount;
  $("s-llm").textContent = `${runs.length} 调用`;
}

function renderApproachCards(cards) {
  const stream = $("stream");
  cards.forEach((card) => {
    const div = document.createElement("div");
    div.className = "suggestion approach";
    div.innerHTML = `<div class="sug-head approach">方案考量 · ${escapeHtml((card.card_type || "").replace("approach.", ""))}</div>
      <div class="sug-body">${escapeHtml(card.suggestion_text || "")}</div>
      <div class="evidence"><span class="quote">"${escapeHtml(card.evidence_quote || "")}"</span></div>
      <div class="sug-meta">confidence ${card.confidence} · tokens ${card.llm_trace?.usage?.total_tokens || 0}</div>`;
    stream.appendChild(div);
  });
  $("c-approach").textContent = cards.length;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("btn-load").addEventListener("click", async () => {
  try {
    const sid = "workbench_" + Date.now().toString(36);
    const created = await api("/live/asr/mock/sessions", { method: "POST", body: JSON.stringify({ ...MOCK_PAYLOAD, session_id: sid }) });
    currentSession = sid;
    currentEvents = created.live_events || [];
    const ev = await api(`/live/asr/sessions/${sid}/events`);
    currentEvents = ev.events || currentEvents;
    renderTranscriptAndCandidates(currentEvents);
    $("session-meta").textContent = `会话 ${sid} · ${currentEvents.length} 事件`;
    $("s-asr").textContent = "local_mock_asr";
    $("sys-status").innerHTML = `<div class="empty">会话已加载。点击「生成建议卡片」触发 LLM。</div>`;
    toast("会议已加载");
  } catch (err) {
    toast("加载失败: " + err.message);
  }
});

$("btn-cards").addEventListener("click", async () => {
  if (!currentSession) return toast("先加载会议");
  try {
    const body = await api(`/live/asr/sessions/${currentSession}/llm-execution-runs`, { method: "POST", body: JSON.stringify({ mode: "enabled" }) });
    renderRealCards(body.runs || []);
    toast(`生成 ${body.run_count} 张建议卡片`);
  } catch (err) {
    if (err.status === 422) {
      $("s-llm").textContent = "未配置";
      toast("LLM 未配置（设 LLM_GATEWAY_* 后可用）");
    } else {
      toast("生成失败: " + err.message);
    }
  }
});

$("btn-approach").addEventListener("click", async () => {
  if (!currentSession) return toast("先加载会议");
  try {
    const body = await api(`/live/asr/sessions/${currentSession}/approach-cards`, { method: "POST", body: JSON.stringify({ mode: "enabled" }) });
    renderApproachCards(body.approach_cards || []);
    toast(`生成 ${body.count} 张方案考量卡`);
  } catch (err) {
    if (err.status === 422) toast("LLM 未配置（方案考量需要 LLM）");
    else toast("生成失败: " + err.message);
  }
});
