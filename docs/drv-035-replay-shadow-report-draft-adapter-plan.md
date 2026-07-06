# DRV-035 Replay Shadow Report Draft Adapter Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`replay timeline report -> DRV-033 shadow-test report draft`  
> 边界：本计划不授权访问麦克风、不读取真实用户音频、不读取 `.m4a`、不读取 `configs/local/`、不读取 `data/asr_eval/local_samples/`、不下载公开音频、不运行 ASR provider、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

## 1. 背景

PCWEB-110 已经把 approved synthetic/mock ASR events replay 成可审计 timeline report。PCWEB-111 又给 replay 增加了 ASR event provenance manifest，能区分 `synthetic_audio`、`mock_streaming` 和未来 `public_audio_sample`。DRV-034 则补齐了公开音频人工抽样后的 evidence schema。

下一步不能继续泛化评测，也不能直接开麦克风。必须把当前 replay timeline 映射成 DRV-033 的真实 shadow-test 报告草稿结构，让“模拟自测”和“未来用户真实麦克风会议验收”使用同一个报告口径。

## 2. 目标

新增 `tools/replay_shadow_report_draft_adapter.py`，把 `tools/asr_live_pipeline_replay.py` 的 report 映射成 `real_mic_shadow_test_report.v1` candidate report draft，并调用 `tools/real_mic_shadow_test_report_schema.py` 做 schema validation。

该 adapter 只创建草稿：

- transcript segments 来自 replay `evidence_span_timeline`。
- ASR metrics 来自 replay `asr_metrics` 和 event counts。
- EvidenceSpan/state/candidate-card timeline 来自 replay timeline。
- feedback summary 固定为 0，因为还没有真实用户反馈。
- final decision 固定为 `inconclusive_requires_more_shadow_tests`。
- audio retention 固定为 `not_written` / `not_applicable_no_audio_written`。
- privacy/cost flags 全 false。

## 3. 不做什么

- 不读取麦克风。
- 不枚举音频设备。
- 不请求音频权限。
- 不读取真实用户录音。
- 不读取 `.m4a`。
- 不写 audio chunk。
- 不删除真实 audio chunk。
- 不读取 `configs/local/`。
- 不读取 `data/asr_eval/local_samples/`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行外部命令、Cargo 或 Tauri。
- 不伪造真实用户 feedback。
- 不把公开音频或 replay 草稿当作产品价值 Go 证据。

## 4. 输入与输出

输入：

- 内存中的 replay report，或
- `artifacts/tmp/asr_reports/**/*.json` 下的 replay report path。

路径守卫：

- `replay_report_path` 必须在 `artifacts/tmp/asr_reports` 下。
- 读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`。
- 读取前阻断仓库外路径和非 JSON 文件。
- 输入 replay report 的 `validation_errors` 必须为空。
- 输入 replay report 的 `safe_to_call_llm_now`、`safe_to_call_remote_asr_now`、`safe_to_read_user_audio_now`、`safe_to_read_configs_local_now`、`safe_to_capture_microphone_now` 必须全部明确为 false；否则 blocked，避免把上游危险来源洗成干净 shadow draft。

输出：

- `adapter_id=DRV-035`
- `adapter_status=shadow_report_draft_created` 或明确 blocked 状态。
- `candidate_report_validation_status=passed/failed/not_run`
- `candidate_report`，仅当 DRV-033 schema validation passed 时返回。
- all false safety flags。

## 5. 映射规则

| replay 字段 | DRV-033 draft 字段 | 说明 |
| --- | --- | --- |
| `session_id` | `session_id=replay-draft-{session_id}` | 避免与真实 shadow-test session 混淆 |
| `evidence_span_timeline` | `transcript.segments` | 每个 evidence span 生成一个 transcript segment |
| `evidence_span_timeline` | `evidence_span_timeline` | 保留 evidence id、segment id、text、start/end |
| `candidate_card_timeline` | `candidate_card_timeline` | 生成 draft engineering gap card |
| `state_timeline` | `state_timeline` | 映射 state type，保留首个 evidence id |
| `asr_metrics` | `asr_metrics` | 保留 duration、first partial、first final、error/eos count |
| 无真实反馈 | `feedback_summary` | 全 0 |
| 无真实 pilot | `final_decision` | `inconclusive_requires_more_shadow_tests` |
| 无真实音频 | `audio_retention` | `not_written` |

EvidenceSpan 的 `supports_candidate_id` 必须交叉引用现有 candidate id。若 replay evidence 没有直接绑定 candidate，但 replay 中已有候选卡，则归到第一张草稿 candidate，避免生成无法通过 schema 的占位 id；这仍是草稿关联，不是正式用户反馈。

## 6. 网上音频与模拟分工结论

2026-07-03 官方来源复核结论保持不变：

- AliMeeting / OpenSLR SLR119：会议语音主候选，官方页面标注 CC BY-SA 4.0；Eval 包约 3.42G。只做 no-download sample manifest / future evidence schema，不默认下载。
- AISHELL-4 / OpenSLR SLR111：会议语音补充候选，官方页面标注 CC BY-SA 4.0；test 包约 5.2G。只做 no-download sample manifest / future evidence schema，不默认下载。
- AISHELL-1 / OpenSLR SLR33：普通话 sanity check，官方页面标注 Apache License v2.0；不是会议，不证明产品价值。
- FunASR：中文 ASR 质量主候选，但当前必须有本地模型目录或明确 DRV-019 审批；不自动下载模型。

执行分工：

```text
官方公开音频来源复核
  -> no-download planned sample manifest / post-extraction evidence schema
  -> 自建中文技术会议脚本 / 合成音频 / mock streaming events
  -> 本地 replay timeline
  -> DRV-035 shadow report draft
  -> 用户最终真实麦克风 shadow test
