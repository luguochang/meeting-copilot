# PC Web MVP 产品主线自测报告 - 2026-07-07

> 范围：本轮针对用户反馈“前端按钮/功能混乱、主线功能没跑通、页面理解成本高”进行产品验收、P0 修复和自测。
> 结论：PC Web MVP 主流程已从“工程验证台”推进到“可点击的会议助手主流程”。这不等于生产发布，也不等于真实中文技术会议价值已最终验证。

## 1. 本轮完成内容

### 文档

- 新增 `docs/workbench-product-mainline-audit-and-fix-plan-2026-07-07.md`
  - 记录多 Agent 审查结论。
  - 记录前端按钮清单、产品需求差距、UI 简化方向、后端 API 缺口。
  - 固定本轮 P0 checklist 和暂不做边界。

### 后端

- `GET /live/asr/sessions`
  - 新增 live ASR 会话列表能力。
  - 返回 session_id、provider、event_count、final_count、suggestion_candidate_count、suggestion_card_count、approach_card_count、has_minutes、duration_ms。

- live ASR session record 最小持久化增强：
  - LLM 建议卡片写入 `suggestion_cards`。
  - 方案分析写入 `approach_cards`。
  - 会后复盘写入 `minutes`。

- `GET /live/asr/sessions/{session_id}/minutes.md`
  - 返回已生成的 Markdown 会后复盘。

- `GET /live/asr/sessions/{session_id}/events`
  - 在原有 events 外返回 `suggestion_cards`、`approach_cards`、`minutes`，方便历史会话恢复。

### 前端

- 首屏主流程文案改成用户语言：
  - `会议助手`
  - `开始会议`
  - `导入录音`
  - `实时文字`
  - `实时建议`
  - `会后复盘`
  - `历史记录`

- 顶部按钮改成主流程入口：
  - 开始会议/结束会议
  - 导入录音
  - 生成会后复盘
  - 生成会议建议
  - 分析方案利弊
  - 试用示例
  - 历史记录
  - 刷新实时文字
  - 删除本次会议

- 删除前端调试感强的核心文案：
  - 不再把 `真实录音`、`实时订阅`、`缺口卡`、`方案考量卡` 作为首屏核心语言。
  - `ASR/LLM` 在底部改成 `语音识别/AI 分析`。

- 新增/增强前端行为：
  - `setMeetingPhase()`：统一会议状态，录音中按钮变为“结束会议”，整理中显示“正在整理...”。
  - `resetSessionView()`：删除会话后清空事件、计数、状态、复盘和录音资源。
  - `loadSessionHistory()`：读取 live ASR session 列表并展示历史记录。
  - `openHistorySession()`：点击历史记录恢复文字、建议、方案分析和复盘。
  - `renderMinutes()`：展示会后复盘 Markdown。
  - `appendLiveEvent()` 同时支持 WebSocket 原始 `partial/final` 和 SSE `transcript_partial/transcript_final`。

## 2. 按钮自测结果

| 按钮 | 当前结果 | 验证方式 |
|---|---|---|
| 开始会议 | 页面状态和代码已改为开始/结束会议双态；真实麦克风全链路沿用上一轮已修复路径 | 自动化断言 + 上轮真实麦克风报告 |
| 导入录音 | 仍接 `/live/asr/transcribe-file/sessions`，文案改为“正在识别录音” | 自动化覆盖静态入口；本轮未上传真实文件 |
| 生成会后复盘 | 接 `/minutes`，结果写入 record，前端 `minutes-panel` 展示 | headless Chrome smoke 使用假 LLM 网关点击通过 |
| 生成会议建议 | 接 `/llm-execution-runs`，建议卡写回 record，前端去重展示 | headless Chrome smoke 使用假 LLM 网关点击通过 |
| 分析方案利弊 | 接 `/approach-cards`，方案卡写回 record，前端去重展示 | headless Chrome smoke 使用假 LLM 网关点击通过 |
| 试用示例 | 创建 mock ASR session，显示 4 条文字和建议 | in-app browser 实际点击通过 |
| 历史记录 | 读取 `GET /live/asr/sessions` 并显示 session | headless Chrome smoke + in-app browser 点击通过 |
| 刷新实时文字 | 仍使用 SSE 回放已落库事件，文案降级为刷新实时文字 | 自动化覆盖事件兼容 |
| 删除本次会议 | 删除后清空文字、计数、状态、复盘和会话信息 | headless Chrome smoke 点击并确认通过 |

