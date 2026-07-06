# 多 Agent 全链路设计评审报告

> 日期：2026-06-30  
> 范围：Meeting Copilot 全链路设计、PC Local Web MVP、架构边界、SDD/TDD 验收追踪。  
> 评审方式：3 个只读 Agent 分别从产品价值、技术架构、SDD/TDD 验收追踪角度审查；主线程汇总并给出修正优先级。  
> 安全边界：未读取 `configs/local/**`，未输出密钥。

## 1. 总体结论

当前产品方向正确，最初的产品设计文档已经能清楚表达核心目标：

```text
实时/准实时中文技术会议 Copilot
  != 音频转文字
  != 会后总结
```

已有强约束包括：

- 会议状态机。
- EvidenceSpan。
- 工程语境门禁。
- 低频实时建议卡片。
- 状态事件日志。
- 会后证据化报告。
- 默认本地 ASR，远程 ASR 非默认。
- LLM 调用节流和成本记录。

但评审一致认为：**当前验收门槛还不够硬，仍可能让实现退化成“有证据的总结器”。** 尤其 PC Local Web MVP 当前更多证明“能聚合快照和导出报告”，还没有证明“卡片来自状态变化和缺口规则，并在可追问窗口内出现”。

因此，下一阶段不是直接进 Mac desktop shell，而是先加固 PC-1 的产品价值 gate、核心 schema/contract 和验收追踪。

## 2. 产品价值评审

结论：

- 产品定位足够强。
- PC-1 成功标准偏低。
- 必须把“实时性、状态驱动、可追问窗口、建议有效性、反例门禁、场景覆盖”写成必过验收。

P0 问题：

1. PC-1 只要求至少 1 张建议卡片可追溯，门槛过低。
2. 没有硬性证明卡片来自 `MeetingStateEvent + gap_rule`，而不是 LLM 总结后补卡。
3. 没有可计算的实时窗口字段，例如 `final_segment_at`、`state_event_at`、`card_created_at`、`too_late`。
4. EvidenceSpan 只验 ID，不验语义支持，可能出现“证据挂靠”。

P1 问题：

1. MVP 缺口类型不一致：`compatibility_gap` 在部分文档出现，但不在卡片白名单中。
2. 工程语境门禁缺少边界负例样本。
3. 状态生命周期不够具体：created/updated/answered/confirmed/dismissed 的状态转换还不完整。
4. 用户反馈闭环不完整：`too_late`、`too_intrusive` 未进入 PC-1 验收。
5. PC-1 replay/mock 容易被误解为真实 ASR 可用性证明。

## 3. 技术架构评审

结论：

- “shared core + worker/runtime + web/desktop adapter”的方向正确。
- 但当前核心领域契约还没有真正落在 `code/core`。
- 如果现在做桌面壳，会让桌面代码反向依赖 `asr_runtime/scripts` 实验脚本。

P0 问题：

1. `TranscriptSegment`、`EvidenceSpan`、`TranscriptReport` 仍在 `code/asr_runtime/scripts/transcript_report.py`。
2. `StreamingTranscriptEvent` 仍在 `code/asr_runtime/scripts/streaming_contract.py`。
3. `AnalysisSchedulerConfig/State/Decision` 仍在 `code/asr_runtime/scripts/incremental_scheduler.py`。
4. Mac desktop shell 前还没有 ASR worker 生命周期协议。
5. 文件回放/伪实时可能被误当作生产实时达标。

P1 问题：

1. Web MVP API 使用 `dict[str, Any]`，缺少版本化 schema。
2. 降级策略没有进入 API/repository 数据模型。
3. `InMemorySessionRepository` 会话覆盖和删除语义不适合桌面阶段。
4. core 只校验证据 ID 存在，不校验证据时间戳、segment 对齐、revision 语义和 quote 来源。

## 4. SDD/TDD 与验收追踪评审

结论：

- RTM 已经建立需求到测试的人工可读追踪。
- ASR/runtime 层测试相对扎实。
- PC Web/Core 仍是最小骨架测试。
- 尚未形成“验收项 ID -> 自动测试断言 -> gate 结果”的强闭环。

P0 问题：

1. Demo 最小价值验收无法被当前测试完整证明。
2. 实时性和 `too_late` 没有 gate。
3. 低质量 ASR、LLM timeout、schema invalid、证据缺失时的“不得输出强建议”没有完整测试闭环。

P1 问题：

1. 测试函数名/marker 没有关联 `REQ-*` 或 `PCWEB-*`。
2. PC Web/Core 只覆盖最小字段，ActionItem/Risk 等对象覆盖不足。
3. Markdown/JSON 报告 schema 测试不足。
4. 用户反馈质量资产没有 schema、导出、回归 gate。
5. 隐私/成本边界仍主要是人工 gate。

## 5. 修正优先级

### P0：进入下一轮实现前必须处理

1. 强化 PC-1 验收清单，新增 `AC-PCWEB-009` 到 `AC-PCWEB-020`。
2. 统一 MVP 建议卡片类型，决定 `compatibility_gap` 是否进入 PC-1。
3. 把核心 schema/contract 迁入或复制到 `code/core` 的稳定模块：
   - `TranscriptSegmentV1`
   - `EvidenceSpanV1`
   - `TranscriptReportV1`
   - `StreamingTranscriptEventV1`
   - `SuggestionCardV1`
   - `MeetingStateEventV1`
   - `DegradationStateV1`
4. 增加状态驱动卡片 gate：卡片必须引用 `state_refs`、`gap_rule_id`、`trigger_reason`、`scheduler_decision`。
5. 增加实时窗口 gate：卡片必须能计算 10-30 秒窗口，超窗标记 `too_late`。
6. 增加降级 gate：低质量/超时/schema 失败/证据缺失不得输出强建议。

### P1：PC-1 前端工作台前应处理

1. 增加工程序境负例/混合样本。
2. 明确状态生命周期：candidate、confirmed、answered、dismissed、superseded、revised。
3. 增加 EvidenceSpan 充分性负例。
4. 增加反馈资产 schema：keep/dismiss/mark_wrong/too_late/too_intrusive。
5. 增加 report schema gate，区分 confirmed/candidate/rejected/open/unconfirmed。
6. 增加 traceability gate，让测试和需求 ID 可机器检查。

### P2：进入桌面壳前处理

1. 建立统一 `make test` 或 `scripts/verify_all.sh`。
2. 增加 core import-boundary gate。
3. 明确模型许可证、缓存、下载和删除策略。
4. 增加 UI 截图/交互验收。
5. 建立 CI。

## 6. 对当前路线的影响

Accepted：

- 继续 PC Local Web MVP。
- 不直接进入 Mac desktop shell。
- 先加固 core schema/contract 和 PC-1 价值 gate。
- PC-1 只证明 Copilot 结构链路，不证明真实桌面音频采集和真实 ASR 质量。

Blocked：

- 在未完成核心 schema、状态驱动卡片、实时窗口 gate 前，不应把 PC-1 判定为可进入桌面壳。

## 7. 下一步建议

下一轮实现顺序：

```text
1. 新增 core schemas/contracts
2. 新增 PCWEB-009..020 验收项和测试计划
3. 增加 demo fixture/replay endpoint
4. 增加 state-driven card trace fields
5. 增加 realtime-card-gate 和 degradation-gate
6. 再做 Web 前端工作台
```

