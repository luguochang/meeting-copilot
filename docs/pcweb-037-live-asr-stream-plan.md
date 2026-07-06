# PCWEB-037 本地 live_asr_stream 骨架计划

> 日期：2026-07-01  
> 状态：Implemented as local synthetic ASR event source skeleton  
> 目的：把 PC Web MVP 从 fixture-only Live Mock 推进到可接真实 ASR worker 的本地事件源契约，但仍不调用远程 ASR/LLM，不接桌面麦克风/系统音频采集。

## 1. 背景

PCWEB-029 到 PCWEB-036 已经证明 Web 工作台可以通过 `EventSource` 消费 live envelope，并能增量展示 transcript、EvidenceSpan、state、suggestion card、revision lifecycle 和 `suggestion_invalidated` 审计事件。

当前最大缺口不是继续增加 Mock UI 功能，而是建立真实 ASR worker 进入 Web/API 的最小接口形状。否则项目会停留在“fixture 能演示实时”的阶段，无法判断 Mac 桌面壳接入音频后是否能复用同一套事件消费链路。

## 2. 推荐决策

已新增 `PCWEB-037: 本地 live_asr_stream 骨架`。

核心原则：

- 先做本地、免费、可复现的 file/mock ASR event source，不接麦克风、不接系统音频、不调用云 ASR。
- 复用 `code/asr_runtime/scripts/streaming_contract.py` 的 `partial/final/revision/error/end_of_stream` 语义。
- Web backend 输出新的 source：`source=live_asr_stream`、`trace_kind=live_event`，与 `live_mock_stream` 明确区分。
- 初期只覆盖 transcript/evidence/provider status/eos，不在同一轮接真实 scheduler、真实 LLM 或替代建议卡生成。
- 仍使用现有 Web 工作台 live event applier；前端可以新增 Live ASR/Local ASR 模式，但必须清楚标记这是本地文件/事件源回放，不是桌面实时采集。

## 3. 最小功能范围

已按 4 个 TDD 任务实现：

1. 后端事件转换器
   - 输入：ASR runtime streaming event dict 列表。
   - 输出：Web live envelope 列表。
   - `partial` -> `transcript_partial`，不得生成 EvidenceSpan。
   - `final` -> `transcript_final`，生成最小 EvidenceSpan。
   - `revision` -> `transcript_revision`，携带 `revision_of`；如果有旧 evidence，则生成 `superseded_evidence_spans`。
   - `error` -> `provider_error`。
   - `end_of_stream` -> `evaluation_summary` 或 `stream_closed` 风格终止事件；具体命名实现前再定，但必须可让前端关闭有限 stream。

2. 后端本地测试 endpoint
   - 建议先用 JSON payload 创建 `live_asr_stream` session，例如 `POST /live/asr/mock/sessions` 或 `POST /live/asr/events/sessions`。
   - endpoint 只接受事件 JSON，不接受音频文件，避免误以为桌面采集已完成。
   - `GET /live/asr/sessions/{id}/events(.sse)` 输出 `source=live_asr_stream`。

3. 前端模式接入
   - 在工作台中增加可测试入口，加载内置本地 ASR event fixture 或测试 endpoint。
   - 复用现有 EventSource 处理和 transcript/evidence rendering。
   - UI 文案不得声明 microphone/system-audio capture 已完成。

4. 文档与质量门
   - 新增 `PCWEB-037` 和 `AC-PCWEB-030`。
   - 更新 checklist 的 P0 缺口：从“只有 mock live source”推进为“已有 local ASR event source skeleton，仍缺桌面音频采集和真实 provider endpoint final 质量验证”。
   - `tools/run_quality_gate.py --profile pc-web` 继续不调用远程 provider。

## 4. 明确范围外

- macOS 麦克风/系统音频采集。
- Windows WASAPI loopback。
- FunASR/sherpa 模型加载、模型下载和真实音频转写。
- 远程 ASR provider。
- 真实 LLM 中转站调用。
- 真实 scheduler event log。
- revision 后完整 state engine 重算和 LLM 替代卡生成。

这些必须留到后续阶段，否则会把下一轮从“接口骨架”扩大成桌面/模型集成项目，影响可验证性。

## 5. 验收口径

`PCWEB-037` 只有在以下证据齐全时才算完成：

- 后端单元测试证明 partial 不生成 EvidenceSpan，final/revision 生成可点击 EvidenceSpan。
- 后端测试证明 source/trace_kind 与 `live_mock_stream` 不混淆。
- SSE 测试证明具名事件可被浏览器 `EventSource` 消费。
- 浏览器 smoke 证明 Web 工作台可以从空 live view 消费 `live_asr_stream`，增量显示 transcript/evidence，并在 eos 后关闭有限 stream。
- 文档明确这不是桌面实时音频采集，也不证明中文技术会议 ASR 质量达标。

## 6. 费用与隐私

- 默认不读取 `configs/local/**`。
- 默认不调用远程 ASR/LLM。
- 默认不读取用户真实录音路径。
- 测试数据只使用仓库内 synthetic streaming event fixture 或测试内联 JSON。
- 不把 API key、真实录音、临时音频 chunk、模型缓存写入仓库。
