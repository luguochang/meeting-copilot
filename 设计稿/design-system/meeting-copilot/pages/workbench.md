# Workbench Page Overrides

> **PROJECT:** Meeting Copilot
> **Generated:** 2026-07-06 22:13:19
> **Page Type:** Dashboard / Data View

> ⚠️ **IMPORTANT:** Rules in this file **override** the Master file (`design-system/MASTER.md`).
> Only deviations from the Master are documented here. For all other rules, refer to the Master.

---

## Page-Specific Rules

### Layout Overrides

- **Max Width:** 1400px or full-width
- **Grid:** 12-column grid for data flexibility
- **Sections:** 1. Hero (product + live preview or status), 2. Key metrics/indicators, 3. How it works, 4. CTA (Start trial / Contact)

### Spacing Overrides

- **Content Density:** High — optimize for information display

### Typography Overrides

- No overrides — use Master typography

### Color Overrides

- **Strategy:** Dark or neutral. Status colors (green/amber/red). Data-dense but scannable.

### Component Overrides

- Avoid: Remove focus outline without replacement
- Avoid: Animate everything that moves

---

## Page-Specific Components

### Meeting Cockpit / 会议驾驶舱

- Workbench 左侧列是 `Meeting Cockpit`，不是装饰计数栏，也不是重复的 transcript list。
- 可见分区使用更直接的产品语言：`本场会议` 是同一 session 的主链路状态投影，`重点筛选` 是实时提醒的 triage 控制区。
- `本场会议` 必须显示文字记录、实时提醒、AI 建议、方案分析、录音保存、会后复盘是否真的发生。
- `重点筛选` 按决定、待办、风险、待确认问题四类筛选实时提醒；有内容的类型必须可点击、可键盘访问，并通过明确选中态说明当前筛选。
- 0 条重点筛选不应伪装成可用操作。默认禁用，等对应类型出现提醒后再启用。
- 录音中，`文字记录` 和 `实时提醒` 必须随 WebSocket 实时事件增长；不能只在停止会议后通过 session snapshot 才补齐。
- 实时文字区必须区分稳定上下文和当前 ASR 尾巴：稳定 partial 追加为 `已记录`，正在变化的尾巴显示为 `正在听`。
- 会后，cockpit 必须能一眼证明复盘闭环：AI 建议、方案分析、录音保存、纪要生成都来自同一 session。
- 空态允许显示 `0 / 未保存 / 未生成`，但必须配合阶段 badge 和 `本场会议` 命名，例如 `待开始`、`录音中`、`整理中`、`已记录`、`已复盘`，避免被理解成无意义侧栏。
- 不在 cockpit 中展示 raw event id、debug trace、长段纪要正文或 mock/demo-only 数据。

---

## Recommendations

- Effects: Real-time chart animations, alert pulse/glow, status indicator blink animation, smooth data stream updates, loading effect
- Interaction: Use visible focus rings on interactive elements
- Animation: Animate 1-2 key elements per view maximum
- CTA Placement: Primary CTA in nav + After metrics