## 3. 已运行验证

### 自动化测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_minutes.py \
  code/web_mvp/backend/tests/test_approach_cards.py \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_sessions_list_endpoint_returns_history_index \
  code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_execution_runs_enabled_persists_cards_for_history \
  code/web_mvp/backend/tests/test_real_asr_to_cards.py \
  code/web_mvp/backend/tests/test_e2e_mainline.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_mic_capture.py \
  -q -p no:cacheprovider
```

结果：

```text
32 passed, 2 warnings
```

### 浏览器主流程自测

```bash
node code/web_mvp/e2e/workbench_smoke.mjs
```

结果：

```text
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

说明：该 smoke 使用本地假 OpenAI-compatible LLM 网关，避免消耗真实中转站费用，同时验证页面点击链路。

### in-app browser 检查

- 打开 `http://127.0.0.1:8765/workbench`
- 页面标题：`会议助手`
- 首屏包含：开始会议、导入录音、实时文字、实时建议、会后复盘、历史记录。
- 点击 `试用示例` 后：
  - 4 条文字。
  - 4 条建议候选。
  - 历史记录显示 `workbench_*` 会话。

本地工作台服务已在 `http://127.0.0.1:8765/workbench` 运行。

## 4. 当前仍未完成的主线缺口

这些不是本轮遗漏，而是仍需后续主线继续推进：

1. 真实中文技术会议价值验证还没成立。
   - 目前真实麦克风链路已初步打通，但真实样本不是技术会议。
   - 还需要一次授权中文技术会议 shadow run，验证 10-30 秒内是否能出现有用工程提醒。

2. 系统音频采集仍未实现。
   - 仅麦克风可用，对线上会议不够可靠。
   - Mac/Windows 系统音频需要单独设计，不应混入本轮 UI 主流程修复。

3. 原始录音音频保存仍未实现。
   - 本轮只保存 session/events/cards/minutes。
   - PRD 要求的原始音频/WAV/PCM 保存和删除级联仍需后续实现。

4. 实时建议仍是“候选自动 + 正式建议手动生成”。
   - 当前规则候选会自动出现。
   - LLM 正式建议仍需点击“生成会议建议”。
   - 后续要做自动触发，需要先做频率、成本、时效和误报控制。

5. SSE 仍是已落库事件回放。
   - 录音中实时字幕主要靠 WebSocket。
   - 录音中跨页面恢复/多窗口同步不是本轮范围。

6. UI 仍是第一轮简化，不是最终视觉设计。
   - 当前只完成理解成本降低和主流程入口。
   - 后续可以继续按 `ui-ux-pro-max` 做更完整视觉重构。

## 5. 结论

本轮不再是评测循环，而是完成了一次产品主线收敛：

```text
打开页面
  -> 试用示例 / 开始会议 / 导入录音
  -> 实时文字
  -> 实时建议
  -> 方案分析
  -> 会后复盘
  -> 历史记录
  -> 删除本次会议
```

现在 PC Web MVP 的主流程已经可以点击验收。下一轮最有价值的动作不是再写新 wrapper，而是：

```text
真实中文技术会议 shadow run
  -> 观察 ASR 准确率、建议时效、建议有用率、误报和会后复盘质量
```

只有这条真实会议链路证明“实时建议有价值”，产品才算真正回到最初的初心。
