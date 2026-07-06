# DECISION — Meeting Copilot 当前状态冻结与后续规则

> 日期：2026-07-06  
> 决策者：项目维护者 + Claude Code 复盘  
> 参照：`docs/project-release-readiness-reset-2026-07-05.md`（DEC-217）、`优化方案与计划书-2026-07-06.md`

## 1. 当前状态定性

**Meeting Copilot 当前为 Local Shadow Preview（工程演示级），不是 Production MVP，不是 Shadow Pilot ready。**

主链路 6 个环节全部未达到真实会议可用：

| 环节 | 状态 | 说明 |
|---|---|---|
| 音频采集 | blocked / no-op | `asr_worker_sidecar.py` no_execution；Tauri command 全 no-op |
| ASR 识别 | not_exited | 真实推理可跑但未接实时管线；7/4 真实麦克风测试召回 0.0（疑似音频管线 bug，待 Phase 1 重测） |
| 事件处理 | 真实（规则驱动） | EvidenceSpan/state/scheduler/candidate 链完整 |
| LLM 建议 | disabled_not_called | `app.py` LLM 执行写死 disabled |
| 卡片创建 | not_created | card_status 恒 not_created |
| 桌面运行时 | noop_only | Tauri 10 command 全 no-op |

唯一能跑通的是合成/mock/replay 驱动的影子链路，不含真实音频/ASR/LLM/桌面采集。

## 2. 后续工作规则（反循环）

**新任务除非直接改变以下 6 个决定性状态之一，否则不算主线进展，只算 maintenance：**

1. `quality_exit_status`（ASR 质量出口）
2. `real_mic_shadow_readiness_status`（真实麦克风准备度）
3. `user_can_start_real_mic_shadow_test_now`（用户能否现在开真实麦克风测试）
4. normalized 技术实体 recall（ASR 质量）
5. formal card/report evidence status（正式卡片/纪要证据状态）
6. 真实会议反馈指标

**反循环规则**：连续两个任务只加 readiness/preflight/approval/preview/wrapper，第三个必须停下来选 ASR quality exit / degraded pilot / pivot。**不再产出新的 readiness/report-only 文档。** 决策记进本文件一段话，不再写进 855KB decision-log。

## 3. 已采纳的优化路线（5 阶段）

见 `优化方案与计划书-2026-07-06.md`。摘要：

- **Phase 0**（止血）：git 基线、密钥迁 .env、structlog 日志、归档脚手架 ← 进行中
- **Phase 1**（ASR 决断，命门）：干净音频重测 FunASR/sherpa/远程，决断路线
- **Phase 2**（主链路打通）：拆 app.py、接通实时 ASR/LLM、真实成卡、E2E 测试
- **Phase 2.5**（方案考量卡）：从缺口雷达扩展到会议助手
- **Phase 3**（前端重构）：按 `设计稿/workbench-v1.html` 落地 React+TS
- **Phase 4**（桌面+加固）：Tauri 真实化、sidecar、系统音频、CI

## 4. 关键技术判断

- **产品方向正确**（中文技术会议实时 Copilot，证据驱动，本地 ASR 优先）。问题在方法学（过度规划/脚手架堆积）和优先级，不在方向。
- **ASR 是命门**。7/4 真实麦克风测试 FunASR 输出"我是我样"糟糕到不可能是 ASR 真实水平，更可能是音频管线 bug。Phase 1 用干净音频重测后再决断是否 pivot 远程，**不要在重测前 pivot**。
- **LLM 只做 request draft 不执行是过度保守**。Phase 2 接通真实 LLM（带开关 + 用量记录），安全靠开关和日志，不是靠永远不调用。

## 5. 文档体系约束

- docs/ 现有 157 个文档 + 855KB decision-log 是病因的症状，不再扩充。
- 新决策写进本文件（DECISION.md），不新增 pcweb-XXX/drv-XXX plan。
- 实现进展用 git commit history 跟踪，不用文档量冒充进展。

## 6. Phase 进度

- [x] Phase 0-1: git 基线（独立仓库，commit 65da6ec）
- [x] Phase 0-2: 密钥迁 .env + 依赖补全（commit 8972d92）
- [x] Phase 0-3: structlog 结构化日志（commit 1ac0758）
- [x] Phase 0-4: DECISION.md 冻结（归档延后到 Phase 2，commit ab65708）
- [x] Phase 1-1: 干净音频 ASR 重测（见 §7）
- [x] Phase 1-2: ASR 路线决断 = **本地继续**（文档默认 + Phase 1-1 证据支持；远程作为可选高质量模式保留，不默认开启）
- [ ] Phase 2: 主链路打通
- [ ] Phase 2.5: 方案考量卡
- [ ] Phase 3: 前端重构
- [ ] Phase 4: 桌面 + 加固

## 7. Phase 1-1 ASR 干净音频重测结论（2026-07-06）

用 `simulated-release-review.16k.wav`（macOS `say` 生成的干净中文技术口播，17.9s）重测：

| 引擎 | RTF | 延迟 | 转写可读性 | 关键实体 |
|---|---|---|---|---|
| FunASR | 0.95 | 16952ms | 可读，基本正确 | 灰度✓ 错误率✓ 回滚✓ 监控✓ 负责人张三✓ 测试用例✓；checkout-service→payment gate✗ P99→t九九 staging✗ |
| sherpa-onnx | 0.026 | 461ms | 可读，略差 | 错误率✓ 回滚✓ 监控✓；灰度→先挥✗ 用例→用力✗ P99→九九 staging✗ |

**结论**：
1. **两个本地 ASR 在干净音频上都输出可读中文**，都没有"我是我样"。这证实 7/4 真实麦克风测试的"我是我样"是**音频采集管线 bug**，不是 ASR 引擎问题。本地 ASR 路线在干净音频上可行。
2. FunASR 准确度更高但慢（RTF 0.95，边缘实时）；sherpa 快得多（RTF 0.026）但准确度略低（灰度→先挥）。
3. 两者都漏/错英文技术实体（checkout-service/P99/staging），**需要热词**才能召回。
4. 真正的阻塞是**真实麦克风采集管线**（sidecar no_execution + Tauri no-op），不是 ASR 质量。

**Phase 1-2 建议**：不 pivot 远程。本地继续（FunASR 准确优先 / sherpa 实时优先，按场景选），加技术热词，Phase 2 修真实麦克风采集管线。
