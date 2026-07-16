const DEFAULT_SETTINGS = {
  asr: {
    l2_correction_enabled: true,
    l3_normalize_enabled: true,
  },
  suggestions: {
    enabled: true,
    window_seconds: 20,
    cooldown_minutes: 5,
    confidence_threshold: 0.7,
  },
  budget: {
    session_limit_cny: 10,
    daily_limit_cny: 50,
    l3_value_policy: "when_needed",
  },
};

let currentSettings = JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
let costStats = {
  currentSession: null,
  today: null,
  month: null,
  breakdown: [],
};

function clientApiUrl(path) {
  return window.meetingCopilotClient?.apiUrl(path) || path;
}

function notify(message) {
  if (window.meetingCopilotClient?.notify) window.meetingCopilotClient.notify(message);
}

function normalizeSettings(value = {}) {
  return {
    asr: { ...DEFAULT_SETTINGS.asr, ...(value.asr || {}) },
    suggestions: { ...DEFAULT_SETTINGS.suggestions, ...(value.suggestions || {}) },
    budget: { ...DEFAULT_SETTINGS.budget, ...(value.budget || {}) },
  };
}

async function fetchJson(path, options = {}) {
  const response = await fetch(clientApiUrl(path), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || body.message || detail;
    } catch {}
    throw new Error(detail);
  }
  return response.json();
}

function setValue(id, value) {
  const element = document.getElementById(id);
  if (element) element.value = String(value);
}

function setChecked(id, value) {
  const element = document.getElementById(id);
  if (element) element.checked = Boolean(value);
}

function estimatedCostLabel(value) {
  return Number.isFinite(Number(value)) ? `约 ¥${Number(value).toFixed(4)}` : "费率未配置";
}

function applySettingsToUI() {
  setChecked("setting-asr-l2-correction", currentSettings.asr.l2_correction_enabled);
  setChecked("setting-asr-l3-normalize", currentSettings.asr.l3_normalize_enabled);
  setChecked("setting-suggestions-enabled", currentSettings.suggestions.enabled);
  setValue("setting-suggestions-window", currentSettings.suggestions.window_seconds);
  setValue("setting-suggestions-cooldown", currentSettings.suggestions.cooldown_minutes);
  setValue("setting-suggestions-confidence", currentSettings.suggestions.confidence_threshold);
  setValue("setting-budget-session", currentSettings.budget.session_limit_cny);
  setValue("setting-budget-daily", currentSettings.budget.daily_limit_cny);
  setValue("setting-l3-value-check", currentSettings.budget.l3_value_policy);

  const confidenceValue = document.getElementById("setting-suggestions-confidence-value");
  if (confidenceValue) confidenceValue.textContent = String(currentSettings.suggestions.confidence_threshold);
  const current = document.getElementById("cost-current-session");
  const today = document.getElementById("cost-today");
  const month = document.getElementById("cost-month");
  if (current) current.textContent = estimatedCostLabel(costStats.currentSession);
  if (today) today.textContent = estimatedCostLabel(costStats.today);
  if (month) month.textContent = estimatedCostLabel(costStats.month);

  const breakdown = document.getElementById("cost-breakdown-body");
  if (!breakdown) return;
  if (!Array.isArray(costStats.breakdown) || costStats.breakdown.length === 0) {
    breakdown.innerHTML = '<tr><td colspan="3">暂无调用记录</td></tr>';
    return;
  }
  breakdown.innerHTML = costStats.breakdown.map((item) => `
    <tr>
      <td>${String(item.name || item.purpose || "AI 分析")}</td>
      <td>${Number(item.tokens || item.total_tokens || 0)}</td>
      <td>${estimatedCostLabel(item.estimated_cost_cny ?? item.cost)}</td>
    </tr>
  `).join("");
}

async function loadSettings() {
  try {
    const [settings, costs] = await Promise.all([
      fetchJson("/settings"),
      fetchJson("/settings/cost-stats"),
    ]);
    currentSettings = normalizeSettings(settings);
    costStats = costs;
    applySettingsToUI();
  } catch (error) {
    notify(`设置读取失败：${error.message}`);
    throw error;
  }
}

function readSettingsFromUI() {
  return {
    asr: {
      l2_correction_enabled: document.getElementById("setting-asr-l2-correction").checked,
      l3_normalize_enabled: document.getElementById("setting-asr-l3-normalize").checked,
    },
    suggestions: {
      enabled: document.getElementById("setting-suggestions-enabled").checked,
      window_seconds: Number(document.getElementById("setting-suggestions-window").value),
      cooldown_minutes: Number(document.getElementById("setting-suggestions-cooldown").value),
      confidence_threshold: Number(document.getElementById("setting-suggestions-confidence").value),
    },
    budget: {
      session_limit_cny: Number(document.getElementById("setting-budget-session").value),
      daily_limit_cny: Number(document.getElementById("setting-budget-daily").value),
      l3_value_policy: document.getElementById("setting-l3-value-check").value,
    },
  };
}

async function saveSettings() {
  const candidate = readSettingsFromUI();
  try {
    const saved = await fetchJson("/settings", {
      method: "PATCH",
      body: JSON.stringify(candidate),
    });
    currentSettings = normalizeSettings(saved);
    applySettingsToUI();
    document.getElementById("settings-modal").hidden = true;
    notify("设置已保存");
  } catch (error) {
    notify(`设置保存失败：${error.message}`);
  }
}

function resetSettings() {
  currentSettings = JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
  applySettingsToUI();
  notify("已恢复默认值，保存后生效");
}

async function openSettingsModal() {
  const modal = document.getElementById("settings-modal");
  if (!modal) return;
  modal.hidden = false;
  await loadSettings();
}

function closeSettingsModal() {
  const modal = document.getElementById("settings-modal");
  if (modal) modal.hidden = true;
}

function initializeSettingsPanel() {
  const actions = document.getElementById("primary-actions");
  if (actions && !document.getElementById("btn-settings")) {
    const settingsBtn = document.createElement("button");
    settingsBtn.id = "btn-settings";
    settingsBtn.className = "btn";
    settingsBtn.textContent = "设置";
    settingsBtn.addEventListener("click", () => void openSettingsModal());
    actions.appendChild(settingsBtn);
  }
  document.getElementById("btn-save-settings")?.addEventListener("click", () => void saveSettings());
  document.getElementById("btn-reset-settings")?.addEventListener("click", resetSettings);
  document.getElementById("btn-close-settings")?.addEventListener("click", closeSettingsModal);
  document.getElementById("setting-suggestions-confidence")?.addEventListener("input", (event) => {
    const value = document.getElementById("setting-suggestions-confidence-value");
    if (value) value.textContent = event.target.value;
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeSettingsPanel, { once: true });
} else {
  initializeSettingsPanel();
}
