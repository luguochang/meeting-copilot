export const CANONICAL_TRANSCRIPT_SELECTOR =
  ".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])";

export function hasCanonicalTranscript({ canonicalCount = 0, activeTailVisible = false } = {}) {
  return Number(canonicalCount) > 0 || Boolean(activeTailVisible);
}

export function hasHistorySession(sessionIds = [], expectedSessionId = "") {
  const ids = (sessionIds || []).map((value) => String(value || "").trim()).filter(Boolean);
  const expected = String(expectedSessionId || "").trim();
  return expected ? ids.includes(expected) : ids.length > 0;
}

export function isMinutesReady({ minutesCountText = "", panelText = "" } = {}) {
  return String(minutesCountText).trim() === "已生成"
    && String(panelText).trim().length > 20
    && !String(panelText).includes("暂时没有生成可用复盘");
}

export function isOrganizeTerminal({ organizeButtonDisabled = true, statusText = "" } = {}) {
  const text = String(statusText || "");
  if (organizeButtonDisabled || !text) return false;
  if (text.includes("正在整理会议") || text.includes("正在生成会后复盘")) return false;
  return text.includes("会议整理完成")
    || text.includes("识别质量不足")
    || text.includes("AI 分析暂不可用")
    || text.includes("生成失败");
}

export function isMeetingStopped({ recordButtonHidden = true, stopButtonHidden = false, cockpitState = "" } = {}) {
  return recordButtonHidden === false
    && stopButtonHidden === true
    && !["recording", "processing"].includes(String(cockpitState));
}