```

公开音频只验证会议声学和 ASR event contract；合成中文技术会议验证工程语义、EvidenceSpan、gap 和 candidate；真实麦克风会议由用户最终验证产品价值。

## 7. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_replay_shadow_report_draft_adapter.py -q -p no:cacheprovider
```

结果：

```text
4 failed, 1 warning
```

原因：

- `tools/replay_shadow_report_draft_adapter.py` 不存在。

实现后首次绿灯前失败：

```text
1 failed, 3 passed, 1 warning
```

原因：

- 第一条 EvidenceSpan 使用 `draft_candidate_pending` 作为 `supports_candidate_id`，无法交叉引用 `candidate_card_timeline`。

修复：

- 增加测试断言未直接绑定的 EvidenceSpan 也引用现有 draft candidate。
- Adapter fallback 到第一张 replay candidate id，不再输出无效占位 candidate id。

最终 focused 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_replay_shadow_report_draft_adapter.py -q -p no:cacheprovider
```

结果：

```text
6 passed, 1 warning
```

复审加固：

- 只读审查发现 adapter 原先只校验 `safe_to_call_llm_now` 和 `safe_to_capture_microphone_now`，可能接受 `safe_to_call_remote_asr_now=true`、`safe_to_read_user_audio_now=true` 或上游 `validation_errors` 非空的 replay，再输出 all-false privacy/cost draft。
- 追加红灯：
  - `test_replay_shadow_report_draft_blocks_replay_with_side_effect_flags`
  - `test_replay_shadow_report_draft_blocks_replay_with_validation_errors`
- 红灯结果：`2 failed, 1 warning`，证明旧实现会把危险 replay 转成 `shadow_report_draft_created`。
- 加固后同一 focused selection：`2 passed, 1 warning`。
- 加固后完整 DRV-035 focused gate：`6 passed, 1 warning`。

## 8. 复审结论

两个只读审查 Agent 的结论一致，复审提出的 replay side-effect flags 阻断问题已按 TDD 加固：

- 完整计划已经写下，且公开音频模拟、合成/Mock 转写、用户最终真实麦克风验证三条路线已经分开。
- 不需要继续泛搜音频。当前官方来源已经足够支撑输入层验证，继续泛搜会偏离产品主线并增加版权风险。
- DRV-034 后继续 DRV-035 是正确下一步，因为它把 replay 证据桥接到未来真实 shadow-test 报告结构。
- 风险不是计划缺失，而是 ASR 中文技术实体质量、桌面 runtime/worker/mic connector 和最终真实 pilot 仍未完成。

## 9. 下一步

DRV-035 完成后，下一步不应继续评测循环。可选主线只剩三类：

- `Real Tauri no-op run`：在明确边界下运行 Tauri WebView，验证 no-op IPC 可由 UI 调用，仍不访问麦克风。
- `worker/mic connector`：把 no-op worker/mic 合同推进到真实 connector 设计，但启动/采集仍需审批。
- `shadow report ingestion/export/feedback`：把 DRV-035 draft 和未来真实 DRV-033 report 接到导出、反馈和 Go/Pivot/Stop 报告链路。

真实麦克风会议仍由用户最终执行。没有用户显式 start，不进入真实采集。
