# Decision Log

> 日期：2026-06-19  
> 用途：记录 Meeting Copilot 的重要产品、技术、成本、隐私和阶段决策。后续所有关键方向变化必须追加到本文档，并同步更新相关专项文档。

## 记录规则

重要决策必须落文档，不能只停留在聊天记录或临时实验输出中。

必须记录的决策类型：

- 产品定位、目标用户、MVP 范围变化。
- 架构分层、客户端形态、开源复用方式变化。
- ASR/LLM provider 选择、默认/可选状态变化。
- 成本、隐私、数据流、远程调用边界变化。
- 进入或退出某个开发阶段的 Go/No-Go。
- 测试、评测、验收门槛变化。

每条决策应包含：

- 状态：Accepted / Rejected / Superseded。
- 背景：为什么需要决策。
- 决策：最终选择。
- 原因：为什么这么选。
- 替代方案：考虑过但未选的方案。
- 影响范围：代码、文档、产品或测试影响。
- 成本/隐私影响：如适用。
- 验证方式：测试、smoke、评测或后续验收。
- 关联文档：同步更新或依赖的文档。
- 复审条件：什么情况下需要重新评估。

## DEC-001: 产品定位为中文技术会议实时 Copilot

- 日期：2026-06-18
- 状态：Accepted

背景：

用户希望做一个会议中实时辅助工具，而不是会后才总结的录音转文字工具。

决策：

产品定位为中文技术会议实时 AI Copilot。核心价值不是 ASR 转写，而是在会议中持续维护结构化状态，并低频提醒工程讨论缺口。

原因：

- 普通转写和会后总结已经有大量现成工具，差异化不足。
- 技术会议中更有价值的是及时发现 owner、deadline、rollback、test、monitoring 等缺口。
- 证据化状态机可以让 AI 输出可追溯，降低幻觉风险。

替代方案：

- 通用会议转写工具：拒绝，差异化不足。
- 会后总结工具：拒绝，不能满足实时辅助目标。
- 通用 AI 会议问答助手：拒绝，MVP 范围过大。

影响范围：

- `product-requirements.md`
- `meeting-state-model.md`
- `realtime-suggestion-cards.md`
- `requirements-traceability-matrix.md`

成本/隐私影响：

- 产品必须显式区分本地 ASR、LLM 远程调用和会话保存状态。

验证方式：

- MVP 必须至少产出带 EvidenceSpan 的会议状态、建议卡片和会后报告。
- 只有转写和总结不算 MVP 成功。

复审条件：

- 如果实时建议卡片长期误报过高或用户保留率过低，需要重新评估产品形态。

## DEC-002: Mac-first，Windows later

- 日期：2026-06-18
- 状态：Accepted

背景：

用户当前主机是 Mac，初始目标是装在笔记本电脑上，会中本地运行。

决策：

MVP 首发只承诺 macOS Apple Silicon。Windows 放到第二阶段。

原因：

- macOS 是当前真实开发和试用环境。
- 同时兼容 Windows 会显著扩大音频采集、权限、打包和测试复杂度。
- ASR provider、LLM 调度和产品价值验证不依赖首轮跨平台。

替代方案：

- 首版同时支持 Windows/macOS：拒绝，范围过大。
- 先做纯 Web SaaS：拒绝，用户目标是笔记本本地会议助手，且隐私/音频采集要求更适合本地客户端。

影响范围：

- `mac-mvp-requirements-and-technical-plan.md`
- `feature-map.md`
- `implementation-roadmap.md`
- `requirements-traceability-matrix.md`

成本/隐私影响：

- 首版减少跨平台适配成本。
- 本地优先更符合会议隐私预期。

验证方式：

- 后续桌面采集 MVP 只需先验证 macOS 麦克风/系统音频。

复审条件：

- Mac MVP 价值跑通后，再评估 Windows 音频采集和打包。

## DEC-003: 默认本地 ASR，远程 ASR 非默认

- 日期：2026-06-18
- 状态：Accepted

背景：

用户明确希望尽量不要增加额外收费项，除 LLM 中转站外，不希望默认依赖付费 ASR。

决策：

默认 ASR 走本地/open-source provider。远程 ASR 只作为 bake-off 对照、企业可选高质量模式或用户显式配置项，不进入默认 MVP 链路。

原因：

- 本地 ASR 不按分钟产生第三方 ASR API 费用。
- 会议录音隐私更容易解释。
- 远程 ASR 可作为质量标尺，但不能成为隐藏成本。

替代方案：

- 默认接阿里/讯飞/腾讯/百度云 ASR：拒绝，增加默认收费和隐私复杂度。
- 完全不评估远程 ASR：拒绝，缺少质量上界参考。

影响范围：

- `asr-provider-strategy.md`
- `asr-bakeoff-guide.md`
- `privacy-and-data-flow.md`
- `requirements-traceability-matrix.md`

成本/隐私影响：

- 默认远程模型成本只允许来自 OpenAI-compatible LLM 中转站。
- 远程 ASR 必须显式启用并可见。

验证方式：

- 配置和文档不得默认启用 remote-paid provider。
- 报告需标记 provider 类型。

复审条件：

- 如果本地 ASR 在中文技术会议上长期不达最低可用标准，而远程 ASR 明显达标，再重新评估可选高质量链路和定价。

## DEC-004: LLM 使用 OpenAI-compatible 中转站

- 日期：2026-06-18
- 状态：Accepted

背景：

用户提供了 OpenAI-compatible 中转站配置，模型为 `gpt-5.5`。

决策：

LLM provider 层以 OpenAI-compatible chat completions 协议为默认适配目标。真实 key 只存放在 `configs/local/`，不写入公开文档。

原因：

- OpenAI-compatible 是当前国产/中转站/第三方大模型常见协议形态。
- 可用同一接口兼容后续模型替换。
- 中转站 smoke 已验证连通。

替代方案：

- 写死某一家 SDK：拒绝，降低 provider 可替换性。
- 在文档中保存完整 key：拒绝，违反安全边界。

影响范围：

- `asr_bakeoff/llm_gateway`
- `meeting_analysis.py`
- `privacy-and-data-flow.md`
- `local-run-notes.md`

成本/隐私影响：

- LLM 是默认主要远程成本来源。
- API key 必须被 ignore，并通过扫描防止泄漏。

验证方式：

- `python3 -m asr_bakeoff.llm_smoke --config ../../configs/local/llm-gateway.local.json`
- 密钥扫描不得命中真实 key。

复审条件：

- 如果中转站协议或模型不稳定，再引入多 provider fallback。

## DEC-005: 核心智能层自研，底层开源能力复用，客户端择机二开

- 日期：2026-06-19
- 状态：Accepted

背景：

用户最初希望找开源项目做二次开发，但当前验证发现开源会议助手通常覆盖录音、转写和会后总结，不覆盖本产品的实时工程缺口 Copilot 能力。

决策：

采用 Core-first 策略：

```text
核心智能层自研
底层通用能力复用开源库
客户端壳择机二开或自建
```

自研范围：

- Transcript event contract。
- Transcript normalizer/stabilizer。
- EvidenceSpan。
- Incremental scheduler。
- Meeting state engine。
- Suggestion card engine。
- Engineering context gate。
- Evidence-backed report。
- Quality/eval pipeline。

复用范围：

- ASR 模型和推理库：FunASR、sherpa-onnx，后续可选 SenseVoice。
- 音频处理：ffmpeg、soundfile、numpy 等。
- LLM 协议：OpenAI-compatible。
- 桌面采集/UI 壳：后续评估 Meetily、Tauri、Electron、Swift 等。

原因：

- 产品壁垒在实时状态机、建议卡片和证据链，不在通用录音/转写。
- 直接 fork 一个转写工具容易被其“转写 + 总结”架构限制。
- 核心层独立后，未来可接 Web MVP、Tauri、Electron 或开源桌面壳。

替代方案：

- 直接 fork Meetily 或类似项目作为主仓库：暂不采用，先评估 license、架构和可插拔性。
- 完全从零写客户端和 ASR：拒绝，重复造轮子。
- 只借鉴不开源复用：拒绝，浪费成熟生态能力。

影响范围：

- `project-structure.md`
- `implementation-roadmap.md`
- 后续需新增 `codebase-strategy-and-open-source-reuse.md`

成本/隐私影响：

- 自研核心层会增加前期工程量，但降低长期架构被基座限制的风险。
- 开源复用需持续检查 license、模型体积、下载路径和本地缓存。

验证方式：

- 核心层必须可被本地 Web MVP、桌面壳和测试脚手架复用。
- 客户端不得直接耦合 LLM prompt 或 EvidenceSpan 规则。

复审条件：

- 如果某个开源桌面项目 license 允许、架构清晰、音频采集能力成熟且易接入 core，可进入 fork/二开评估。

## DEC-006: 先做本地 Web MVP 垂直切片，再做 Mac 桌面采集

- 日期：2026-06-19
- 状态：Accepted

背景：

当前最大不确定性是实时建议卡片是否有产品价值，而不是窗口能否打开或 App 能否打包。

决策：

下一阶段进入 MVP 垂直切片开发。先做本地 Web MVP 验证核心链路，再进入 Mac 桌面采集壳。

阶段顺序：

```text
Local Web MVP
  -> 文件回放 / 可控实时事件流
  -> transcript
  -> meeting state
  -> suggestion cards
  -> evidence panel
  -> report

Mac Desktop Shell
  -> mic/system audio capture
  -> local ASR worker
  -> local storage
  -> packaging
```

原因：

- Web MVP 能最快验证核心产品价值。
- 直接做 Mac 客户端会同时引入音频权限、系统音频采集、打包签名、公证等工程风险。
- 核心层独立后，Web MVP 不会浪费，后续桌面客户端可以复用。

替代方案：

- 直接做 Mac 原生客户端：暂不采用，风险过早集中。
- 继续只做 ASR 评测：拒绝，容易陷入无限评测。
- 先做完整 SaaS：拒绝，不符合本地隐私和会议音频采集目标。

影响范围：

- `implementation-roadmap.md`
- 后续 MVP 设计文档和实现计划。

成本/隐私影响：

- Web MVP 阶段仍本地运行，不默认上传原始音频。
- LLM 调用继续通过中转站，受 scheduler 和 usage 记录约束。

验证方式：

- 能本地打开一个 MVP 页面，展示 transcript、状态、建议卡片、证据和报告。
- 至少 2-3 个技术会议脚本可跑通。

复审条件：

- Web MVP 证明建议卡片有效后，进入 Mac 桌面采集。

## DEC-007: ASR 评测阶段收束，进入 MVP 垂直切片开发

- 日期：2026-06-19
- 状态：Accepted

背景：

已有 sherpa-onnx 和 FunASR 本地验证、LLM smoke、scheduler、EvidenceSpan、demo gate 等结果。继续无限 provider 横评会拖慢产品开发。

决策：

ASR 泛评测阶段收束。下一阶段不再无限尝试新 provider，只保留有限的质量门槛验证。

保留的评测：

- FunASR + normalizer 的最低可用门槛。
- 实时建议有效性评测。
- 进入桌面采集前的 30-60 分钟稳定性评测。

暂停的评测：

- 大规模云 ASR 横评。
- 无限尝试 Whisper/SenseVoice/更多 sherpa 模型。
- 与 MVP 无关的 provider 扩散。

原因：

- 当前结果已足够做产品方向决策：Go，但收窄。
- 产品价值不取决于 ASR 原始文本完美，而取决于可追溯状态和建议是否有用。
- 无限评测会偏离 MVP 开发。

替代方案：

- 继续 provider bake-off：拒绝，当前收益递减。
- 完全不再评估 ASR：拒绝，仍需最低可用门槛和长会议稳定性。

影响范围：

- `implementation-roadmap.md`
- `requirements-traceability-matrix.md`
- `local-run-notes.md`

成本/隐私影响：

- 避免不必要的云 ASR 成本。
- 保持本地默认路线。

验证方式：

- 下一阶段交付本地 MVP 垂直切片，而不是新增一堆 provider 报告。

复审条件：

- 如果 FunASR + normalizer 低于最低可用门槛，再补一个远程 ASR 对照。

## DEC-008: FunASR 为中文质量主候选，sherpa-onnx 为轻量端侧备选

- 日期：2026-06-19
- 状态：Accepted

背景：

本机已验证 sherpa-onnx 和 FunASR 的性能、体积、事件输出和中文技术词风险。

决策：

当前 ASR 默认候选排序：

```text
FunASR / Paraformer streaming: 中文质量主候选
sherpa-onnx: 轻量端侧备选和性能基线
远程 ASR: 质量对照，不默认启用
SenseVoice: 后续可选二遍修正或质量上限候选
```

原因：

- sherpa-onnx 很快、很轻，但中文技术词错识别明显。
- FunASR 更适合作为中文质量路线，但依赖重、模型大、RTF 接近实时边界。
- 两者都不能裸用，normalizer/stabilizer 必选。

替代方案：

- 只用 sherpa-onnx：拒绝，中文技术词质量风险大。
- 只用 FunASR：暂不锁死，生产打包和性能仍需验证。
- 默认远程 ASR：拒绝，违背默认成本边界。

影响范围：

- `asr-provider-strategy.md`
- `code/asr_runtime/scripts/transcribe_funasr.py`
- `code/asr_runtime/scripts/transcribe_sherpa_onnx.py`

成本/隐私影响：

- FunASR 本地模型下载和磁盘成本较高，但不产生云 ASR API 费。
- sherpa-onnx 更适合低资源兜底。

验证方式：

- FunASR streaming 文件回放已产出 30 partial / 6 window-final / 1 eos。
- scheduler 已验证 partial 不触发 LLM。
- 仍需热词/术语表/normalizer 和长会议稳定性验证。

复审条件：

- 如果 FunASR 无法满足实时性或打包要求，再提升 sherpa 或远程 ASR 可选路线优先级。

## DEC-009: 所有正式状态、建议和纪要必须 EvidenceSpan-backed

- 日期：2026-06-18
- 状态：Accepted

背景：

实时 AI 会议助手容易产生幻觉、误判 owner 或把候选讨论写成正式结论。

决策：

所有正式会议状态、建议卡片和会后纪要都必须引用 EvidenceSpan。没有证据的内容只能作为草稿或待确认，不能进入正式结论。

原因：

- 证据链是区分本产品和普通 LLM 总结的核心可信度机制。
- 用户必须能回到原始片段复盘。
- 低质量 ASR 或 LLM 幻觉不能被包装成确定结论。

替代方案：

- 让 LLM 自由总结：拒绝，幻觉风险高。
- 只保存全文 transcript，不做 EvidenceSpan：拒绝，不能支撑可信建议。

影响范围：

- `meeting-state-model.md`
- `meeting_analysis.py`
- `meeting_events.py`
- `transcript_report.py`
- `realtime-suggestion-cards.md`

成本/隐私影响：

- 需要本地保存证据片段和时间戳。
- 会后报告必须避免无证据断言。

验证方式：

- tests/test_meeting_analysis.py
- tests/test_meeting_events.py
- demo_eval evidence 引用检查。

复审条件：

- 不建议放宽。除非某类输出明确标记为“草稿/待确认”。

## DEC-010: 重要决策必须写入文档

- 日期：2026-06-19
- 状态：Accepted

背景：

用户明确要求每次重要决策都要记录下来，否则后续开发容易偏离方向。

决策：

后续所有重要决策必须写入本文档，并同步更新相关专项文档和需求追踪矩阵。

原因：

- 项目容易偏成 ASR 评测、普通转写、会后总结或纯 UI 壳。
- 文档是后续开发不偏航的约束。
- SDD/TDD 需要需求、决策、测试、结果可追溯。

替代方案：

- 只在聊天中确认：拒绝，不可追溯。
- 只写在 local-run-notes：拒绝，实验记录和架构决策职责不同。

影响范围：

- 所有 docs。
- 后续实现计划。

成本/隐私影响：

- 决策文档不得包含真实 API key、真实录音路径或敏感数据。

验证方式：

- 每次阶段性汇报列出新增/更新的决策条目。
- 需求矩阵和专项文档与 decision log 保持一致。

复审条件：

- 不建议放宽。

## DEC-011: PC 客户端采用共享核心/UI + 平台适配器 + 分平台打包

- 日期：2026-06-19
- 状态：Accepted

背景：

用户询问 Windows 和 Mac 客户端是否可以同一套代码直接导出安装包，还是需要两套开发。

决策：

PC 客户端采用以下架构：

```text
shared core
shared UI
platform adapters
separate packaging/signing/release pipelines
```

不做 Windows/Mac 两套业务代码，也不承诺完全无平台差异的一键跨平台。

可复用：

- EvidenceSpan、meeting state、suggestion cards、LLM scheduler、report。
- Web UI 主体。
- OpenAI-compatible LLM gateway。
- ASR provider 抽象和 transcript event contract。
- 本地数据 schema、导出格式、评测和质量门禁。

分平台实现：

- macOS/Windows 音频采集。
- 麦克风、系统音频、屏幕录制等权限。
- 托盘、快捷键、自动启动。
- ASR worker 打包和模型缓存路径。
- 安装包、签名、公证、SmartScreen 和自动更新。

原因：

- 核心 Copilot 价值不应绑定某个桌面框架。
- macOS 和 Windows 的音频、权限、签名、安装器差异是真实存在的。
- 共享核心/UI 能避免重复开发，platform adapter 能避免把系统差异污染到业务层。

替代方案：

- Windows/Mac 两套完整业务代码：拒绝，维护成本过高且容易行为不一致。
- 完全依赖桌面框架一键跨平台：拒绝，会低估系统音频、权限、签名和模型分发差异。

影响范围：

- `platform-packaging-and-store-compliance.md`
- `project-structure.md`
- 后续 `code/core`、`code/desktop/platform`、`code/web_mvp`

成本/隐私影响：

- 会增加 platform adapter 和分平台测试成本。
- 但核心层不重复，长期成本更低。
- 音频采集和模型缓存必须按平台单独做权限和数据目录设计。

验证方式：

- Web MVP 能复用 core。
- Mac desktop shell 只接 platform adapter，不重写 core。
- 后续 Windows adapter 接入时不改 EvidenceSpan/state/suggestion 规则。

复审条件：

- 如果 Tauri/Electron/Flutter 中某个框架无法稳定承载 sidecar/audio capture，再重评桌面壳，但不放弃 core/platform 分层原则。

## DEC-012: 桌面壳先不锁死，路线为 Local Web MVP -> Mac desktop shell

- 日期：2026-06-19
- 状态：Accepted

背景：

当前最大不确定性仍是实时建议卡片和证据化状态是否有产品价值，而不是最终安装包框架。

决策：

继续执行：

```text
Local Web MVP
  -> 验证 transcript/state/cards/evidence/report
  -> 再进入 Mac desktop shell
```

桌面壳优先预研 Tauri，Electron 作为备选，最终技术栈在 Mac desktop capture 阶段再锁定。

原因：

- 过早锁死桌面框架会把音频权限、打包、签名、更新和模型分发复杂度提前引入。
- Tauri 更轻，适合复用 Web UI 和调用 sidecar worker。
- Electron 生态更成熟，可作为打包、自动更新和音频采集遇到阻塞时的备选。
- 当前核心层已经能通过本地脚本和 Web MVP 路径验证，不依赖桌面壳。

替代方案：

- 立即写 Tauri/Electron 完整客户端：暂不采用，容易拖慢核心价值验证。
- 只做 Web SaaS：拒绝，不符合本地会议音频和隐私目标。

影响范围：

- `implementation-roadmap.md`
- `mac-mvp-requirements-and-technical-plan.md`
- `platform-packaging-and-store-compliance.md`

成本/隐私影响：

- 本地 Web MVP 阶段不引入商店、签名和自动更新成本。
- LLM 仍是默认远程成本，ASR 默认本地。

验证方式：

- Local Web MVP 必须展示 transcript、状态、建议卡片、证据和报告。
- 进入桌面壳前必须有明确的 adapter API。

复审条件：

- Web MVP 证明建议卡片有效后，做 Tauri/Electron/原生壳选型评估。

## DEC-013: Mac MVP 初期采用 App Store 外分发，不优先 Mac App Store

- 日期：2026-06-19
- 状态：Accepted

背景：

用户关心 Mac 版安装包和后续上架路径。产品涉及会议录音、系统音频、本地 ASR worker、模型下载和本地文件保存。

决策：

Mac MVP 初期采用：

```text
Apple Developer Program
Developer ID signing
notarization
.dmg / .pkg direct download
```

Mac App Store 后置评估。

原因：

- App Store 外分发可以更早验证桌面会议场景。
- Mac App Store sandbox 可能影响系统音频采集、sidecar worker、模型下载和本地文件访问。
- Developer ID + notarization 是 Mac 直接分发更现实的 MVP 路径。

替代方案：

- 首发 Mac App Store：暂不采用，审核和 sandbox 风险过早。
- 无签名直接分发：拒绝，Gatekeeper 和用户信任风险太高。

影响范围：

- `platform-packaging-and-store-compliance.md`
- 后续 Mac desktop packaging。

成本/隐私影响：

- 需要 Apple Developer Program，官方费用为 99 USD/年或本地货币。
- 录音、转写、LLM 中转站发送、保存、删除都必须有清晰披露。

验证方式：

- 后续 Mac 包需要完成签名、公证、安装、启动、更新和卸载 smoke。
- 麦克风/系统音频权限文案必须可见。

复审条件：

- 音频采集、worker、模型管理和隐私披露都能满足 sandbox/App Review 后，再评估 Mac App Store。

## DEC-014: Windows 第二阶段 direct download 优先，Microsoft Store 作为补充分发

- 日期：2026-06-19
- 状态：Accepted

背景：

Windows 需要单独处理 WASAPI loopback、设备差异、安装器、签名、SmartScreen、杀软误报和自动更新。

决策：

Windows 在 Mac MVP 后进入。第一轮优先：

```text
signed .exe / .msi direct download
```

Microsoft Store、MSIX、Artifact Signing / Trusted Signing 作为后续分发和信任优化选项。

原因：

- 直接下载路径更适合早期内测和快速迭代。
- Microsoft Store 对 MSIX 会有更好的签名和 SmartScreen 体验，但会引入 Store 包形态和提交流程。
- Microsoft 官方资料显示 EV 证书不再默认绕过 SmartScreen，OV/EV/Artifact Signing 都需要积累 reputation。

替代方案：

- 首发 Microsoft Store：暂不采用，流程更重。
- 不签名直发：拒绝，SmartScreen、企业策略和用户信任风险高。

影响范围：

- `platform-packaging-and-store-compliance.md`
- 后续 Windows platform adapter。

成本/隐私影响：

- Microsoft Store 新个人开发者注册流程可免注册费，但仍需身份验证。
- 代码签名证书或 Artifact Signing 可能产生持续费用。
- Windows 音频采集也必须明确用户授权和录音提示。

验证方式：

- Windows 阶段需要单独做安装、签名、SmartScreen、音频设备、蓝牙耳机和会议软件兼容 smoke。

复审条件：

- 如果 direct download 的 SmartScreen/杀软摩擦过高，提前评估 Microsoft Store/MSIX 或 Artifact Signing。

## DEC-015: 移动端不进入当前 MVP，只作为 companion app

- 日期：2026-06-19
- 状态：Accepted

背景：

用户询问未来 iOS/Android 上架和移动端规划，但当前产品核心场景是笔记本电脑会议实时辅助。

决策：

iOS/Android 不进入当前 MVP。未来只按 companion app 规划：

```text
查看会议记录
查看建议卡片和证据
线下会议麦克风录音
报告阅读/分享
不承诺捕获桌面会议系统音频
不承诺捕获其他 App 音频、通话音频或后台隐蔽录音
```

原因：

- 移动端不适合作为桌面会议系统音频主采集端。
- iOS/Android 的后台录音、系统音频、通话音频和其他 App 音频都有严格限制和审核风险。
- 移动端会提前引入商店审核、隐私政策、账号同步、云端数据和跨端状态问题。
- PC-first 更符合用户当前主场景。

替代方案：

- 移动端和 PC 同步开发：拒绝，会稀释 MVP 资源。
- 移动端作为主录音端：拒绝，不适合捕获电脑会议声音。

影响范围：

- `platform-packaging-and-store-compliance.md`
- `implementation-roadmap.md`
- 后续移动端需求不得混入 PC MVP。

成本/隐私影响：

- 暂不产生 Apple/Google 移动端上架成本。
- 未来移动端涉及麦克风、会议内容、云同步、第三方 AI 服务时，必须单独做隐私和合规设计。

验证方式：

- 当前 MVP 需求矩阵不加入移动端实现项。
- 未来移动端启动前必须有独立 PRD、数据流、隐私政策和商店合规清单。

复审条件：

- Mac/Windows 客户端稳定后，且用户明确需要移动查看/分享/线下录音场景时再启动。

## DEC-016: 中国大陆移动分发前必须先确认主体、备案、隐私和资质路径

- 日期：2026-06-19
- 状态：Accepted

背景：

用户询问 iOS/Android 上架是否只是交开发者费用，尤其个人开发者身份是否容易上架。

决策：

中国大陆 iOS/Android 上架不能按“交平台费即可”处理。进入中国大陆移动分发前，必须先确认：

- 域名、服务器和 ICP 备案。
- APP 备案。
- 隐私政策、个人信息处理规则、账号注销和数据删除。
- 麦克风/录音/会议文本/音频文件权限和用途披露。
- 第三方 SDK 和第三方 AI/LLM 服务披露。
- 必要时的软件著作权或 APP 电子版权认证。
- 应用名称、包名、主体、备案信息一致性。
- 是否需要公司主体承载品牌、商业化、备案和应用市场资质。

原因：

- 本产品处理会议录音、转写文本、技术讨论、可能的企业信息和第三方 LLM 调用，隐私敏感度高。
- Apple 中国大陆 App Store、国内 Android 市场和监管要求都可能涉及 ICP/APP 备案和额外材料。
- 国内 Android 市场不是统一入口，不同市场有不同材料要求。
- 个人开发者身份在品牌、商业化、备案和资质材料上存在现实限制。

替代方案：

- 个人身份直接多市场上架：暂不采用，材料和合规不确定性高。
- 先做海外移动端商店：可作为后续备选，但仍需隐私和 AI 数据披露。

影响范围：

- `platform-packaging-and-store-compliance.md`
- 后续移动端路线。

成本/隐私影响：

- 备案本身通常不是主要费用，但域名、服务器、材料、软著、法律文本、人力和时间会成为真实成本。
- 如做订阅、IAP 或云服务套餐，还需评估 App Store / Google Play 数字商品服务费。

验证方式：

- 移动端立项前输出专项合规清单和费用周期表。
- 不把移动端上架列入当前 PC MVP 的验收。

复审条件：

- 决定进入中国大陆 App Store 或国内 Android 应用市场前复审。

## DEC-092: 从 readiness mode 切换到 desktop runtime validation mode

- 日期：2026-07-02
- 状态：Accepted

背景：

用户暂停项目并要求复盘：当前已经执行大量 PCWEB readiness、dry-run、preflight、policy 边界，但主线仍未完成，产品初心可能被安全边界和评测循环稀释。多 Agent 对抗审查后结论一致：项目定位仍有价值，但不能继续把 no-op/readiness 作为主线进展。

决策：

下一阶段从：

```text
PCWEB readiness / no-op policy mode
```

切换到：

```text
Desktop runtime validation mode
```

主线改为：

```text
公开/合成音频模拟
  -> 真实 Mac 桌面壳运行
  -> 麦克风采集
  -> 本地 ASR worker
  -> final/revision transcript
  -> meeting state / engineering gap candidate
  -> 受控 LLM suggestion cards
  -> 用户真实麦克风会议验证
```

后续不再新增只输出 `ready_for_explicit_*_approval` 的 PCWEB 阶段作为主线。安全边界继续保留，但每个增量必须推进真实链路中的一个环节。

原因：

- 产品价值不是“转写 + 会后总结”，而是中文技术会议中实时发现工程缺口。
- 当前 Web MVP、core gate、ASR runtime 和 Tauri scaffold 有价值，但没有证明真实桌面产品价值。
- 继续扩展 dry-run/provider/card lifecycle/readiness 会形成过程拖延。
- 公开授权音频和合成技术会议音频可以在真实麦克风会议前降低 ASR 和管线风险。
- 最终产品价值必须由真实中文技术会议验证，不能只靠 fixture 或 synthetic UI success。

替代方案：

- 继续 PCWEB-092 no-op native bridge readiness：降级为非主线，只有在真实 Tauri IPC 实现中直接需要时才做。
- 直接进入用户真实会议验证：暂不采用，桌面壳、麦克风采集和 ASR worker 尚未跑通。
- 默认接远程 ASR 加速验证：拒绝作为默认路线，违背“不增加额外收费项”的边界；远程 ASR 只保留为显式质量对照。
- 继续大规模 provider bake-off：拒绝，当前收益递减。

影响范围：

- `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`
- `docs/asr-evaluation-dataset.md`
- `docs/implementation-roadmap.md`
- `docs/project-stage-status-and-next-work-2026-07-02.md`
- `docs/requirements-traceability-matrix.md`
- `code/desktop_tauri`
- `code/asr_runtime`
- `code/web_mvp`

成本/隐私影响：

- 默认仍不增加远程 ASR 费用。
- 公开音频只使用明确授权来源；不抓取不明授权视频、播客或会议录播。
- 真实用户音频只能在用户最终验证阶段由用户确认后使用。
- 原始公开数据、生成音频、真实录音和 runtime chunks 不提交仓库。
- LLM 成本仍只来自 OpenAI-compatible 中转站，并且必须受 scheduler、cooldown、usage 记录和证据输入约束。

验证方式：

- M1: 受控 `cargo check` 产生真实桌面构建证据。
- M2: 真实 Tauri no-op window 打开并加载本地 Web MVP。
- M3: 前端通过 Tauri IPC 调用 native no-op commands。
- M4: 公开授权音频进入本地 ASR 并产生 event JSON。
- M5: 自建中文技术会议脚本和合成音频验证技术实体、gap candidate、非工程 0 卡。
- M6-M8: 麦克风采集、本地 ASR worker、state/gap candidate。
- M9-M10: 受控 LLM 建议卡片和用户真实麦克风会议验证。

复审条件：

- 如果两周内仍不能跑通至少 `真实/模拟音频 -> ASR final/revision -> EvidenceSpan -> state/gap candidate`，必须暂停并重评产品路线。
- 如果真实会议卡片 useful / would-have-asked 低于 40%，或 wrong/too_late/too_intrusive 高于 20-25%，必须降级或停止实时 Copilot 路线。
- 如果本地 ASR 关键技术实体 recall 无法达到 80%，且没有明确到 90% 的路径，必须重新评估 ASR provider 或产品形态。

## DEC-017: 进入 PC Local Web MVP 阶段，再进入桌面壳

- 日期：2026-06-30
- 状态：Accepted

背景：

用户确认先做 PC 端，并授权按推荐路线推进。当前最大产品风险仍是实时 Copilot 价值是否成立，而不是安装包、签名或平台商店。

决策：

下一阶段进入 PC Local Web MVP：

```text
code/core
  -> 平台无关会议快照、证据、状态、建议、报告

code/web_mvp/backend
  -> 本地 API，会话创建、读取、卡片操作、删除

后续再进入：
  Mac desktop shell
  Windows platform adapter
```

PC Local Web MVP 是本机运行的 PC 原型，不是 SaaS，不是最终安装包。

原因：

- 可以最快验证“实时会议状态 + 工程建议卡片 + EvidenceSpan”是否有产品价值。
- 避免过早引入 macOS/Windows 音频权限、安装包、签名、公证、SmartScreen 和自动更新复杂度。
- core 先独立出来，后续 Tauri/Electron/Mac/Windows 都能复用。
- 如果 PC-1 只做成转写和总结，就可以早停，不继续投入桌面壳。

替代方案：

- 直接做 Mac Tauri/Electron 桌面壳：暂不采用，桌面复杂度会掩盖产品价值验证。
- 继续 ASR provider 横评：拒绝，已决定收束 ASR 泛评测。
- 先做完整 Web SaaS：拒绝，不符合本地会议音频和隐私目标。

影响范围：

- `docs/pc-local-web-mvp-requirements.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/pc-local-web-mvp-plan.md`
- `code/core`
- `code/web_mvp/backend`

成本/隐私影响：

- PC-1 不新增远程 ASR 成本。
- 默认远程成本仍只来自 LLM 中转站，并受 scheduler/usage 记录约束。
- 真实录音、API key、模型缓存和运行输出不得提交。

验证方式：

- `cd code/core && pytest -q`
- `cd code/web_mvp/backend && pytest -q`
- `cd code/asr_runtime && pytest -q`
- `cd code/asr_bakeoff && python3 -m pytest tests -q`

复审条件：

- PC Local Web MVP 能证明建议卡片和证据化状态有效后，进入 Mac desktop shell。
- 如果建议卡片没有证据链、非工程会议误触发、或只剩转写总结价值，则停止进入桌面壳并重新评估产品方向。

## DEC-018: 多 Agent 评审后先加固 PC-1 Gate 和 Core Contract

- 日期：2026-06-30
- 状态：Accepted

背景：

用户要求整理完整 checklist，并启动多 Agent 评估遗漏和架构问题。3 个只读 Agent 分别从产品价值、技术架构、SDD/TDD 验收追踪角度审查后，结论一致：方向正确，但 PC-1 验收门槛和 core contract 还不够硬。

决策：

在进入 Mac desktop shell 前，必须先加固：

```text
PC-1 value gates
core schema/contract
state-driven suggestion card trace
realtime card timing gate
degradation gate
traceability gate
```

新增 `AC-PCWEB-009` 到 `AC-PCWEB-020` 作为 PC-1 后续验收铁门。

原因：

- 当前产品定位强，但现有 PC-1 最小骨架仍可能被误实现成“有证据的总结器”。
- 卡片必须来自状态变化和缺口规则，而不能由会后总结 prompt 直接生成。
- 文件回放/伪实时不能被误认为真实桌面实时 ASR 达标。
- 核心领域契约仍分散在 `asr_runtime/scripts`，未来桌面壳可能反向依赖实验脚本。
- RTM 目前是人工可读追踪，还不是强机器 gate。

替代方案：

- 直接进入 Mac desktop shell：拒绝，风险过早放大。
- 只继续写前端工作台：暂缓，前端前需要先定义更硬的 schema/gate。
- 维持现有最低验收门槛：拒绝，无法防止退化成转写总结工具。

影响范围：

- `docs/end-to-end-design-checklist.md`
- `docs/multi-agent-design-review-2026-06-30.md`
- `docs/pc-local-web-mvp-acceptance.md`
- 后续 `code/core` schema/contract。
- 后续 PC Web MVP demo/replay endpoint 和 frontend。

成本/隐私影响：

- 不新增远程 ASR 成本。
- 会增加前期 TDD 和验收 gate 工作量，但降低后续桌面壳返工风险。
- 隐私/密钥/真实录音仍按 existing boundary 管理。

验证方式：

- 多 Agent 评审报告落文档。
- 全链路 checklist 落文档。
- PC-1 验收新增 AC-PCWEB-009..020。
- 后续实现必须为 P0 gate 增加自动测试或明确人工 gate。

复审条件：

- AC-PCWEB-009..020 至少 P0 项通过后，再评估 Web 前端工作台和 Mac desktop shell 进入条件。

## DEC-019: PC-1 先落 core gate 与 demo fixture endpoint

- 日期：2026-06-30
- 状态：Accepted

背景：

PC-1 最小 API 骨架已经存在，但仍有两个风险：一是建议卡片可以退化成 LLM 总结直接生成的“孤立卡片”；二是 Web MVP 没有一键可复现的中文技术会议样本，难以持续验证产品价值。

决策：

先在 `code/core` 落地 PC-1 关键 contract/gate，再在 `code/web_mvp/backend` 增加 demo fixture endpoint：

```text
code/core
  meeting_copilot_core/contracts.py
  meeting_copilot_core/gates.py

code/web_mvp/backend
  /demo/fixtures
  /demo/fixtures/{fixture_id}/sessions

data/web_mvp/fixtures
  api-review.json
  release-review.json
```

正式建议卡片必须携带：

- `evidence_span_ids`
- `state_refs`
- `state_event_ids`
- `gap_rule_id`
- `trigger_reason`
- `final_segment_at_ms`
- `state_event_at_ms`
- `card_created_at_ms`
- `latency_ms`
- `trigger_source`
- `segment_batch`
- `prompt_version`
- `model`
- `usage`
- `schema_result`
- `show_or_silence_decision`

repository 的 create/status update 必须先通过 core gate，再写入内存，避免坏数据污染 session。

原因：

- 这能把产品价值锁定在“状态驱动的实时 Copilot”，而不是“转写 + 总结”。
- demo fixture 可以让后续 Web 前端工作台有稳定样本，不依赖真实录音或远程 ASR 成本。
- core gate 是 Web/Mac/Windows 共用边界，优先级高于桌面壳。

替代方案：

- 直接进入 Web 前端：暂缓，前端前需要有稳定数据契约和 gate。
- 直接进入 Mac desktop shell：拒绝，桌面音频和权限复杂度会掩盖 core 价值风险。
- 只在 fixture endpoint 里做校验：拒绝，容易形成后门，必须复用 core gate。

影响范围：

- `code/core`
- `code/web_mvp/backend`
- `data/web_mvp/fixtures`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/requirements-traceability-matrix.md`

成本/隐私影响：

- 不新增远程 ASR 成本。
- fixture 不包含真实录音路径、API key 或本地用户数据。
- LLM 成本字段只作为 trace/schema，fixture 不实际调用中转站。

验证方式：

- `cd code/core && pytest -q`
- `cd code/web_mvp/backend && pytest -q`
- 发布/交付前继续执行敏感信息扫描。

代码审查后加固：

- `state_refs` 必须指向真实 state item。
- `state_event_ids` 必须指向结构完整的 MeetingStateEvent，且 event target 必须存在。
- 卡片 `segment_batch` 必须指向真实 transcript segment，`final_segment_at_ms` 必须匹配 segment batch 的 finalized/end time。
- 卡片 `state_event_at_ms` 必须匹配引用事件的 `created_at_ms`。
- `schema_result=failed/timeout/invalid` 不得输出强建议。
- `usage.total_tokens` 必须为非负整数。
- repository 必须拒绝未知 card_id 的状态更新。

复审条件：

- 接入真实 scheduler event log、WebSocket/SSE 或桌面音频采集时复审。
- 如果 fixture 通过但真实会议误报率高，需要扩展负例和人工标注 gate。

## DEC-020: PC-1 fixture evaluation 作为最小价值门禁

- 日期：2026-06-30
- 状态：Accepted

背景：

PC-1 已经有 core gate、demo fixture endpoint 和 Web 工作台第一版，但仍可能退化成“能展示转写/摘要/单张建议卡片”的 demo。为了避免继续陷入无限 ASR 测评，同时也避免过早进入桌面壳，PC-1 需要一个轻量、可重复、低成本的价值门禁。

决策：

在 `code/web_mvp/backend` 增加 fixture evaluation summary，并把它接入 `POST /demo/fixtures/{fixture_id}/sessions` 响应和 Web 工作台：

```text
evaluation_summary
  gate_version
  is_engineering_meeting
  state_counts
  suggestion_card_count
  effective_card_count
  gap_rule_ids
  gap_rule_count
  expected_gap_rule_count
  false_positive_count
  too_late_count
  kept_count
  failures
  passes_minimum_gate
```

PC-1 fixture gate 口径：

- 工程会议 fixture 必须至少有 1 个四类核心 state，并至少覆盖 2 个有效 gap rule。
- 非工程/边界 fixture 必须 0 张工程建议卡片。
- 第一批固定 2 个工程正例：`api-review`、`release-review`。
- 第一批固定 3 个负例：`business-sync`、`product-priority`、`mixed-terms-sync`。
- 暂不新增 `compatibility_gap` 到 core 白名单；API 兼容性评审里的兼容性风险先用 `test_verification_gap` 和 `metric_monitoring_gap` 表达。若后续要新增类型，必须同步更新 PRD、core gate、fixture、测试和 UI。

原因：

- 用最小 replay gate 证明产品不是单纯转写工具：正例要能提出多个工程缺口，负例要能保持安静。
- 不新增远程 ASR 或额外收费 provider；fixture evaluation 是本地静态计算。
- 在进入真实桌面音频、持久化和 SSE/WebSocket 前，先固定“什么算有价值”的验收口径。

替代方案：

- 继续扩大 ASR provider bake-off：暂缓，ASR 质量重要但不能替代 Copilot 价值验证。
- 只保留人工评审：拒绝，缺少自动回归会导致后续实现漂移。
- 建立复杂 precision/recall 标注系统：暂缓，PC-1 先用轻量 gate；真实会议评审阶段再补人工标注。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/demo_evaluation.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/*`
- `data/web_mvp/fixtures/*.json`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `code/web_mvp/README.md`

成本/隐私影响：

- 不调用远程 ASR。
- 不调用 LLM 中转站。
- fixture 不包含真实录音路径、API key 或用户本地数据。

验证方式：

- `cd code/web_mvp/backend && pytest -q`
- 全量验证仍需运行 core、web_mvp、asr_runtime、asr_bakeoff 测试。
- 发布/交付前继续执行敏感信息扫描。

复审条件：

- 接入真实 scheduler event log 时，evaluation summary 需要增加 trigger event 覆盖。
- 接入真实会议/用户反馈后，需要把 `kept`、`marked_wrong`、`too_late`、`too_intrusive` 纳入可导出的质量资产。

## DEC-021: PC-1 先用 replay snapshot timeline 固定事件流契约

- 日期：2026-06-30
- 状态：Accepted

背景：

PC-1 文档要求展示 replay/streaming events、MeetingStateEvent、建议卡片触发 trace 和 LLM 用量。但真实 ASR streaming endpoint、真实 scheduler event log、桌面音频采集和 live SSE/WebSocket 仍未完成。如果直接进入 live 事件源，容易把音频/ASR/调度问题和 UI/API 契约问题混在一起。

决策：

先在 Web MVP 中从已通过 core gate 的 session snapshot 生成 replay event timeline，并暴露两个本地接口：

```text
GET /sessions/{session_id}/events
GET /sessions/{session_id}/events.sse
```

事件类型第一版固定为：

- `transcript_final`
- `state_event`
- `llm_scheduled`
- `llm_schema_result`
- `suggestion_card`
- `evaluation_summary`

每个事件必须有：

- `sequence`
- `id`
- `event_type`
- `at_ms`
- `source`
- `trace_kind`
- `payload`

Web 工作台增加事件流面板，用同一 JSON endpoint 展示 timeline。SSE endpoint 只作为格式契约和后续 live event source 的替换点，不宣称已完成真实实时。

原因：

- 保持 PC-1 继续往“会中可审计 Copilot”推进，而不是停留在最终 snapshot。
- 用 gated snapshot 作为事件流来源，可以复用现有 EvidenceSpan、state/card、timing、LLM trace gate。
- 不新增远程服务、不增加 ASR/LLM 费用。
- 为后续真实 scheduler event log、ASR partial/final/revision 和桌面音频输入预留稳定 UI/API 形状。

替代方案：

- 直接接 live SSE/WebSocket：暂缓，真实 ASR/LLM scheduler 还没形成稳定 event source。
- 只保留最终 snapshot：拒绝，会削弱 PC-1 对实时/准实时链路的验证价值。
- 在前端本地拼事件：拒绝，事件流契约应由后端统一生成并测试。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/replay_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/*`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `code/web_mvp/README.md`

成本/隐私影响：

- 不调用远程 ASR。
- 不调用 LLM 中转站。
- 事件 payload 来自本地 gated snapshot，不包含 API key 或真实录音路径。

验证方式：

- `cd code/web_mvp/backend && pytest -q`
- HTTP smoke 检查 `/events` 和 `/events.sse`。
- 浏览器 smoke 检查事件流面板可见且无 console error。

复审条件：

- 接入真实 ASR partial/final/revision 时，新增 `transcript_partial`、`transcript_revision`、`provider_error` 等事件类型。
- 接入真实 scheduler 时，将 replay-derived `llm_scheduled` / `llm_schema_result` 替换或并行为真实 scheduler event log，并新增 `suggestion_silenced` 等事件类型。

## DEC-022: PC-1 replay timeline 先派生 LLM trace events

- 日期：2026-06-30
- 状态：Accepted

背景：

PCWEB-019 要求每次 LLM 分析有触发源、segment batch、prompt version、model、usage、schema result 和 show/silence decision。此前这些字段已经被写入建议卡片并通过 core gate 约束，但 Web 工作台时间线只显示 `suggestion_card`，用户无法看出“什么时候因为哪个状态缺口调度了 LLM”和“结构化结果何时完成”。如果不补这层 trace，PC-1 容易退化成“最终卡片展示器”，不能支撑会中 Copilot 的可审计价值。

决策：

在不接真实 LLM、不调用中转站、不增加额外费用的前提下，从已通过 gate 的 suggestion card trace 字段派生两类 replay events：

- `llm_scheduled`：表示状态缺口进入 LLM 调度。时间取 `state_event_at_ms`，payload 包含 `card_id`、`gap_rule_id`、`trigger_source`、`trigger_reason`、`segment_batch`、`state_event_ids`、`prompt_version`、`model`。
- `llm_schema_result`：表示模型结构化结果和 show/silence 决策完成。时间取 `card_created_at_ms`，payload 包含 `card_id`、`schema_result`、`show_or_silence_decision`、`usage`、`latency_ms`。

每个 replay event 都必须在事件 envelope 中携带：

- `source=replay_snapshot`
- `trace_kind=replay_derived`

事件排序使用显式优先级，避免同一毫秒下因字母序破坏因果关系：

```text
transcript_final -> state_event -> llm_scheduled -> llm_schema_result -> suggestion_silenced -> suggestion_card -> evaluation_summary
```

边界：

- 这些事件是 `replay_snapshot` 派生事件，不是真实 scheduler event log；JSON 和 SSE 消费者都必须能从单个事件看出这个边界。
- 这一步不证明 ASR live capture、真实 scheduler cooldown/budget、远程 LLM latency 或失败重试质量。
- 真实 scheduler 接入后，需要保留同一 API/UI envelope，但事件来源应改为真实 event log 或显式标注 trace source。

原因：

- 补齐 PCWEB-019 的可观察性，让“建议为什么出现”在时间线上可审计。
- 不新增远程 ASR/LLM 调用，符合“尽量只把花费放在中转站且可控”的成本原则。
- 为后续 live SSE/WebSocket 提前固定事件字段和 UI 呈现方式。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/replay_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `code/web_mvp/README.md`

验证方式：

- TDD：先让 `llm_scheduled` / `llm_schema_result` 缺失测试失败，再实现后通过。
- `cd code/web_mvp/backend && pytest -q`
- HTTP smoke 检查 `/events` 和 `/events.sse` 包含两类 LLM trace events。
- 浏览器 smoke 检查事件流面板可见且无 console error。

复审条件：

- 接入真实 schema failure/timeout/invalid 事件后，必须复审 `suggestion_silenced` 与真实 scheduler event log 的来源映射。
- 接入真实 scheduler event log 后，evaluation summary 增加 trace event 覆盖检查，避免“卡片存在但缺少调度/结构化结果事件”。
- 接入 live ASR/LLM event source 后，事件 envelope 需要区分 `trace_kind=replay_derived` 和真实 scheduler trace。

## DEC-023: Schema 失败/超时/非法结果只进入静默降级事件

- 日期：2026-06-30
- 状态：Accepted

背景：

PCWEB-019 已要求 `schema_result=failed/timeout/invalid` 不得输出强建议。此前 core gate 已能阻止这类结果以强建议形式展示，但 Web MVP 只有 valid/show 样本，无法验证产品在模型失败时是否“安静且可审计”。如果失败结果被直接丢弃，后续无法复盘模型质量；如果失败结果被当成普通建议卡展示，会在会中制造噪音。

决策：

PC-1 增加 `schema-degradation-review` fixture，覆盖 1 张 valid/show 有效卡和 3 张 schema blocked 卡：

- `schema_result=failed`
- `schema_result=timeout`
- `schema_result=invalid`

blocked 卡必须满足：

- `show_or_silence_decision=silence`
- 仍保留 evidence、state refs、state events、segment batch、usage 和 timing trace。
- replay timeline 仍生成 `llm_scheduled` 和 `llm_schema_result`。
- 最终展示事件不是 `suggestion_card`，而是 `suggestion_silenced`。
- Web 工作台中以 muted card 展示，不渲染反馈按钮，不计入 effective cards。

evaluation summary 增加：

- `silenced_card_count`
- `schema_blocked_count`
- `schema_result_counts`

原因：

- 保持会中体验：模型失败时不打断会议。
- 保持可复盘性：失败/超时/非法 schema 仍进入时间线和质量计数。
- 不新增远程调用或额外收费，全部来自本地 fixture/replay。

影响范围：

- `data/web_mvp/fixtures/schema-degradation-review.json`
- `code/web_mvp/backend/meeting_copilot_web_mvp/replay_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/demo_evaluation.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/tests/test_demo_evaluation.py`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `code/web_mvp/README.md`

验证方式：

- `schema-degradation-review` fixture 创建 session 必须通过 core gate。
- blocked cards 数量必须为 3，且全部不是 `show`。
- replay JSON/SSE 必须包含 4 条 `llm_schema_result`、3 条 `suggestion_silenced`、1 条 `suggestion_card`。
- Web 工作台必须能展示 muted/silenced 状态，且 schema blocked 卡不出现反馈按钮。

复审条件：

- 接入真实 scheduler event log 后，`suggestion_silenced` 事件来源需要从 replay-derived 切到真实调度事件或明确并行展示。
- 增加真实用户反馈样本后，schema blocked 的比例需要纳入模型质量报告。
- 如果未来允许“会后补问/草稿建议”，需要新增非强展示状态，而不是复用会中强建议卡。

## DEC-024: Web MVP 证据 chip 必须回跳到 EvidenceSpan 和 transcript segment

- 日期：2026-06-30
- 状态：Accepted

背景：

Meeting Copilot 的价值边界要求所有正式状态和建议卡片都 EvidenceSpan-backed。此前 Web MVP 已在状态和卡片上展示 evidence chip，但点击后只高亮 Evidence 面板项，不能同时定位到原始 transcript segment。这样用户仍需要手动找原话，容易把 AI 建议误认为黑盒结论。

决策：

PC-1 Web 工作台中，状态对象和建议卡片的 evidence chip 必须携带：

- `data-evidence-id`
- `data-segment-id`

点击 evidence chip 时必须同时：

- 高亮并滚动到 Evidence 面板中的 `evidence-{id}`。
- 高亮并滚动到 transcript 面板中的 `transcript-segment-{segment_id}`。

边界：

- 本决策只覆盖 replay/gated snapshot UI 的 evidence click-back。
- revision 后 EvidenceSpan 被替换、撤销或失效时如何可视化，仍是后续专项，不因 click-back 已实现而视为完成。
- 这一步不新增远程 ASR/LLM 调用，不增加费用。

原因：

- 让实时建议卡片从“AI 说了什么”变成“AI 为什么这么提醒”，增强会中可信度。
- AC-PCWEB-017 要求主视图不是 transcript-first，但点击状态/卡片必须能定位证据。
- 保持前端轻量，不引入 Node/React/Vite，只在现有静态 JS 中实现。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `code/web_mvp/README.md`

验证方式：

- TDD：先让静态资源测试因缺少 `segmentByEvidenceId`、`data-segment-id`、`focusTranscriptSegment` 和 transcript active 样式失败，再实现。
- `cd code/web_mvp/backend && pytest -q`
- 浏览器 smoke：加载 fixture 后点击建议卡片 evidence chip，验证 `.evidence-item.active` 和 `.segment-item.active` 同时出现且无 console error。

复审条件：

- 接入真实 `revision` 事件后，必须新增 EvidenceSpan 失效/替换 UI 状态，避免用户点击到被修正或撤销的旧证据。
- 引入脚本化浏览器 E2E 后，click-back 需要从手工 smoke 升级为自动化 gate。

## DEC-025: PC-1 先用显式本地 JSON repository 建立删除语义

- 日期：2026-06-30
- 状态：Accepted

背景：

进入 Mac desktop shell 前，PC Web MVP 不能只依赖内存 repository。内存删除只能证明 API record 被移除，不能证明本地会议数据有明确存放位置和删除语义。另一方面，当前阶段还没有真实音频采集、chunk、导出文件和模型缓存生命周期，过早引入 SQLite/迁移系统会扩大依赖和维护成本。

决策：

PC-1 新增 `JsonFileSessionRepository`，作为进入桌面壳前的最小本地持久化层：

- 默认 `create_app()` 仍使用 in-memory repository，便于快速开发和测试。
- 传入 app factory `data_dir` 或设置 `MEETING_COPILOT_DATA_DIR` 时启用 JSON repository。
- session record 写入 `<data_dir>/sessions/{session_id}.json`。
- session/status 可跨 repository 实例读回。
- 写入前必须先通过 `build_session_snapshot` core gate，避免坏数据落盘。
- `DELETE /sessions/{session_id}` 删除对应 JSON 文件，删除后 API 返回 404。
- session id 使用安全字符白名单，禁止路径穿越。
- 默认建议数据目录为 `data/local_runtime/`，并加入 `.gitignore`，避免本地会议数据入仓库。

边界：

- JSON repository 只覆盖 PC-1 的 session snapshot、状态、建议卡片反馈和 fixture metadata。
- 原始音频、临时 audio chunk、导出文件、模型缓存和桌面系统权限数据尚未接入，因此完整会议删除生命周期仍未完成。
- 暂不引入 SQLite、全文索引或迁移系统；这些在长会议、多 session 搜索或桌面多版本升级需要时再复审。

原因：

- 最小代价补齐“本地数据目录 + 删除文件”的关键 gate。
- 不增加远程 ASR/LLM 费用。
- 保持当前 FastAPI 静态 Web MVP 依赖轻量，不引入数据库运行时。
- 为 Mac desktop shell 的数据目录、导出物和缓存生命周期预留清晰边界。

影响范围：

- `.gitignore`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/repository.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `docs/privacy-and-data-flow.md`

验证方式：

- TDD：先让 JSON repository/import/data_dir 测试失败，再实现。
- `cd code/web_mvp/backend && pytest -q`
- HTTP/local smoke：设置 `MEETING_COPILOT_DATA_DIR` 后创建 fixture session，确认 JSON 文件存在；DELETE 后确认文件消失并 GET 返回 404。
- 敏感信息扫描不得命中用户 key、真实录音路径或中转站配置。

复审条件：

- 接入真实音频采集后，必须把 audio/chunk/report/export 文件加入同一会议删除事务。
- 支持 session 列表、搜索、长会议分页或多版本 schema 后，复审是否切换 SQLite。
- 进入打包阶段前，复审 macOS Application Support 路径、权限、清理策略和用户可见删除文案。

## DEC-026: PC Web MVP 使用零 npm 依赖 Chrome/CDP 脚本作为浏览器 E2E gate

- 日期：2026-07-01
- 状态：Accepted

背景：

PC-1 Web 工作台已经有 FastAPI 后端测试和手工浏览器 smoke，但 checklist 仍标记“还没有浏览器端 E2E 验证”。Evidence click-back、schema 降级 muted cards、事件流和 report 都是用户可见行为，仅靠静态资源字符串测试不足以防止 UI 回归。当前项目刻意不引入 Node/React/Vite，继续保持轻量依赖。

决策：

新增 `code/web_mvp/e2e/browser_smoke.mjs`：

- 使用 Node.js 内置能力，不新增 npm package。
- 启动本地 `uvicorn`，并设置临时 `MEETING_COPILOT_DATA_DIR`。
- 启动本机 Google Chrome headless，通过 Chrome DevTools Protocol 驱动真实页面。
- 加载 workbench 默认 `api-review` fixture。
- 验证建议卡 evidence chip `ev_002 -> seg_002` 点击后同时激活 `evidence-ev_002` 和 `transcript-segment-seg_002`。
- 验证 replay timeline 出现 `llm_scheduled`。
- 切换到 `schema-degradation-review` fixture。
- 验证 Gate passed、3 张 muted card、0 个 muted feedback button、3 条 `suggestion_silenced` event，以及 Markdown report 包含 `## Silenced Suggestion Records`。

边界：

- 该脚本是 PC Web MVP 的 UI/E2E smoke，不是截图/视觉回归。
- 它仍基于 replay fixture，不证明真实 ASR/LLM live event source。
- 它依赖本机 Chrome；CI 或无 Chrome 环境需要设置 `CHROME_BIN` 或改用后续 Playwright/浏览器镜像策略。

原因：

- 用最小依赖把关键 UI 行为变成可重复 gate。
- 保持本地开发环境轻量，避免为了单个 smoke 引入 Node 依赖树。
- 为后续 Mac desktop shell 之前的 Web UI 稳定性提供更强证据。

影响范围：

- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

验证方式：

- TDD：先让 README/脚本存在性测试失败，再实现脚本和文档。
- `cd code/web_mvp/backend && pytest -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`
- 运行后不得留下 8767/Chrome remote debugging 端口、临时数据目录或 Python cache。

复审条件：

- 引入 CI 后，需要复审 Chrome 可用性、端口冲突、截图留档和失败产物保存策略。
- 接入真实 live event source 后，新增 E2E 用例覆盖 ASR partial/final/revision 和真实 scheduler trace。

## DEC-027: EvidenceSpan revision 先落最小 lifecycle gate

- 日期：2026-07-01
- 状态：Accepted

背景：

产品要求 `revision` 修改或撤回证据后，相关状态/卡片必须更新、撤销或降级。PC Web MVP 目前还没有真实 live ASR revision event source，但如果 core 仍允许强建议卡引用已经被修正的旧 EvidenceSpan，后续接入 live source 时会把错误证据链带入 UI 和报告。

决策：

先在 core/Web MVP 落地最小 EvidenceSpan lifecycle：

- `EvidenceSpanV1` 保留 `status`，默认 `active`。
- 支持 `status=stale|superseded`。
- 支持 `revision_of` 和 `replaced_by`，用于记录旧证据和修正证据的关系。
- core gate 拒绝强建议卡引用非 `active` evidence。
- 非强审计记录可以引用 stale/superseded evidence，用于保留模型失败、草稿或降级轨迹。
- Web 工作台对 `stale/superseded` evidence item 和 evidence chip 使用 warning 风格标记。

边界：

- 这不是完整 live revision 处理器。
- 当前不会自动从 `revision` event 重算状态、撤销卡片或生成 superseded state event。
- 真实 ASR live source 接入后，必须把 revision event 转为 evidence lifecycle 更新，并触发状态/卡片重算或降级。

原因：

- 先把“旧证据不能支撑强建议”的安全 gate 落到 core，避免 UI/fixture 绕过。
- 保持 PC-1 仍可用 replay fixture 验证，不引入远程 ASR/LLM 调用。
- 给后续 live `transcript_revision` event source 预留明确字段。

影响范围：

- `code/core/meeting_copilot_core/contracts.py`
- `code/core/meeting_copilot_core/gates.py`
- `code/core/tests/test_contracts.py`
- `code/core/tests/test_gates.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

验证方式：

- TDD：先让 lifecycle 字段保留、未知 lifecycle status 拒绝、强卡引用 stale evidence 拒绝的测试失败，再实现。
- `cd code/core && pytest -q`
- `cd code/web_mvp/backend && pytest -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`

复审条件：

- 接入真实 `transcript_revision` event 后，必须新增自动更新或撤销状态/卡片的集成测试。
- 如果后续 report schema 增加 confirmed/rejected/superseded sections，需要把 evidence lifecycle 状态映射到报告条目状态。

## DEC-028: PC Web MVP 建立统一本地质量门禁脚本

- 日期：2026-07-01
- 状态：Accepted

背景：

PC-1 已经有 core pytest、Web backend pytest 和零 npm Chrome/CDP 浏览器 E2E smoke。继续靠人工记多条命令会让后续开发容易漏跑关键门禁，尤其是 EvidenceSpan click-back、schema 降级 UI、JSON 持久化和 replay event stream 这类跨层行为。checklist 中也明确缺少统一 `make test` 或脚本化全量验证入口。

决策：

新增根目录脚本 `tools/run_quality_gate.py`，作为 PC Web MVP 的本地质量门禁入口：

- 默认 profile 为 `pc-web`。
- `pc-web` 顺序运行 `code/core` pytest、`code/web_mvp/backend` pytest 和 `code/web_mvp/e2e/browser_smoke.mjs`。
- `all-local` profile 额外运行 `code/asr_runtime` pytest 和 `code/asr_bakeoff` pytest，用于本地 ASR 工具链回归。
- 支持 `--dry-run` 打印命令而不执行，便于文档、CI 和人工检查。
- 支持 `--no-browser` 临时跳过 Chrome/CDP smoke，用于无 Chrome 环境诊断。
- 任一步骤失败即停止并返回该退出码。

边界：

- 默认不读取 `configs/local/`。
- 默认不调用 LLM 中转站、远程 ASR 或真实 provider smoke，避免本地验证产生额外费用。
- 该脚本是本地验证编排，不替代后续 CI、截图/视觉回归、真实 live event source 验收或桌面安装包 smoke。

原因：

- 用 Python 标准库实现，避免引入 Makefile 可用性差异或 npm 依赖树。
- 把 PC Web MVP 的核心质量证据压缩成一条可复现命令，降低后续开发漏跑 gate 的风险。
- 保持 SDD/TDD 可追溯：新增脚本行为有 `tests/test_quality_gate.py` 覆盖。

替代方案：

- 使用 `make test`：暂不采用，macOS 本机可用但 Windows/CI 体验后续还需评估。
- 使用 npm scripts/Playwright：暂不采用，当前 Web MVP 刻意零 npm 依赖，浏览器 smoke 已用 CDP 覆盖关键路径。
- 只在 README 写多条命令：拒绝，无法形成可重复门禁。

影响范围：

- `tools/run_quality_gate.py`
- `tests/test_quality_gate.py`
- `README.md`
- `code/web_mvp/README.md`
- `docs/project-structure.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`
- `docs/pc-local-web-mvp-acceptance.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取或打印本地 LLM key。
- 浏览器 E2E 使用临时 `MEETING_COPILOT_DATA_DIR`，运行后由 smoke 脚本清理。

验证方式：

- TDD：先让 `tests/test_quality_gate.py` 因缺少脚本失败，再实现。
- `python3 -m pytest tests/test_quality_gate.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web --dry-run`
- `python3 tools/run_quality_gate.py --profile pc-web`
- 运行后检查 8767/9223 没有残留监听，cache 目录清理，敏感信息扫描无命中。

复审条件：

- 引入 CI 后，把该脚本作为 CI 入口或拆成 CI matrix，并复审 Chrome 可用性与失败产物保存。
- 引入真实 live event source 后，扩展 `pc-web` gate 覆盖 live SSE/WebSocket fixture。
- 引入桌面壳后，新增 `mac-desktop` 或 `desktop-smoke` profile，而不是把平台安装包 smoke 混进 PC Web gate。

## DEC-029: PC Web MVP 先落 mock live event source skeleton

- 日期：2026-07-01
- 状态：Accepted

背景：

PC-1 已有 `/sessions/{id}/events` 和 `/events.sse`，但它们明确是 replay snapshot timeline，事件带 `source=replay_snapshot` 和 `trace_kind=replay_derived`。进入桌面壳前，需要给未来真实 ASR/LLM 增量链路一个稳定 API 形状，但现在还没有 macOS 音频采集、真实 ASR endpoint final 或真实 scheduler event log。

决策：

新增 mock live event source skeleton：

- `POST /live/mock/fixtures/{fixture_id}/sessions`：复用现有 gated fixture 创建 session，并返回 `live_events`。
- `GET /live/sessions/{session_id}/events`：返回 mock live JSON event stream。
- `GET /live/sessions/{session_id}/events.sse`：返回同一事件流的 SSE 格式。
- live events 必须标记 `source=live_mock_stream` 和 `trace_kind=live_event`，与 replay timeline 严格区分。
- live envelope 覆盖 `transcript_partial`、`transcript_final`、`transcript_revision` 预留、`state_event`、`scheduler_event`、`llm_schema_result`、`suggestion_card`、`suggestion_silenced`、`provider_error` 预留和 `evaluation_summary`。
- `scheduler_event` 使用 `scheduler_event_type=llm_scheduled`，避免把 replay-derived `llm_scheduled` 事件误认为真实 scheduler log。

边界：

- 这是 mock fixture stream，不是桌面麦克风/系统音频采集。
- 不调用远程 ASR、LLM 中转站或 `configs/local/`。
- 不证明 provider endpoint final、真实 scheduler event log、真实 LLM 增量调用、WebSocket transport 或 live UI subscription。
- 当前前端工作台仍展示 replay timeline；live UI 订阅是后续工作。

原因：

- 先把 live/replay 边界用 API 和测试固定，避免后续把 replay 事件误当真实实时能力。
- 让 Mac desktop shell 和未来 ASR worker 有可对接的事件 envelope。
- 保持 PC-1 低成本和可复现，继续使用 fixture + core gate，不引入真实录音或远程费用。

替代方案：

- 直接把现有 `/events.sse` 改名为 live：拒绝，会混淆 replay 和真实增量语义。
- 立即接真实 ASR/LLM：暂不采用，当前 P0 仍需先稳定 API 契约，且会引入音频权限、模型性能和费用风险。
- 只写文档不落代码：拒绝，不能为后续桌面壳提供可测试接口。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/project-structure.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取真实 API key。
- 不处理真实录音或用户会议数据。

验证方式：

- TDD：先让 `test_live_events.py` 因缺少 live module 失败，再实现。
- `cd code/web_mvp/backend && python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- 敏感信息扫描不得命中用户 key、真实录音路径或中转站配置。

复审条件：

- 接入真实 ASR worker 后，必须把 mock live source 替换或并列为 `live_asr_stream`，并新增真实 partial/final/revision 延迟测试。
- 接入真实 scheduler 后，`scheduler_event` 必须来自 scheduler event log，不再从 card snapshot 派生。
- 前端开始订阅 live stream 后，需要浏览器 E2E 覆盖 live UI 更新和 replay/live 边界切换。

## DEC-030: Web 工作台事件流增加 Replay / Live Mock 切换

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-029 已经给 Web backend 增加 mock live event source skeleton，但工作台仍只展示 replay snapshot timeline。这样前端还没有证明自己能消费 `source=live_mock_stream` / `trace_kind=live_event` 的 live envelope，也不利于后续 Mac desktop shell 或 ASR worker 接入。

决策：

在 Web 工作台事件流面板增加 Replay / Live Mock segmented control：

- Replay 是默认模式，继续读取 `/sessions/{session_id}/events`。
- Live Mock 模式使用当前选中的 fixture 调用 `/live/mock/fixtures/{fixture_id}/sessions`，创建 `live_{fixture_id}_{timestamp}` session。
- Live Mock 模式展示 `/live/sessions/{session_id}/events` 返回的事件，并在面板顶部展示 `live_mock_stream` / `live_event` source marker。
- 前端事件摘要支持 `transcript_partial`、`transcript_revision` 和 `scheduler_event`。
- 浏览器 E2E smoke 必须点击 Live Mock，验证页面出现 `transcript_partial`、`scheduler_event` 和 `live_mock_stream`。

边界：

- 当前只是 fixture-driven mock live UI。
- 不接真实 EventSource 长连接，不接 WebSocket。
- 不调用远程 ASR/LLM 或 `configs/local/`。
- 不证明真实桌面音频采集、真实 ASR endpoint final、真实 scheduler event log 或真实 LLM 增量调用。

原因：

- 让前端提前适配 live envelope，减少后续接 ASR worker 时的 UI 返工。
- 继续保持 replay/live source 边界清楚，避免把 replay-derived 事件误当真实实时能力。
- 用现有零 npm Chrome/CDP smoke 覆盖用户可见路径，不增加前端依赖。

替代方案：

- 只保留后端 live endpoint：拒绝，前端不能证明可消费 live envelope。
- 直接把工作台切到 live-only：拒绝，replay fixture 仍是 PC-1 的稳定价值 gate。
- 立即接真实 SSE/EventSource：暂不采用，真实 ASR/scheduler 还没接入，过早做长连接会扩大不确定性。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取真实 API key。
- 不处理真实录音或用户会议数据。

验证方式：

- TDD：先让静态资源测试和浏览器 smoke 因缺少 Live Mock UI 失败，再实现。
- `cd code/web_mvp/backend && python3 -m pytest tests/test_app.py -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`

复审条件：

- 接入真实 ASR worker 后，Live Mock 控件应扩展为 Replay / Live Mock / Live ASR 或按环境隐藏 mock。
- 接入真实 EventSource/WebSocket 后，浏览器 E2E 必须覆盖流式增量更新，而不只是一次性 JSON 拉取。

## DEC-031: Web 工作台 Live Mock 使用 EventSource 订阅 SSE

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-030 让 Web 工作台可以切换到 Live Mock mode，但前端仍是创建 mock live session 后一次性渲染响应里的 `live_events` JSON。这只能证明 UI 能展示 live envelope，不能证明前端能消费浏览器原生 SSE，也不能暴露有限 SSE 流自动重连导致重复事件的风险。

决策：

Live Mock mode 改为通过浏览器 `EventSource` 订阅 `/live/sessions/{session_id}/events.sse`：

- 创建 mock live session 后，前端调用 `connectLiveEventStream()`，清空本次 live event buffer，并建立 `new EventSource("/live/sessions/{session_id}/events.sse")`。
- 因后端 SSE 使用具名事件，例如 `event: transcript_partial`、`event: scheduler_event`、`event: evaluation_summary`，前端必须通过 `addEventListener(eventType, handler)` 消费 `transcript_partial`、`transcript_final`、`transcript_revision`、`state_event`、`scheduler_event`、`llm_schema_result`、`suggestion_silenced`、`suggestion_card`、`provider_error` 和 `evaluation_summary`。
- SSE `data` 继续是完整 event envelope，而不是只传 payload；前端 append 后复用 `renderEventStream()` 和 `syncEvaluationFromEvents()`。
- 收到 `evaluation_summary` 后主动 `closeLiveEventStream()`，避免有限 mock SSE 被浏览器自动重连并重复渲染。
- 重新加载 fixture、切换回 Replay、创建新的 Live Mock session 或删除 session 时关闭旧 EventSource。
- 浏览器 smoke 注入轻量 EventSource wrapper，验证实际打开 `/live/sessions/.../events.sse`，并验证收到终止事件后连接关闭。

边界：

- 这仍是 fixture-driven mock SSE，不是真实桌面音频采集。
- 不接真实 ASR provider endpoint final、真实 scheduler event log 或真实 LLM 增量调用。
- 不引入 WebSocket、React/Vite/npm 依赖或远程费用。
- 不读取 `configs/local/`，不调用 LLM 中转站或远程 ASR。

原因：

- 先用低成本、可复现的 mock stream 固定前端消费 streaming envelope 的方式，降低后续接 Mac desktop ASR worker 的 UI 风险。
- 使用 EventSource 足够覆盖当前单向事件流；WebSocket 留到需要客户端上行控制、双向会话或更复杂 backpressure 时再评估。
- 对有限 mock SSE 主动关闭连接，可以提前避免自动重连把同一批 fixture 事件重复注入 UI。

替代方案：

- 继续只用 JSON：拒绝，无法验证 SSE 订阅生命周期。
- 立即接 WebSocket：暂不采用，当前需求是服务端单向推送，WebSocket 会扩大协议、测试和资源清理面。
- 立即接真实 ASR/LLM：暂不采用，PC-1 当前仍在稳定 contract/UI/gate，真实 provider 会引入音频权限、ASR 准确率、费用和延迟风险。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取真实 API key。
- 不处理真实录音或用户会议数据。

验证方式：

- TDD：先让静态资源测试因缺少 `connectLiveEventStream`/`EventSource` 字符串失败，让浏览器 smoke 因等不到 `/events.sse` EventSource URL 失败，再实现。
- `cd code/web_mvp/backend && python3 -m pytest tests/test_app.py::test_workbench_static_assets_are_served -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`

复审条件：

- 接入真实 ASR worker 后，必须新增真实 `live_asr_stream` source，并区分 mock fixture stream 和真实 provider stream。
- 真实 scheduler event log 接入后，`scheduler_event` 不能再从卡片 snapshot 派生。
- 如果后续需要暂停/恢复录音、provider 控制、客户端确认 ACK 或复杂错误恢复，再评估是否从 SSE 升级到 WebSocket。

## DEC-032: Live Mock 从预加载结果改为增量 UI 应用

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-031 已经让 Live Mock 使用浏览器 `EventSource` 订阅 SSE，但前端仍在创建 Live Mock session 后先 `renderSnapshot()` 完整展示转写、状态和建议卡，再消费 SSE 事件。这会让页面看起来像实时流，实际上关键结果已经预先加载，不能证明实时会议助手最核心的“随事件逐步出现”的体验。

决策：

Live Mock mode 改为 fixture-backed incremental UI：

- 创建 mock live session 后，完整 snapshot 只保留为 `snapshotLookup`，用于从 event payload 的 `segment_id`、`target_id`、`card_id` 找到完整展示对象。
- 当前 live view 使用 `createLiveIncrementalSnapshot()` 初始化为空：transcript segments/evidence、state lanes、suggestion cards、state events 从空集合开始，evaluation summary 为空。
- 收到 `transcript_partial` 时只显示 partial segment 预览，不生成 EvidenceSpan。
- 收到 `transcript_final` 或 `transcript_revision` 时，把对应 segment 和 EvidenceSpan 加入当前 live view，并刷新 transcript/evidence。
- 收到 `state_event` 时，从 lookup 找到对应状态对象，加入 state board，并更新质量指标里的 state event count。
- 收到 `suggestion_card` 或 `suggestion_silenced` 时，从 lookup 找到对应卡片，加入 suggestion cards，并更新质量指标里的 card count。
- 收到 `evaluation_summary` 后刷新 evaluation summary、加载完整 Markdown report，并继续按 DEC-031 关闭有限 mock SSE。
- Live Mock 打开时必须清空旧 report，避免把 Replay report 或完整 Live report 提前展示成假实时结果。
- Live Mock 中的卡片反馈只更新已展示卡片的状态和 report，不得把 repository 返回的完整 snapshot 重新渲染回 UI，也不得把事件面板替换成 JSON replay/live 拉取结果。
- 浏览器 E2E 必须验证 `EventSource` 打开瞬间没有预加载完整卡片、状态、转写、证据或旧 report；随后等 live events 到达，再验证 transcript/state/cards 出现，并验证 live feedback 不会 full snapshot rehydrate。

边界：

- 这仍是 fixture-backed incremental UI。完整 snapshot 仍存在于前端内存中，只能作为 mock lookup，不代表真实 ASR/LLM 事件已可自包含地驱动 UI。
- 不接真实 ASR provider endpoint final、真实 scheduler event log 或真实 LLM 增量调用。
- 不改变后端 live event payload；后续真实 `live_asr_stream` / scheduler / LLM source 必须提供足够 payload 或配套 lookup API。
- 不引入 React/Vite/npm、WebSocket、远程 ASR 或 LLM 中转站调用。

原因：

- 防止 PC Web demo 退化成“完整结果预加载 + 事件面板装饰”，这会偏离用户最关心的实时会议价值。
- 让前端先具备 event-driven UI 的状态边界，为后续真实桌面音频/ASR worker 接入降低返工。
- 保持成本为零，继续使用 gated fixture 验证交互链路。

替代方案：

- 继续预加载完整 snapshot：拒绝，会掩盖实时 UI 风险。
- 立即把 live event payload 扩成完整状态/card 对象：暂不采用，当前 mock event skeleton 还在固定 envelope，过早扩协议会影响真实 provider 设计。
- 立即接真实 ASR/LLM 自包含事件源：暂不采用，音频权限、ASR 质量、scheduler log 和费用控制仍需分步落地。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取真实 API key。
- 不处理真实录音或用户会议数据。

验证方式：

- TDD：先让浏览器 smoke 因 `expected no preloaded live cards, got 2` 失败，再实现增量 live view；再按 review 反馈补 report 和 live feedback 旁路回归断言。
- `cd code/web_mvp/backend && python3 -m pytest tests/test_app.py::test_workbench_static_assets_are_served -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`

复审条件：

- 接入真实 ASR worker 后，`transcript_partial/final/revision` 应直接来自真实 provider stream，不能依赖 fixture lookup。
- 接入真实 state engine / scheduler / LLM 后，`state_event` 和 `suggestion_card` payload 必须能独立或通过明确 API 还原 UI 所需字段。
- 后续要增加 revision 后撤销/降级旧证据、状态和建议卡的增量 UI 回归测试。

## DEC-033: Live event payload 增加自包含状态和卡片展示字段

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-032 让 Live Mock UI 从空 live view 开始按事件增量更新，但前端仍依赖 `snapshotLookup` 才能把 `state_event.target_id` 还原成状态正文，把 `suggestion_card.card_id` 还原成可展示卡片。这样虽然比预加载完整 UI 更接近实时体验，但事件本身还不够接近未来真实 state engine / LLM source。

决策：

增强 mock live event envelope 的自包含程度：

- `state_event.payload` 增加 `state_item`，包含该状态对象的完整展示字段，例如 `statement`、`description`、`question`、`owner` 和 `evidence_span_ids`。
- 如果 `state_event` 的 `target_type` / `target_id` 无法在 session states 中找到对应状态对象，mock live event 构造必须 fail fast，不能发出空 `state_item`。
- `suggestion_card.payload` 和 `suggestion_silenced.payload` 增加完整 `card` 对象，包含 title、suggested_question、state_refs、state_event_ids、trace/cost/schema/status 等 UI 和反馈所需字段。
- 前端 `applyStateEvent()` 优先使用 `payload.state_item`，`snapshotLookup` 只作为兼容 fallback。
- 前端 `applySuggestionEvent()` 优先使用 `payload.card`，并把 payload card 写回 `snapshotLookup.cards`，方便后续 card feedback 更新。
- 浏览器 smoke 继续验证 Live Mock 增量 UI、payload card 优先级和 feedback 不回灌完整 snapshot；后端单元测试验证 payload 自包含字段和坏 state target fail-fast。

边界：

- 本决策只让 state/card live payload 更自包含，不代表真实 ASR、真实 scheduler log 或真实 LLM card generation 已接入。
- `transcript_final` 当前仍只携带 segment 字段；EvidenceSpan 仍来自 gated session context。真实 ASR/stabilizer 接入时，需要决定 final event 直接携带 EvidenceSpan，还是由本地 transcript stabilizer 生成。
- Markdown report 仍来自 session report endpoint，并在 `evaluation_summary` 后加载；不是逐段 report 流。
- 不新增远程费用，不读取 `configs/local/`，不调用远程 ASR/LLM。

原因：

- 减少前端对完整 fixture snapshot lookup 的依赖，让协议更像真实增量源。
- 为后续真实 state engine 和 LLM suggestion source 明确最小 payload 目标。
- 保持变化聚焦，不提前设计完整 provider/scheduler/LLM 事件协议。

替代方案：

- 继续完全依赖 snapshot lookup：拒绝，会让 Live Mock 难以暴露真实 source payload 缺口。
- 一次性把所有事件扩成完整 snapshot delta：暂不采用，会过早扩大协议面。
- 立即接真实 ASR/LLM source：暂不采用，仍需先稳定 event contract、UI applier 和本地质量门禁。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取真实 API key。
- 不处理真实录音或用户会议数据。

验证方式：

- TDD：先让 `test_live_events.py` 因缺少 `payload.state_item` 和 `payload.card` 失败，让静态资产测试因前端没有 `payload.state_item` / `payload.card` 优先路径失败，再实现。
- `cd code/web_mvp/backend && python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`

复审条件：

- 接入真实 ASR/stabilizer 后，必须补 `transcript_final` / `transcript_revision` 与 EvidenceSpan 的自包含或生成策略。
- 接入真实 scheduler log 后，`scheduler_event` 必须来自真实 scheduler，而不是从 card snapshot 派生。
- 接入真实 LLM 后，`suggestion_card.payload.card` 必须由 LLM schema/gate 产物生成，并保留 usage、schema_result、show/silence decision。

## DEC-034: Transcript final/revision live payload 携带 EvidenceSpan

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-033 让 state/card live payload 更自包含，但 `transcript_final` / `transcript_revision` 仍只携带 segment 字段。前端需要从 `snapshotLookup.evidenceSpans` 过滤对应 segment 才能渲染证据面板。这会继续把证据链绑定在完整 fixture/session lookup 上，不利于后续真实 ASR stabilizer 输出 final segment 后直接驱动 EvidenceSpan。

决策：

增强 transcript final/revision envelope：

- `transcript_final.payload` 和 `transcript_revision.payload` 增加 `evidence_spans`，包含该 segment 对应的 EvidenceSpan 列表。
- `transcript_partial` 仍不得携带或生成正式 EvidenceSpan，只作为预览。
- 前端 `applyTranscriptFinal()` 优先使用 `payload.evidence_spans` 渲染 Evidence 面板；`snapshotLookup.evidenceSpans` 只作为兼容 fallback。
- 浏览器 smoke 在测试中改写 final event 的 EvidenceSpan quote，验证 Evidence 面板来自 event payload，而不是 fixture lookup。

边界：

- 这仍是 mock/session 生成的 EvidenceSpan，不代表真实 ASR provider endpoint final 已能直接输出 EvidenceSpan。
- 真实 ASR/stabilizer 接入时，可以选择由 provider final event 直接带 EvidenceSpan，或由本地 stabilizer 根据 final segment 生成 EvidenceSpan；但进入 UI 前必须满足同样的 payload-first 证据契约。
- 本决策不处理 revision 后 stale/superseded evidence 自动撤销，这仍是后续增量 UI 回归项。
- 不新增远程费用，不读取 `configs/local/`，不调用远程 ASR/LLM。

原因：

- EvidenceSpan 是产品价值链路的核心，不能长期依赖完整 snapshot lookup 才能显示。
- 让 transcript、state、card 三类 live event 都具备最小 UI 自包含能力。
- 为真实 ASR stabilizer 接入建立清晰的 final/revision 事件目标。

替代方案：

- 继续让前端从 snapshot lookup 查 EvidenceSpan：拒绝，会掩盖真实 provider/stabilizer payload 缺口。
- 让 partial 也生成 EvidenceSpan：拒绝，违反 partial 不生成正式证据的边界。
- 立即实现完整 revision lifecycle：暂不采用，当前先完成 final/revision 自包含证据，再专项处理撤销/降级。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

成本/隐私影响：

- 不新增远程费用。
- 不读取真实 API key。
- 不处理真实录音或用户会议数据。

验证方式：

- TDD：先让 `test_live_events.py` 因缺少 `payload.evidence_spans` 失败，让静态资产测试因前端没有 `payload.evidence_spans` 优先路径失败，让 browser smoke 因 Evidence 面板未显示 payload-only quote 失败，再实现。
- `cd code/web_mvp/backend && python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `cd code/web_mvp && node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`

复审条件：

- 接入真实 ASR/stabilizer 后，必须证明真实 final/revision 能产生 EvidenceSpan，并且 partial 不会进入正式 evidence。
- 实现 revision 自动撤销/降级时，需要测试旧 EvidenceSpan `stale/superseded` 状态如何增量更新 UI 和建议卡。

## DEC-035: Live Mock revision 增量更新 EvidenceSpan lifecycle 并降级 stale 卡片

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-034 已让 `transcript_final` / `transcript_revision` 带自包含 `evidence_spans`，但 revision 到达后旧 EvidenceSpan 仍停留在 active UI 状态；引用旧 evidence 的建议卡也仍可操作。这会让用户在实时会议中继续基于已被 ASR/stabilizer 修正的证据追问，违背 `AC-PCWEB-015` 的 revision 更新/撤销/降级要求。

决策：

在 Mock Live envelope 和 Web 工作台增量 applier 中落一个最小 revision lifecycle：

- `transcript_revision.payload` 增加 `supersedes_segment_id`。
- `transcript_revision.payload.superseded_evidence_spans` 携带被替换旧 EvidenceSpan，并把其 `status` 标为 `superseded`，同时写入 `replaced_by`。
- revision 新 EvidenceSpan 保留 `status=active`，并保留 `revision_of` 指向旧 EvidenceSpan；旧 EvidenceSpan 使用 `replaced_by` 指向新 EvidenceSpan，方便 UI 展示修订关系且避免 lineage 方向歧义。
- 前端收到 revision 后，先 upsert 旧 superseded EvidenceSpan，再 upsert 新 EvidenceSpan，刷新 Evidence 面板和 transcript。
- Evidence 面板展示 `status`、`revision_of` 和 `replaced_by`。
- 建议卡渲染时，如果引用的任意 EvidenceSpan 为 `stale` / `superseded`，或引用的 EvidenceSpan 尚未到达当前 live view，卡片降级为 muted，不再显示反馈按钮，并展示 `evidence: stale ...` 或 `evidence: missing ...` 原因。

边界：

- 这是 Mock Live / UI 层的最小降级，不是完整 revision state engine。
- 当前不自动重算 MeetingState，不重新调用 LLM，不生成新的替代建议卡，也不把 muted 状态持久写回 repository。
- 当前 browser smoke 覆盖的是 `api-review` fixture 通过真实 Mock Live SSE 发出的 `transcript_revision`，仍属于 fixture/mock stream，不是桌面真实 ASR source。
- 当前不声称真实 ASR provider/stabilizer 已经可以产生 revision EvidenceSpan；真实来源接入后必须复用或适配同样的 lifecycle payload。
- 不调用远程 ASR/LLM，不读取 `configs/local/`，不增加除用户明确配置 LLM 中转站外的收费项。

TDD 证据：

- 先新增 `test_build_mock_live_events_transcript_revision_supersedes_replaced_evidence`，观察到失败：`KeyError: 'supersedes_segment_id'`。
- 实现后端 payload 生成后，该测试通过。
- 先在浏览器 smoke 注入 synthetic `transcript_revision`，观察到失败：旧 EvidenceSpan class 仍为 `evidence-item`，随后实现前端 superseded evidence upsert 和 lineage 展示。
- 再把 synthetic revision 改成影响现有建议卡证据，观察到失败：引用 superseded evidence 的卡片仍为 `suggestion-card`，随后实现 stale evidence card muting。
- 复审后发现 synthetic 不能证明真实 SSE revision 和“卡片先出现、revision 后到达”的时序；改为让 `api-review` fixture 通过真实 Mock Live SSE 发出 `transcript_revision`，并补 core contract 测试确保 `segment.revision_of` 不被 repository gate 丢弃。
- 复审后修正 lineage 方向：active 新 EvidenceSpan 不再携带 `replaced_by`，只携带 `revision_of`；旧 superseded EvidenceSpan 使用 `replaced_by` 指向新 evidence。
- 二次复审后补 malformed lineage fail-fast：revision segment 必须有 replacement evidence；replacement evidence 的 `revision_of` 必须指向被 `supersedes_segment_id` 覆盖的旧 segment evidence，否则后端拒绝生成 mock live revision event。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/requirements-traceability-matrix.md`
- `docs/end-to-end-design-checklist.md`

替代方案：

- 删除旧 EvidenceSpan：拒绝，破坏审计轨迹和 click-back。
- 让旧卡片继续可操作但 chip 标黄：拒绝，用户仍可能基于过期证据追问。
- 立即实现完整状态重算和 LLM 重跑：暂不采用，范围会扩大到 scheduler/LLM/provider，当前先把 UI 层的安全降级闭环。

复审条件：

- 接入真实 ASR/stabilizer revision 后，必须证明真实 event 能产生 `superseded_evidence_spans` 或由本地 stabilizer 补齐。
- 接入真实 state engine 后，revision 应触发状态重算、相关卡片撤销/替换或生成新的建议卡，而不只是在 UI 层 muted。

## DEC-036: Live Mock revision 生成建议卡失效审计事件

- 日期：2026-07-01
- 状态：Accepted

背景：

DEC-035 让 Live Mock `transcript_revision` 可以更新 EvidenceSpan lifecycle，并让前端把引用 stale/superseded evidence 的卡片降级为 muted。但如果卡片失效只存在于前端渲染推断里，事件流本身缺少“为什么这张卡失效”的审计记录；后续真实 state engine / LLM 接入时，也缺少一个稳定的撤销/替换事件形状。

决策：

在 Mock Live event stream 中新增 `suggestion_invalidated`：

- 当 `transcript_revision` 使旧 EvidenceSpan 变为 superseded，且已有建议卡引用该旧 evidence 时，生成 `suggestion_invalidated`。
- 事件 payload 包含 `card_id`、`reason=stale_evidence`、`invalidated_by_event_id`、`stale_evidence_span_ids`、`replacement_evidence_span_ids` 和降级后的 `card`。
- 降级后的 `card.show_or_silence_decision` 为 `silence`，并保留原始 schema/cost/trace 字段，同时增加 `invalidation_reason`、`invalidated_by_event_id`、旧/新 evidence id 列表。
- 前端把 `suggestion_invalidated` 作为 suggestion event 消费，优先使用 payload card 更新当前 live view。
- 事件排序上，`suggestion_invalidated` 位于 `llm_schema_result` 后、`suggestion_card/suggestion_silenced` 前；如果 revision 发生在原始卡片之后，前端仍会再次渲染并降级既有卡片。
- 复审后补充两条稳定性约束：第一，只有已经发生的 invalidation 可以影响后续 card display，未来 revision 不得提前把历史 card display 改成 silenced；第二，多 EvidenceSpan revision 只返回该卡实际 stale evidence 对应的 replacement ids，不把同一 revision segment 的无关 replacement evidence 写进该卡审计 payload。
- 前端收到 `suggestion_invalidated.payload.card` 后允许该 payload 更新 invalidation lifecycle；后续同 card 的普通 suggestion payload 不能把 `show_or_silence_decision=silence`、`invalidation_reason` 和 `invalidated_by_event_id` 回滚。

边界：

- 这仍是 fixture-backed Mock Live 审计事件，不代表真实 state engine 已重算状态、真实 LLM 已重新生成替代卡，也不代表 repository 已持久撤销旧卡。
- 真实实现接入时，`suggestion_invalidated` 可以继续作为撤销/失效事件，或被更完整的 `state_event/suggestion_replaced` 生命周期替代，但必须保持旧卡为什么不可操作可追溯。
- 不调用远程 ASR/LLM，不读取 `configs/local/`，不增加额外收费项。

TDD 证据：

- 先新增 `test_build_mock_live_events_emits_suggestion_invalidated_after_revision`，观察到失败：事件流中没有 `suggestion_invalidated`。
- 实现后端 invalidation 派生后，`tests/test_live_events.py` 通过。
- 再补 API/SSE 和静态前端测试，先观察到前端资源缺少 `suggestion_invalidated` 字符串，再实现事件类型、applier 和摘要。
- 浏览器 smoke 现在等待真实 Mock Live SSE 的 `.event-item.suggestion_invalidated`，并验证事件摘要含受影响 card 和 `stale_evidence`。
- 复审后新增 `test_build_mock_live_events_downgrades_later_card_display_after_invalidation`，先观察到失败：revision 早于 card 时后续 display 仍是 `suggestion_card`；实现后，已发生 invalidation 会把后续 display 降级为 `suggestion_silenced`。
- 复审后新增 `test_build_mock_live_events_limits_replacement_ids_to_impacted_card_evidence`，先观察到失败：多 EvidenceSpan revision 会把无关 replacement id 写入 card invalidation；实现后，只返回 impacted stale evidence 对应的新 evidence ids。
- 浏览器 smoke 复审后对 `suggestion_invalidated.payload.card.invalidation_reason` 注入 payload-only 值，先观察到失败：UI 只靠 stale evidence 推断 muted；实现生命周期 merge 和 invalidation reason 展示后通过，证明前端确实消费 invalidation payload card。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`

替代方案：

- 继续只由前端渲染时推断 stale card：拒绝，事件流不可审计。
- 立即实现完整状态重算和 LLM 替代卡生成：暂不采用，范围会扩展到真实 scheduler/LLM/provider；当前先固定审计事件形状。

复审条件：

- 接入真实 state engine 后，需要明确 `suggestion_invalidated` 是否持久化为 repository card lifecycle，还是转成更完整的 `suggestion_replaced` / `state_superseded` 事件。
- 接入真实 LLM 后，替代建议卡必须引用新的 active EvidenceSpan，不能继续引用 stale/superseded evidence。

## DEC-037: 先建立本地 live_asr_stream 骨架再进入桌面音频采集

- 日期：2026-07-01
- 状态：Accepted

背景：

PCWEB-029 到 PCWEB-036 已经证明 Web 工作台能消费 fixture-backed Live Mock SSE，并能处理 transcript final/revision、EvidenceSpan lifecycle、state/card payload 和 `suggestion_invalidated`。但这些仍然来自 gated fixture snapshot，无法证明未来 ASR worker 的 streaming event contract 能进入 Web/API/UI。直接进入 Mac 麦克风/系统音频采集会同时引入权限、音频设备、模型依赖、延迟和质量问题，容易把接口边界和平台问题混在一起。

决策：

新增 `PCWEB-037` 本地 `live_asr_stream` skeleton：

- 新增 `meeting_copilot_web_mvp.asr_live_events`，从本地 ASR streaming event JSON 生成 Web live envelope。
- API 新增 `/live/asr/mock/sessions`，只接受 JSON streaming events，不读取音频文件；`/live/asr/sessions/{session_id}/events(.sse)` 输出 `source=live_asr_stream`、`trace_kind=live_event`。
- 前端新增 Live ASR 模式，通过 `EventSource` 订阅 `/live/asr/sessions/{session_id}/events.sse`，从空 live view 增量展示 `partial/final/revision` transcript 和 EvidenceSpan。
- `partial` 不生成 EvidenceSpan；`final` 生成 active EvidenceSpan；`revision` 生成新 active EvidenceSpan，并把旧 EvidenceSpan 标记为 `superseded`，复用已有 revision lifecycle UI。
- 终止事件用 `evaluation_summary` 表示有限本地 stream 已结束，前端据此关闭 EventSource。

边界：

- 这是 synthetic/local ASR event source skeleton，不是桌面实时音频采集。
- 不加载 FunASR/sherpa 模型，不读取用户真实录音，不调用远程 ASR，不调用 LLM 中转站，不读取 `configs/local/`。
- 当前 Live ASR skeleton 不生成 meeting state、scheduler event、suggestion card 或报告；它只验证 ASR event -> Web live envelope -> UI 增量渲染。
- `live_asr_stream` 和 `live_mock_stream` 必须保持 source 边界，避免把 fixture 演示误判为 ASR worker 输入。

TDD 证据：

- 先新增 `test_build_asr_live_events_maps_streaming_contract_to_live_envelope`，观察到失败：`meeting_copilot_web_mvp.asr_live_events` 模块不存在。
- 实现 `build_asr_live_events` 和 `asr_event_source_metadata` 后，该测试通过，证明 `partial/final/revision/error/end_of_stream` 到 live envelope 的最小映射和 source 边界。
- 再新增 `test_create_asr_live_session_events_json_and_sse_use_asr_boundary`，观察到失败：`/live/asr/mock/sessions` 返回 404。
- 实现 API in-memory ASR live session store 和 JSON/SSE routes 后，该测试通过。
- 静态资源测试先观察到 Live ASR 按钮和前端函数缺失；实现 `event-mode-live-asr`、`loadLiveAsrSession` 和 `/live/asr/sessions/.../events.sse` 后通过。
- 浏览器 smoke 新增 Live ASR 段，验证真实浏览器 `EventSource` 订阅 `/live/asr/sessions/{session_id}/events.sse`，从空 view 增量展示 transcript/evidence，显示 `live_asr_stream` source，并在 terminal summary 后关闭有限 stream。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/pcweb-037-live-asr-stream-plan.md`

替代方案：

- 继续只完善 Live Mock：拒绝，无法推进 ASR worker 接入口风险。
- 直接接 Mac 音频采集：暂不采用，平台权限和音频设备问题会掩盖 Web/API event contract。
- 直接跑 FunASR/sherpa 模型：暂不采用，模型依赖、性能和质量评估属于下一阶段；当前先固定 live ASR source envelope。

复审条件：

- 接入真实 ASR worker 后，必须把 synthetic JSON source 替换或并列为真实 provider source，并新增 partial/final/revision 延迟和质量验收。
- 接入真实 scheduler 后，Live ASR final/revision 应触发 state engine/scheduler，而不是只更新 transcript/evidence。
- 接入桌面音频采集后，必须重新审查隐私告知、音频 chunk 生命周期、删除语义和模型缓存策略。

## DEC-038: Live ASR 先接本地状态/调度骨架，不直接生成建议卡

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-037` 证明了 synthetic ASR streaming event 可以通过 `live_asr_stream` 进入 Web/API/UI，并从空 live view 增量展示 transcript 和 EvidenceSpan。但如果下一步仍停留在 transcript/evidence，产品会继续像实时转写工具，而不是会议 Copilot。另一方面，直接接真实 state engine、scheduler 和 LLM 会同时引入 schema、成本、延迟、幻觉和降级问题，容易把核心链路和模型质量问题混在一起。

决策：

新增 `PCWEB-038` Live ASR 本地状态/调度骨架：

- 当 `live_asr_stream` 的 `final` 或 `revision` 文本包含 `灰度` 时，本地确定性生成一个 `DecisionCandidate`。
- 生成的 `DecisionCandidate` 必须引用当前 active EvidenceSpan，并以 `state_event.payload.state_item` 自包含传给前端。
- 同步生成 `scheduler_event`，`scheduler_event_type=state_gap_detected`。
- scheduler payload 必须明确 `prompt_version=not-called`、`model=not-called`，表示没有调用 LLM。
- 仍保持 `source=live_asr_stream`、`trace_kind=live_event`。
- 不生成 `llm_schema_result`、`suggestion_card`、`suggestion_silenced` 或报告。

原因：

- 用最小免费本地逻辑证明 Live ASR 已能驱动“证据 -> 状态 -> 调度 trace -> UI”的价值链路。
- 避免为了演示实时价值而提前调用 LLM 中转站，控制成本和隐私边界。
- 避免把一个字符串启发式误判为正式 state engine。
- 前端已经具备 `state_event` 和 `scheduler_event` 消费能力，本轮改动可以保持窄范围。

边界：

- 这是本地确定性 skeleton，不是正式会议状态抽取。
- 不读取真实音频，不加载 FunASR/sherpa，不调用远程 ASR，不调用 LLM，不读取 `configs/local/`。
- 不生成建议卡，不做 schema 校验，不生成报告。
- `灰度` 只是测试信号，后续必须由真实 state engine 替换。

TDD 证据：

- 先新增 `test_build_asr_live_events_emits_local_state_and_scheduler_skeleton`，观察到失败：Live ASR event list 缺少 `state_event/scheduler_event`。
- 实现 `asr_live_events.py` 中的本地状态/调度骨架后，`tests/test_live_events.py` 通过，覆盖 final/revision 对 EvidenceSpan 的引用、no-LLM scheduler payload 和不生成建议卡/LLM schema 结果。
- 更新 `test_create_asr_live_session_events_json_and_sse_use_asr_boundary`，验证 JSON/SSE 都输出 `state_event`、`scheduler_event` 和 `not-called`。
- 更新浏览器 smoke，验证 Live ASR 模式从空 view 增量显示 transcript/evidence/state，事件流显示 scheduler placeholder 和 `not-called`，建议卡数量仍为 0，报告仍为空。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/pcweb-038-live-asr-state-scheduler-plan.md`

替代方案：

- 继续只展示 transcript/evidence：拒绝，无法证明产品价值链路，容易退化成转写工具。
- 直接接 LLM 生成建议卡：暂不采用，会引入成本、schema、延迟和安全边界，不适合当前骨架阶段。
- 直接接完整 state engine：暂不采用，真实状态抽取需要更完整的 contract、样本和降级策略。

验证方式：

- `python3 -m pytest tests/test_live_events.py -q`
- `python3 -m pytest tests/test_app.py::test_create_asr_live_session_events_json_and_sse_use_asr_boundary tests/test_app.py::test_workbench_static_assets_are_served -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 state engine 后，替换 `灰度` 字符串启发式，并支持 DecisionCandidate、ActionItem、Risk、OpenQuestion 多类状态。
- 接入真实 scheduler 后，`scheduler_event` 必须来自 scheduler event log。
- 接入 LLM 后，才允许生成 `llm_schema_result` 和 `suggestion_card`，并必须满足 EvidenceSpan、schema、成本、实时窗口和降级 gate。

## DEC-039: Live ASR scheduler 先输出本地 decision log，不调用 LLM

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-038` 让 Live ASR `final/revision` 产生本地状态候选和 no-LLM scheduler trace，但 scheduler payload 仍接近单一占位，不能表达真实调度器必须记录的 queued/skipped、cooldown、预算和 no-call 状态。直接接 LLM 会引入成本、延迟、schema、幻觉和隐私边界；直接实现完整真实 scheduler 又会扩大到持久化、重试和 LLM gateway。

决策：

新增 `PCWEB-039` Live ASR 本地 scheduler decision log：

- Live ASR 本地状态候选进入 scheduler decision。
- scheduler event 继续使用 `event_type=scheduler_event`，但 `payload.scheduler_event_type` 扩展为：
  - `llm_candidate_queued`
  - `llm_candidate_skipped`
- scheduler payload 必须携带：
  - `decision_reason`
  - `would_call_llm`
  - `llm_call_status=not_called`
  - `cooldown_remaining_ms`
  - `call_count_last_hour`
  - `budget_remaining`
  - `source_event_ids`
  - `segment_batch`
  - `prompt_version=not-called`
  - `model=not-called`
- 默认复用增量调度语义：状态变化使用 10 秒 cooldown，普通 final 使用 30 秒 cooldown，小时预算为 80。
- 当前 synthetic Live ASR 样本中，首个状态变化进入 `llm_candidate_queued`，紧邻 revision 因 cooldown 进入 `llm_candidate_skipped`。

原因：

- 让前端和 API 先具备真实 scheduler event log 的最小审计形状。
- 明确“候选进入队列”和“由于 cooldown/预算跳过”的差异，避免未来每个 final/revision 都误调用 LLM。
- 继续保持默认免费，不调用中转站。
- 为后续真实 scheduler/LLM 接入提供稳定字段，而不把当前本地 builder 误称为真实 scheduler。

边界：

- 不调用 LLM。
- 不生成建议卡。
- 不生成 `llm_schema_result`。
- 不持久化 scheduler log。
- 不读取真实音频、不加载模型、不调用远程 ASR、不读取 `configs/local/`。
- 这是本地 scheduler audit contract，不是生产 scheduler。

TDD 证据：

- 先更新 `test_build_asr_live_events_emits_scheduler_decision_log_for_state_candidates`，观察到失败：旧 payload 仍是 `state_gap_detected`。
- 实现本地 scheduler state/decision helper 后，`tests/test_live_events.py` 通过，覆盖 queued、skipped、cooldown、预算和 no-call 字段。
- 更新 API/SSE 测试，验证 JSON/SSE 都包含 `llm_candidate_queued`、`llm_candidate_skipped`、`not_called`。
- 更新浏览器 smoke 和前端 `eventSummary`，验证 timeline 显示 queued/skipped/cooldown/not_called，同时仍无 LLM schema、建议卡、silenced event 或 report 请求。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pcweb-039-live-asr-scheduler-log-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

替代方案：

- 保持 `state_gap_detected` 单一占位：拒绝，无法表达成本/冷却/预算边界。
- 直接接 LLM：暂不采用，会引入费用和模型质量风险。
- 直接接完整真实 scheduler：暂不采用，当前先固定 Web/API/UI 的 scheduler audit contract。

验证方式：

- `python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 scheduler 后，scheduler event 必须由真实 scheduler event log 产生，并明确持久化和回放策略。
- 接入真实 LLM 后，只有 queued event 才能进一步触发 `llm_schema_result` 和建议卡；skipped event 不得生成强建议。
- 引入用户反馈质量资产后，cooldown、budget、too_late、too_intrusive 需要进入评估指标。

## DEC-040: Live ASR 状态抽取先覆盖决策和未闭环问题两类本地契约

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-038` 和 `PCWEB-039` 已证明 Live ASR 的 `final/revision` 可以生成 evidence-backed `DecisionCandidate` 和 no-LLM scheduler decision log。但状态抽取仍只识别 `灰度`，产品价值容易再次收窄成“转写里出现某个词就生成一个决策”。真实会议中，实时 Copilot 的关键价值之一是当场捕捉“谁负责、是否确认、怎么回滚、有没有遗漏”等未闭环问题，并进入后续调度和建议链路。

决策：

新增 `PCWEB-040` Live ASR 本地状态抽取契约：

- `final/revision` 包含 `灰度` 时继续生成 `DecisionCandidate`。
- `final/revision` 包含问题标记时生成 `OpenQuestion`。
- 当前本地问题标记为：
  - `谁`
  - `吗`
  - `怎么`
  - `是否`
  - `有没有`
  - `还没有确认`
  - `?`
  - `？`
- 每个状态事件必须携带自包含 `state_item`。
- 每个状态事件必须紧跟一个 no-LLM `scheduler_event`，同一段文本产生多个状态时保持 `state_event -> scheduler_event` 成对相邻。
- scheduler payload 继续沿用 `PCWEB-039` 的 queued/skipped/cooldown/budget/not_called 契约。
- 浏览器 Live ASR 样本必须展示中文未闭环问题 `谁负责回滚？`。

原因：

- 用极小本地规则证明 Live ASR 已能驱动两类会议状态，而不是单一关键词 demo。
- 保持默认免费，不调用中转站，不引入远程 ASR 或 LLM 费用。
- 保持 evidence-first：`OpenQuestion` 也必须引用当前 ASR final/revision 产生的 EvidenceSpan。
- 给后续真实 state engine 定义稳定的 Web/API/UI payload 形状。

边界：

- 这是本地确定性 extraction contract，不是正式语义 state engine。
- 不做语义去重、状态关闭、问题回答匹配或行动项/风险抽取。
- 不生成建议卡。
- 不生成 `llm_schema_result`。
- 不调用 LLM、不读取 `configs/local/`、不读取真实音频、不加载 ASR 模型、不调用远程 ASR。
- 问题标记是测试和 UI contract 信号，不是生产质量策略。

TDD 证据：

- 先新增 `test_build_asr_live_events_extracts_open_question_state_candidate`，观察到失败：纯问题句没有 `state_event`。
- 实现本地 state spec extraction helper 后，该测试通过，`OpenQuestion` payload 包含 `question`、`evidence_span_ids`、`source=live_asr_stream` 和 `state_origin=local_deterministic_asr_skeleton`。
- 新增 `test_build_asr_live_events_keeps_multiple_state_candidates_next_to_scheduler`，先观察到失败：同一句同时产生两个状态时两个 `state_event` 被排序到两个 `scheduler_event` 前面。
- 增加 `_sort_step` 后，多状态同段保持 `state_event -> scheduler_event -> state_event -> scheduler_event`。
- 更新 API/SSE 测试，验证 Live ASR sample 中同时存在 `DecisionCandidate` 和 `OpenQuestion`。
- 根据独立审查建议，新增 API/SSE 边界测试，验证单句 `先灰度 10%，谁负责回滚？` 在公共 Live ASR endpoint 中也保持 `state_event -> scheduler_event -> state_event -> scheduler_event`。
- 更新浏览器 smoke 和前端 Live ASR 样本，验证状态板、转写和证据面板都出现 `谁负责回滚？`，同时仍无建议卡、LLM schema、silenced event 或 report 请求。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pcweb-040-live-asr-state-extraction-plan.md`
- `docs/superpowers/plans/2026-07-01-pcweb-040-live-asr-state-extraction.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

替代方案：

- 继续只支持 `灰度 -> DecisionCandidate`：拒绝，状态面过窄，难以证明实时 Copilot 价值。
- 直接接真实 state engine：暂不采用，会把抽取质量、模型调用、schema、成本和 UI contract 混在一起。
- 直接用 LLM 识别问题并生成建议卡：暂不采用，会产生费用并越过当前 no-LLM 骨架边界。

验证方式：

- `python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 state engine 后，替换本地问题标记规则，并支持 DecisionCandidate、OpenQuestion、ActionItem、Risk 的统一抽取/更新/关闭。
- 接入真实 scheduler 后，scheduler event 必须由持久化 scheduler event log 产生。
- 接入真实 LLM 后，只有通过 scheduler/cost/latency/schema gate 的 queued 状态变化才能生成强建议卡。

## DEC-041: Live ASR 先持久化 JSON audit record，不持久化音频或正式快照

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-037` 到 `PCWEB-040` 已经让本地 Live ASR JSON streaming event 能进入 Web/API/UI，并生成 transcript、EvidenceSpan、状态事件和 no-LLM scheduler audit。但这些记录仍只保存在 `create_app()` 内的进程内字典。对 PC 客户端来说，会议中产生的 live event audit trail 必须可追溯、可回放、可删除；否则重启后就丢失，后续也无法支撑问题复盘和质量分析。

决策：

新增 `PCWEB-041` Live ASR JSON audit persistence：

- 新增 `asr_live_repository.py`，提供：
  - `InMemoryAsrLiveSessionRepository`
  - `JsonFileAsrLiveSessionRepository`
- 默认无 `data_dir` 时保持 in-memory 行为。
- 设置 `MEETING_COPILOT_DATA_DIR` 或 `create_app(data_dir=...)` 时，把 Live ASR audit record 写到：
  - `live_asr_sessions/{session_id}.json`
- audit record 包含：
  - `session_id`
  - `provider`
  - `source=live_asr_stream`
  - `trace_kind=live_event`
  - `events`
- `/live/asr/sessions/{session_id}/events` 和 `.events.sse` 从 repository 读取，支持跨 app 实例读回。
- `DELETE /sessions/{session_id}` 同时删除正式 session record 和 Live ASR audit record；只存在 Live ASR audit record 时也返回 204。
- JSON 持久化模式下复用 `SESSION_ID_PATTERN` 拒绝 unsafe `session_id`。

原因：

- 让 Live ASR 的事件、状态和 scheduler 决策具备最小可追溯性。
- 保持本地免费，不调用 LLM、不调用远程 ASR。
- 不把 synthetic/live ASR event stream 误升级为正式 gated session snapshot。
- 为后续真实 ASR worker、真实 scheduler log 和桌面数据生命周期提供清晰落点。

边界：

- 只持久化 JSON audit record。
- 不持久化 raw audio。
- 不持久化 audio chunks。
- 不生成或持久化正式 transcript report。
- 不生成或持久化 LLM suggestion card。
- 不读取 `configs/local/`。
- 不代表真实 ASR provider endpoint final 质量、真实 scheduler 持久日志或真实 LLM 已完成。

TDD 证据：

- 先新增 `test_asr_live_session_persists_json_events_across_app_instances`，观察到失败：第二个 app instance 读取 Live ASR events 返回 404。
- 先新增 `test_delete_session_removes_persisted_asr_live_audit_record`，观察到失败：删除 endpoint 只删正式 session，无法删除纯 Live ASR audit record。
- 先新增 `test_create_asr_live_session_rejects_unsafe_session_id_with_json_persistence`，观察到失败：unsafe `../bad` 可以创建。
- 实现 `asr_live_repository.py` 并接入 `app.py` 后，三个测试通过。
- 后端 focused suite `tests/test_live_events.py tests/test_app.py` 通过。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_repository.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/pcweb-041-live-asr-audit-persistence-plan.md`
- `docs/superpowers/plans/2026-07-01-pcweb-041-live-asr-audit-persistence.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

替代方案：

- 继续只用进程内 dict：拒绝，不能跨重启追溯，和 PC 客户端复盘目标不一致。
- 直接把 Live ASR 转成正式 session snapshot：暂不采用，Live ASR 仍是 synthetic/local skeleton，尚未经过 core gate 和真实状态引擎。
- 直接持久化音频/chunk：暂不采用，音频生命周期涉及隐私告知、删除语义、存储配额和桌面平台适配，应在桌面数据生命周期阶段单独设计。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_session_persists_json_events_across_app_instances -q`
- `python3 -m pytest tests/test_app.py::test_delete_session_removes_persisted_asr_live_audit_record -q`
- `python3 -m pytest tests/test_app.py::test_create_asr_live_session_rejects_unsafe_session_id_with_json_persistence -q`
- `python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 desktop audio 后，必须扩展删除语义覆盖 raw audio、chunk、exports 和模型缓存。
- 接入真实 scheduler 后，应评估是否把 scheduler log 从 Live ASR audit record 中拆到独立持久化 event log。
- 接入真实 state engine 后，应决定 Live ASR audit record 与正式 gated session snapshot 的转换/引用关系。

## DEC-042: Live ASR audit record 先导出非正式 draft review，不生成正式报告

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-041` 让 Live ASR JSON audit record 可跨 app 实例读回并删除，但用户会后复盘仍只能看 raw events。产品目标不是只保存日志，而是让会议后能快速看到“听到了什么、抽取了哪些状态、调度器为什么会/不会调用 LLM”。另一方面，Live ASR 当前仍是 synthetic/local skeleton，ASR 质量、真实 state engine、真实 scheduler 和 LLM 建议卡都没有接入，不能把它包装成正式会议报告。

决策：

新增 `PCWEB-042` Live ASR draft review：

- 新增 `asr_live_report.py`：
  - `build_asr_live_draft_review(record)`
  - `render_asr_live_draft_markdown(review)`
- 新增 API：
  - `GET /live/asr/sessions/{session_id}/draft`
  - `GET /live/asr/sessions/{session_id}/draft.md`
- draft JSON 必须包含：
  - `review_type=asr_live_draft`
  - `is_formal_report=false`
  - `llm_call_status=not_called`
  - transcript segments/text
  - EvidenceSpans
  - state candidates
  - scheduler decisions
  - provider errors
  - evaluation summary
  - `suggestion_cards=[]`
  - `llm_schema_results=[]`
- Markdown 必须明确写出：
  - `Draft only; not a formal gated meeting report.`
  - transcript draft
  - evidence spans
  - state candidates
  - scheduler decisions
  - provider errors
  - stream summary

原因：

- 让持久化 Live ASR audit record 变成可复盘的产品产物，而不是只能给开发者看的事件 JSON。
- 保持 evidence-first：draft 中的状态候选和转写都回到 EvidenceSpan。
- 明确 draft/report 边界，防止当前 synthetic skeleton 被误判为正式会议纪要。
- 继续默认免费，不调用中转站。

边界：

- 不调用 LLM。
- 不生成建议卡。
- 不生成 `llm_schema_result`。
- 不使用 `build_markdown_report` 的正式报告路径。
- 不创建 core gated session snapshot。
- 不读取真实音频，不持久化音频 chunk，不读取 `configs/local/`。
- 不证明真实 ASR 质量、真实 state engine 或真实 scheduler log 已完成。

TDD 证据：

- 先新增 `test_asr_live_draft_review_json_summarizes_audit_record_without_llm`，观察到失败：`/draft` 返回 404。
- 先新增 `test_asr_live_draft_review_markdown_is_marked_as_non_formal`，观察到失败：`/draft.md` 返回 404。
- 实现 `asr_live_report.py` 和 app endpoints 后，两项测试通过。
- 后端 focused suite `tests/test_app.py tests/test_live_events.py` 通过。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/README.md`
- `docs/pcweb-042-live-asr-draft-review-plan.md`
- `docs/superpowers/plans/2026-07-01-pcweb-042-live-asr-draft-review.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

替代方案：

- 继续只提供 raw events：拒绝，不利于会后复盘和产品价值验证。
- 直接用正式 `build_markdown_report`：拒绝，会混淆 Live ASR skeleton 和 gated session snapshot。
- 直接调用 LLM 生成正式纪要：暂不采用，会引入费用、schema、幻觉和证据 gate 风险。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_draft_review_json_summarizes_audit_record_without_llm -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_draft_review_markdown_is_marked_as_non_formal -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 state engine 后，评估 draft review 是否仍从 raw audit record 派生，还是指向正式 gated snapshot。
- 接入真实 LLM 后，draft review 与正式 AI 纪要必须在 UI、API 和存储上清楚区分。
- 接入桌面音频后，draft review 必须能引用音频/chunk 生命周期元数据，但仍不得泄露真实本地路径。

## DEC-043: Web 工作台 Live ASR 模式展示 draft review，但继续关闭 formal report path

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-042` 已经提供 `/live/asr/sessions/{id}/draft(.md)`，但 Web 工作台 Live ASR 模式在 stream 结束后 report panel 仍为空，用户只能从 transcript/state/timeline 三个面板拼接复盘线索。这样产品价值仍偏开发者事件流，而不是“会后能快速看懂这段实时 ASR 到底听到了什么、抽取了什么、调度器会如何处理”。另一方面，Live ASR 当前还是本地 synthetic skeleton，不能走 `/sessions/{id}/report.md` 正式报告路径，否则会混淆 audit draft 与 gated session report。

决策：

新增 `PCWEB-043`：

- 前端新增 `loadLiveAsrDraft()`：
  - 请求 `/live/asr/sessions/{currentSessionId}/draft.md`
  - 将 Markdown 文本展示到 `report-panel`
  - 失败时只 toast 错误
- `connectLiveEventStream()` 收到 terminal `evaluation_summary` 后：
  - `live_mock` 仍调用正式 fixture report `loadReport()`
  - `live_asr` 调用 `loadLiveAsrDraft()`
- `loadReport()` 在 `currentEventMode === "live_asr"` 时直接分流到 `loadLiveAsrDraft()`，防止用户点击刷新报告按钮时请求 `/sessions/{id}/report.md`。
- `loadReport()` / `loadLiveAsrDraft()` 捕获请求开始时的 `session_id` 和 mode，响应回来后只有当前 session/mode 仍一致才写入 report panel。
- replay、Live Mock、Live ASR 创建 session 时递增 `sessionLoadToken`，异步创建响应回来后只有 token 和 mode 仍匹配才可回写当前 UI。
- `loadEventStream()` 捕获请求开始时的 session/mode，响应回来后只有当前 session/mode 仍一致才渲染 timeline 和 evaluation。
- Live ASR session 创建 pending 期间清空当前 session id，避免用户手动刷新时用上一场 session 请求 `/draft.md`。
- `EventSource` 不可用时，JSON fallback 读取 events 后也复用 terminal side-effect，确保 Live ASR 仍加载 draft。
- 浏览器 smoke 必须验证：
  - report panel 包含 `Draft only; not a formal gated meeting report.`
  - report panel 包含 `## State Candidates`
  - terminal summary 自动请求 `/draft.md` 1 次
  - 手动刷新报告后 `/draft.md` 累计 2 次
  - `/report.md` 请求始终 0 次
  - pending Live ASR session 不请求旧 session draft
  - stale Live ASR create response 不覆盖当前 replay session
  - stale event stream response 不覆盖当前 timeline/report
  - 延迟返回的旧 draft 不覆盖当前 replay report
  - 无 `EventSource` fallback 仍加载 draft
  - Live ASR 仍保持 0 张建议卡、0 个 LLM schema event、0 个 silenced event

原因：

- 让 Live ASR audit record 在 UI 中形成可读复盘产物，提升产品价值验证，而不是停留在 raw event viewer。
- 保持 draft/report 边界：当前 local ASR skeleton 不能被误认为正式会议纪要。
- 不增加远程费用，不调用中转站，不引入额外 ASR provider 成本。
- 为后续真实 ASR/state engine/LLM 接入预留 UI 位置，但不提前承诺这些能力已经完成。

边界：

- 不调用 LLM。
- 不生成建议卡。
- 不生成 `llm_schema_result`。
- 不请求 `/sessions/{id}/report.md` formal report。
- 不创建 core gated session snapshot。
- 不读取真实音频，不持久化 audio chunk，不读取 `configs/local/`。
- 不证明真实 ASR 质量、真实 state engine、真实 scheduler log 或真实 LLM live source 已完成。

TDD 证据：

- 先在 `test_workbench_static_assets_are_served` 中新增 `loadLiveAsrDraft`、`/draft.md`、Live ASR report guard 断言，观察到失败。
- 实现 `loadLiveAsrDraft()`、terminal summary hook 和 `loadReport()` Live ASR guard 后，聚焦测试通过。
- 代码质量复审指出 pending session、stale async response 和 EventSource fallback 三个风险；补充浏览器红灯测试后观察到 pending session 旧 draft 请求失败，再实现 session/mode guard、pending guard 和 fallback terminal side-effect。
- 复审继续指出 stale session create 和 stale event stream 回写风险；补充浏览器红灯测试后实现 `sessionLoadToken` 和 event-stream session/mode guard。
- 浏览器 smoke 增加 Live ASR draft/formal report fetch、手动刷新、pending session、stale create、stale events、stale draft 和 fallback 断言，并通过。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pcweb-043-live-asr-draft-ui-plan.md`
- `docs/superpowers/plans/2026-07-01-pcweb-043-live-asr-draft-ui.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

替代方案：

- 继续让 report panel 为空：拒绝，会让 Live ASR 复盘价值不足，用户仍要看 raw event。
- 直接调用正式 `/sessions/{id}/report.md`：拒绝，Live ASR audit record 不是 gated session snapshot。
- 直接调用 LLM 生成正式纪要：暂不采用，会引入费用、schema gate、证据链和幻觉风险。

验证方式：

- `python3 -m pytest tests/test_app.py::test_workbench_static_assets_are_served -q`
- `node e2e/browser_smoke.mjs`
- `python3 -m pytest tests/test_app.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 state engine 后，评估 Live ASR draft UI 是否仍展示 raw audit draft，还是切到正式 gated report。
- 接入真实 LLM 建议卡后，必须把 draft、正式 AI 纪要、实时建议卡三个 UI 状态明确分层。
- 接入桌面音频后，draft UI 需要展示录音/音频 chunk 的删除与保留状态，但不得泄露真实本地路径。

## DEC-044: Live ASR 本地状态骨架扩展到 ActionItem 和 Risk

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-040` 已经让 Live ASR 的 `final/revision` 可以生成 `DecisionCandidate` 和 `OpenQuestion`，但状态面仍偏窄。真实中文技术会议里，实时 Copilot 的价值不只是听到“决定了什么”和“问了什么”，还要及时捕捉“谁接下来做什么”和“什么条件触发风险/回滚”。如果 Live ASR 长期只围绕 `灰度` 和问题句，产品仍容易退化为“实时转写 + 少量关键词规则”，不足以证明实时工程辅助价值。

决策：

新增 `PCWEB-044`，把 Live ASR 本地确定性状态抽取 skeleton 扩展到四类 PC-1 state lane：

- `DecisionCandidate`
- `ActionItem`
- `Risk`
- `OpenQuestion`

本轮新增规则只用于本地 skeleton：

- 当 `final/revision` 文本出现明确行动信号，例如 `负责`、`补充`、`推进`、`跟进`、`处理`、`确认`、`整理`，且不是未闭环问题时，生成 `ActionItem` 候选。
- `ActionItem` 还需要至少一个可解释分配信号：动作词前有简短 owner、文本里有相对 deadline，或出现 `由`、`请`、`让`、`麻烦`、`安排` 等分配语气配合非 `确认` 的行动词；普通 `确认一下` 或 `请大家先确认一下` 不足以生成行动项。
- `ActionItem` 可从动作词/相对 deadline 前的简短中文 2-3 字姓名位置抽取 owner，并从文本里抽取 `今天`、`明天`、`下周三` 等相对 deadline。
- `负责人` 等名词里的 `负责` 不作为行动词触发 owner 抽取。
- 当 `final/revision` 文本出现 `如果` + `超过`，或出现 `风险`，或出现明显阈值异常信号时，生成 `Risk` 候选。
- `Risk` 会优先排除明确否定或解除风险的短句，例如 `没有风险`、`无风险`、`风险可控`、`风险已解除`。
- `Risk` 可把阈值条件标记为 `impact=condition_exceeded`，同句出现 `回滚` 时标记 `mitigation=回滚`。
- 每个新增状态都必须包含自包含 `state_item`、`evidence_span_ids`、`source=live_asr_stream`、`state_origin=local_deterministic_asr_skeleton`。
- 每个 `state_event` 后继续紧跟一个 no-LLM `scheduler_event`，记录候选进入调度审计但 `llm_call_status=not_called`。
- Web 工作台 Live ASR 样本和 draft review 必须能展示行动项和风险。

原因：

- 四类 state lane 能更接近真实工程会议结构，避免 MVP 看起来只是 ASR 展示。
- Action/Risk 是后续实时建议卡最关键的输入：owner/deadline 缺口、rollback 条件、错误率阈值、兼容性测试等都依赖这些状态。
- 继续用本地确定性 skeleton 可以验证事件契约、UI、draft 和 scheduler audit 的端到端形状，同时不调用中转站、不产生远程费用。
- 明确保留 evidence-first：即使是临时规则抽取，也必须能回跳到 ASR final/revision 的 EvidenceSpan。

边界：

- 这不是生产级语义 state engine。
- 不做语义去重、状态关闭、状态确认、跨段合并或 owner disambiguation。
- 不保证昵称、团队名、多人 owner 或复杂长句中的 owner 都能正确抽取。
- 不把所有包含 `确认` 的句子都视为可靠行动项；问题句必须优先进入 `OpenQuestion` 或被排除为行动项。
- 不调用 LLM。
- 不生成建议卡。
- 不生成 `llm_schema_result`。
- 不调用正式 `/sessions/{id}/report.md` gated report。
- 不读取真实音频，不加载真实 ASR 模型，不验证真实 ASR 中文质量。
- 不读取 `configs/local/`，不使用用户提供的中转站 key。

TDD 证据：

- 先新增 `test_build_asr_live_events_extracts_action_item_state_candidate`，样例为 `张三下周三补充兼容性测试用例。`，初始失败表现为找不到 `ActionItem` state event。
- 先新增 `test_build_asr_live_events_extracts_risk_state_candidate`，样例为 `如果错误率超过 0.1% 就回滚。`，初始失败表现为找不到 `Risk` state event。
- 实现本地 helper 后发现 owner 曾被误抽为 `张三下`，已收窄为当前 skeleton 可解释的简短中文姓名抽取。
- 实现过程中发现 `谁负责回滚？` 会被误判为行动项，已让 open question 优先级高于 action item。
- 独立代码审查指出三字 owner、普通 `确认一下` 和否定风险误报三个 skeleton 风险；已补红灯测试并观察到分别失败：三字姓名被截成后两字、普通确认被标为 `ActionItem`、`没有风险。` 被标为 open `Risk`。
- 第一轮 guard 支持 2-3 字 owner，行动项需 owner/deadline/分配语气之一，风险抽取排除明确否定/解除风险短句；对应测试通过。
- 复审继续指出 `负责人` 名词 owner 误抽、`请大家先确认一下` 误报、阈值句尾 `风险可控` 仍生成 open `Risk`；已补红灯测试并观察到失败，再收紧为：`负责(?!人)`、`确认` 不靠分配语气单独触发、否定/可控/解除风险先于阈值规则判断。
- 后端 focused suite 覆盖 state event、scheduler audit、draft review 和 API/SSE 样本。
- 浏览器 smoke 覆盖 Live ASR 状态板和 draft review 中可见 ActionItem/Risk，同时保持 0 张建议卡、0 个 LLM schema event、0 个 silenced event 和 0 次 formal report 请求。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pcweb-044-live-asr-action-risk-state-plan.md`
- `docs/superpowers/plans/2026-07-01-pcweb-044-live-asr-action-risk-state.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

成本/隐私影响：

- 本轮不增加任何远程费用。
- 本轮不调用远程 ASR、远程 LLM 或用户中转站。
- 本轮只处理 synthetic Live ASR event JSON 和本地 audit/draft 产物。
- 后续接入真实桌面音频前，仍不得读取或保存真实会议音频，除非另有明确数据生命周期设计。

替代方案：

- 继续只支持 `DecisionCandidate` / `OpenQuestion`：拒绝，状态面过窄，难以支撑“实时工程 Copilot”价值验证。
- 直接接 LLM 抽取 Action/Risk：暂不采用，会引入费用、延迟、schema gate 和幻觉风险；应先固定本地事件契约和 UI/draft 消费路径。
- 直接实现完整生产 state engine：暂不采用，范围过大；当前阶段先用可替换 skeleton 锁定端到端契约。
- 引入付费远程 ASR 来提升中文识别：拒绝作为默认路径，仍坚持默认本地/免费 ASR，远程 ASR 只可作为显式 bake-off 或企业可选模式。

验证方式：

- `python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- `ActionItem` 或 `Risk` 误报明显影响 UI 信任时，进入 `PCWEB-045` 做 false-positive guard、置信度和 degraded metadata。
- 接入真实 ASR provider 后，用中文技术会议 ASR 评测集复查 Action/Risk 抽取是否受错字、断句和中英混杂影响。
- 接入真实 state engine 后，用统一状态更新/关闭/合并逻辑替换本地规则，但保留本轮锁定的 evidence-backed event contract。
- 接入真实 LLM 建议卡后，必须继续保留 no-LLM scheduler audit 与付费 LLM call 的清晰边界。

## DEC-045: Live ASR 先输出 no-LLM suggestion candidate queue，不直接生成建议卡

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-044` 已经让 Live ASR 本地 skeleton 覆盖 `DecisionCandidate`、`ActionItem`、`Risk`、`OpenQuestion` 四类状态 lane。但如果链路停在状态板和 scheduler audit，产品仍只能说明“听到了什么、抽取了什么、理论上会不会调度 LLM”，还没有解释“这些状态为什么未来可能触发实时建议”。用户反复强调产品不能退化为转写工具，所以需要向建议链路前进；同时当前仍不能引入远程费用、LLM 幻觉、schema gate 或正式建议卡误导。

决策：

新增 `PCWEB-045`，在 Live ASR local skeleton 中为每个本地 `state_event` 增加一个 `suggestion_candidate_event`：

- 事件顺序固定为：
  - `state_event`
  - `scheduler_event`
  - `suggestion_candidate_event`
- `suggestion_candidate_event` 必须包含：
  - `candidate_id`
  - `candidate_type=state_gap_review`
  - `target_type`
  - `target_id`
  - `gap_rule_id`
  - `suggested_prompt`
  - `trigger_reason`
  - `decision_reason`
  - `source_event_ids`
  - `scheduler_event_type`
  - `evidence_span_ids`
  - `segment_batch`
  - `llm_call_status=not_called`
  - `card_status=not_created`
  - `source=live_asr_stream`
  - `candidate_origin=local_deterministic_asr_skeleton`
- 初始本地映射：
  - `DecisionCandidate` -> `release.rollback.owner.required`
  - `OpenQuestion` -> `open.question.followup`
  - `Risk` -> `risk.rollback.validation`
  - `ActionItem` -> `action.owner.deadline.confirmation`
- Draft review JSON 新增 `suggestion_candidates`。
- Draft Markdown 新增 `## Suggestion Candidates`。
- Web timeline 展示 `suggestion_candidate_event` 摘要。

原因：

- 让 Live ASR 链路从“抽状态”推进到“解释未来为什么可能建议”，更接近实时 Copilot 价值。
- 保持费用边界：不调用中转站，不调用远程 ASR，不生成 LLM schema。
- 保持产品边界：candidate 是审计记录，不是用户可操作的正式建议卡。
- 让后续真实 suggestion card engine 可以复用同一 gap-rule/evidence/source-event 契约。

边界：

- 不调用 LLM。
- 不生成正式 `suggestion_card`。
- 不生成 `llm_schema_result`。
- 不生成 `suggestion_silenced`。
- 不支持卡片反馈动作。
- 不做 ranking、dedupe、prompt policy 或跨段语义合并。
- 不请求 `/sessions/{id}/report.md` formal report。
- 不读取真实音频，不读取 `configs/local/`。
- 不声明真实 state engine、真实 scheduler、真实 LLM 或正式建议卡链路已完成。

TDD 证据：

- 先新增 `test_build_asr_live_events_emits_suggestion_candidate_after_action_scheduler`，观察到失败：事件序列缺少 `suggestion_candidate_event`。
- 实现本地 candidate payload 后该测试通过。
- 旧事件顺序测试暴露同段多状态时 `_sort_step` 仍按二联事件步进，导致 candidate 和下一条 state 排序冲突；根因修复为三联步进 `state/scheduler/candidate`。
- API/SSE 测试更新，验证五个 Live ASR 状态候选都生成 suggestion candidate，且 `llm_call_status=not_called`、`card_status=not_created`。
- Draft JSON/Markdown 测试先失败，再实现 `suggestion_candidates` 收集和 `## Suggestion Candidates` 渲染后通过。
- Browser smoke 验证 timeline 可见 `suggestion_candidate_event`、`risk.rollback.validation`、`action.owner.deadline.confirmation`、`not_created`，同时仍保持 0 张正式建议卡、0 个 LLM schema event、0 个 silenced event 和 0 次 formal report 请求。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_report.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_live_events.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `code/web_mvp/README.md`
- `docs/pcweb-045-live-asr-suggestion-candidate-plan.md`
- `docs/superpowers/plans/2026-07-01-pcweb-045-live-asr-suggestion-candidate.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

成本/隐私影响：

- 本轮不增加任何远程费用。
- 本轮不调用远程 ASR、远程 LLM 或用户中转站。
- 本轮只处理 synthetic Live ASR event JSON 和本地 audit/draft 产物。

替代方案：

- 直接生成正式建议卡：拒绝，当前没有真实 state engine、prompt policy、schema gate 和 LLM 质量控制。
- 只保留 scheduler_event，不增加 candidate：拒绝，无法说明具体产品 gap，链路仍偏底层调度审计。
- 直接调用 LLM 生成候选：暂不采用，会引入费用、延迟和幻觉风险。

验证方式：

- `python3 -m pytest tests/test_live_events.py tests/test_app.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 suggestion card engine 前，必须决定 candidate -> LLM request -> schema result -> show/silence 的持久化事件关系。
- 当候选误报明显影响用户信任时，必须增加 confidence/degradation metadata 和候选降级规则。
- 接入真实 LLM 后，candidate 和正式 card 必须在 UI、API 和 draft 中继续分层展示。

## DEC-046: Live ASR suggestion candidate 增加本地 confidence/degradation 元数据

- 日期：2026-07-01
- 状态：Accepted

背景：

`PCWEB-045` 已经让 Live ASR skeleton 输出 no-LLM `suggestion_candidate_event`，但所有候选在 UI 和 draft 中看起来强度相同。对于会议 Copilot，这会带来过度承诺风险：短证据、低 ASR 置信度、缺失 ASR 置信度、行动项缺 owner/deadline 的候选，不应该和完整候选同等展示。用户反复要求产品不能只做转写，也不能做出效果很差、不可解释的实时建议，因此候选层需要先具备本地可审计的质量信号。

决策：

新增 `PCWEB-046`，为每个 Live ASR `suggestion_candidate_event.payload` 增加：

- `candidate_policy_version=asr-candidate-policy.v1`
- `confidence_source=local_deterministic_heuristic`
- `confidence`
- `confidence_level`
- `degradation_reasons`

初始降级原因只使用本地可观察事实：

- `low_asr_confidence`
- `missing_asr_confidence`
- `evidence_text_short`
- `action_owner_missing`
- `action_deadline_missing`
- `risk_mitigation_missing`

初始分数是候选队列质量信号，不是事实正确性保证：

- 起始 `0.90`
- 低 ASR 置信度扣 `0.20`
- 缺失 ASR 置信度扣 `0.15`
- 证据文本过短扣 `0.15`
- ActionItem 缺 owner 扣 `0.10`
- ActionItem 缺 deadline 扣 `0.10`
- Risk 缺 mitigation 扣 `0.10`
- clamp 到 `[0.10, 0.99]`
- `>=0.80` 为 `high`，`>=0.55` 为 `medium`，其余为 `low`

原因：

- 让候选审计记录具备可信度解释，避免用户把本地规则候选误读成正式 AI 建议。
- 为后续候选排序、队列过滤、LLM 调用前置策略留下稳定字段。
- 保持成本边界：不调用中转站、不调用远程 ASR、不调用远程 LLM。

边界：

- 不生成正式 `suggestion_card`。
- 不生成 `llm_schema_result`。
- 不生成 `suggestion_silenced`。
- 不新增真实 ranking、dedupe、跨段语义合并或 prompt policy。
- 不把 scheduler cooldown/budget 作为候选质量降级；调度状态继续由 `scheduler_event_type` 和 `decision_reason` 表达。
- 不请求 formal `/report.md`。
- 不读取真实音频，不读取 `configs/local/`。
- 不声明 confidence 是 ASR 准确率、事实正确率或 LLM 回答质量。

验证方式：

- `python3 -m pytest tests/test_live_events.py -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 ASR provider 后，必须用中文技术会议评测集校验 provider confidence 与本地降级阈值是否匹配。
- 接入真实 suggestion card engine 前，必须决定 low/medium/high 候选是否会影响 LLM 调用、UI 排序和用户可见性。
- 如果用户把 candidate 误读为正式建议，需要在 UI 层进一步弱化或隔离候选展示。

## DEC-047: Live ASR suggestion candidate queue 增加只读查询端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-045` 和 `PCWEB-046` 已经让 Live ASR skeleton 输出 no-LLM `suggestion_candidate_event`，并为候选增加本地 deterministic quality metadata。但后续 scheduler/card engine 如果只能从完整 `/events` 流里自行过滤 candidate，会让候选边界分散在调用方中，也不利于审计和测试。需要一个窄接口明确表达“当前会话有哪些候选可以进入后续建议链路”，同时继续保持不调用 LLM、不生成正式建议卡。

决策：

新增 `PCWEB-047`，提供：

- `GET /live/asr/sessions/{session_id}/suggestion-candidates`

响应只投影已有 Live ASR audit record 中的 `suggestion_candidate_event`：

- `session_id`
- `source=live_asr_stream`
- `trace_kind=live_event`
- `candidate_count`
- `candidates`

每个 candidate 保留：

- 原始事件 `sequence`
- 原始事件 `id`，在响应中命名为 `event_id`
- `event_type=suggestion_candidate_event`
- `at_ms`
- 完整 `payload`

原因：

- 给后续真实 scheduler/card engine 一个稳定、可测试、低成本的候选读取边界。
- 避免调用方重复解析完整事件流，降低误把 transcript/state/scheduler 事件当成候选的风险。
- 保留原始事件顺序，避免在本阶段引入 ranking、dedupe、merge 或 filtering 行为。

边界：

- 只读，不修改 audit record。
- 不调用远程 ASR。
- 不调用 LLM 或中转站。
- 不生成正式 `suggestion_card`。
- 不生成 `llm_schema_result`。
- 不生成 `suggestion_silenced`。
- 不排序、不过滤、不去重、不合并。
- 不读取真实音频，不读取 `configs/local/`。

验证方式：

- `python3 -m pytest tests/test_app.py -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 suggestion card engine 前，必须决定该端点是否需要分页、candidate status、LLM request linkage 或 query filters。
- 如果候选数量在长会中快速增长，需要增加分页或 session-local cursor，但不得改变原始 event stream 顺序。

## DEC-048: Live ASR 增加 no-LLM LLM request draft audit event

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-047` 已经提供只读候选队列查询，但候选距离正式建议卡仍缺一段关键链路：如果未来要调用 LLM，这个请求会带哪些 gap rule、证据、segment batch、prompt policy 和候选质量信号。直接接中转站会引入费用、延迟、schema gate 和幻觉风险；继续停在 candidate queue 又无法验证 request assembly 边界。

决策：

新增 `PCWEB-048`，在每个 Live ASR `suggestion_candidate_event` 后追加本地 `llm_request_draft_event`：

- `state_event`
- `scheduler_event`
- `suggestion_candidate_event`
- `llm_request_draft_event`

`llm_request_draft_event` 只表示未来 LLM 请求草稿，必须包含：

- `request_id`
- `request_type=llm_suggestion_card_draft`
- `request_status=draft_only`
- `target_candidate_id`
- `target_type`
- `target_id`
- `gap_rule_id`
- `prompt_version=not-called`
- `model=not-called`
- `llm_call_status=not_called`
- `card_status=not_created`
- `schema_status=not_generated`
- `suggested_prompt`
- `input_summary`
- `source_event_ids`
- `evidence_span_ids`
- `segment_batch`
- candidate confidence/degradation metadata
- `request_origin=local_deterministic_asr_request_draft`
- `source=live_asr_stream`

原因：

- 在不增加费用的情况下，验证 candidate -> future LLM request 的数据边界。
- 让 prompt/version/model/schema/card 状态明确保持 `not-called/not-created/not-generated`。
- 为后续真实 LLM 调用、schema gate、card show/silence 链路提供可测契约。

边界：

- 不调用中转站或远程 LLM。
- 不选择真实模型。
- 不估算 token。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不请求 formal `/report.md`。
- 不排序、过滤、去重、跨段合并。
- 不读取真实音频，不读取 `configs/local/`。

验证方式：

- `python3 -m pytest tests/test_live_events.py -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `node e2e/browser_smoke.mjs`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 LLM 前，必须决定 request draft -> actual LLM request -> schema result -> suggestion card/silenced event 的持久化关联。
- 如果 request draft 数量在长会中过多，需要分页或 cursor，但不得改变原始事件顺序。

## DEC-049: Live ASR LLM request draft 增加只读查询端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-048` 已经把 future LLM request assembly 固定为 `llm_request_draft_event`，并在 Web timeline 与 draft review 中可见。但后续真实 LLM 执行器如果只能从完整 `/events` 里自行过滤 request draft，会重复 PCWEB-047 之前 candidate queue 的问题：调用方边界分散、测试难以聚焦，也容易把 candidate、request draft、schema result、formal card 混在一起。

决策：

新增 `PCWEB-049`，提供只读端点：

- `GET /live/asr/sessions/{session_id}/llm-request-drafts`

响应只投影已有 Live ASR audit record 中的 `llm_request_draft_event`，并采用与 candidate query 一致的规范化只读 projection：`sequence`/`at_ms` 输出为整数，`event_id`/`event_type` 输出为字符串，单条 draft 不重复携带顶层 `source`/`trace_kind`：

- `session_id`
- `source`
- `trace_kind`
- `request_draft_count`
- `request_drafts`

每个 request draft 保留：

- `sequence`
- `event_id`
- `event_type=llm_request_draft_event`
- `at_ms`
- 完整 `payload`

原因：

- 给后续真实 LLM executor 一个稳定、可测试、低成本的 draft request 读取边界。
- 继续保持 request draft 与 candidate queue、schema result、formal suggestion card 分层。
- 避免调用方重复解析完整 `/events`，降低未来误调用 LLM 或误生成正式卡片的风险。

边界：

- 只读，不改变 Live ASR audit record。
- 不调用 LLM 或中转站。
- 不选择真实模型。
- 不估算 token。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不排序、过滤、去重或分页；保留原始 event stream 顺序。
- 不读取真实音频，不读取 `configs/local/`。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_request_drafts_endpoint_returns_only_request_draft_queue -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 LLM executor 前，必须决定该端点是否需要 status lifecycle、cursor/pagination、重试记录或按 candidate/request id 查询。
- 如果长会 request draft 数量快速增长，需要分页或 session-local cursor，但不得改变原始 event stream 顺序。

## DEC-050: Live ASR 增加 no-call LLM execution preview 查询端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-048` 已经把 future LLM request draft 写入 Live ASR audit stream，`PCWEB-049` 已经允许调用方只读查询 request draft 队列。下一步如果直接接真实中转站，容易把“准备执行”“已经调用”“schema 产物”“正式建议卡”混在一起，也会过早引入费用、密钥和失败重试问题。

决策：

新增 `PCWEB-050`，提供只读端点：

- `GET /live/asr/sessions/{session_id}/llm-execution-previews`

响应从已有 `llm_request_draft_event` 派生本地 execution preview queue：

- `session_id`
- `source`
- `trace_kind`
- `execution_preview_count`
- `execution_previews`

每个 execution preview 必须包含：

- `execution_id`
- `execution_status=preview_only`
- `request_id`
- `request_draft_event_id`
- `request_draft_sequence`
- `request_type`
- `target_candidate_id`
- `target_type`
- `target_id`
- `gap_rule_id`
- `prompt_version=suggestion-card-execution-preview.v1`
- `provider=not_configured`
- `model=not_called`
- `llm_call_status=not_called`
- `schema_name=SuggestionCardV1`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `idempotency_key`
- `source_event_ids`
- `evidence_span_ids`
- `segment_batch`
- candidate quality metadata
- `input_summary`
- `suggested_prompt`

原因：

- 给后续真实 LLM executor 一个清晰、可测、可审计的执行前 envelope。
- 在不读取密钥、不调用中转站、不产生费用的前提下，先固定 idempotency、schema target、request/candidate/evidence/segment linkage。
- 继续保持 request draft、execution preview、actual LLM call、schema result、formal suggestion card 的分层，避免 UI/API 提前把 preview 当作正式建议。

边界：

- 只读，不改变 Live ASR audit record。
- 不追加事件，不改变 `/events` 输出。
- 不读取 `configs/local/` 或 API key。
- 不调用 LLM 或中转站。
- 不选择真实 provider/model。
- 不估算 token。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不排序、过滤、去重、重试、状态迁移或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_execution_previews_endpoint_returns_preview_queue_without_calling_llm -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 LLM executor 前，必须决定 preview -> actual call -> schema result -> show/silence/card 的持久化事件关系。
- 真实调用必须显式 opt-in，读取本地密钥配置前需要独立配置边界和敏感扫描。
- 如果长会 preview 数量快速增长，需要分页或 session-local cursor，但不得改变原始 request draft 顺序。

## DEC-051: Live ASR 增加 disabled LLM executor run 入口

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-050` 已经把 future LLM executor envelope 暴露为只读 preview。继续推进时，如果下一步直接接真实中转站，会过早引入费用、密钥读取、provider 失败、schema 校验、重试和正式建议卡生命周期。另一方面，如果长期只停留在 preview，系统又无法证明“执行动作入口”会如何被审计和关闭。

决策：

新增 `PCWEB-051`，提供 executor action boundary：

- `POST /live/asr/sessions/{session_id}/llm-execution-runs`

请求体当前仅支持：

- `mode=disabled`

`mode` 必填；空请求体必须返回 422，不能默认进入 disabled run。这样后续接入真实 `enabled` 模式时，调用方意图仍然是显式的。

请求体不得携带额外字段；API key、provider、model 或 relay 配置不能通过 disabled endpoint 传入或被静默忽略。

响应从现有 execution preview 派生 disabled run queue：

- `session_id`
- `source`
- `trace_kind`
- `executor_mode=disabled`
- `run_count`
- `runs`

每个 run 必须包含：

- `run_id`
- `run_status=skipped`
- `skip_reason=llm_executor_disabled`
- `execution_id`
- `execution_status=preview_only`
- `request_id`
- `request_draft_event_id`
- `request_draft_sequence`
- `request_type`
- `target_candidate_id`
- `target_type`
- `target_id`
- `gap_rule_id`
- `prompt_version=suggestion-card-execution-preview.v1`
- `provider=not_configured`
- `model=not_called`
- `llm_call_status=not_called`
- `schema_name=SuggestionCardV1`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `idempotency_key`
- `source_event_ids`
- `evidence_span_ids`
- `segment_batch`
- candidate quality metadata
- `input_summary`
- `suggested_prompt`

`mode` 不是 `disabled` 时返回 422。missing session 返回 404。空 request draft/previews 返回空 `runs`。

原因：

- 明确“执行入口已经存在，但被关闭”，避免后续把真实调用、preview 和 formal card 混成一个模糊状态。
- 让 UI/调用方可以演练 executor action path，同时仍保持 0 费用、0 密钥读取、0 远程调用。
- 给真实 `enabled` 模式预留审计字段：run id、execution/request linkage、idempotency、schema target、skip/execute decision。
- 使用 POST 是因为这是未来执行动作入口；当前不持久化 skipped run，是为了避免把 disabled dry-run 当成真实执行历史。

边界：

- 当前仅支持 `mode=disabled`。
- 请求体必须显式传入 `mode`，且拒绝空 body 或额外字段。
- 不追加事件，不改变 `/events` 输出。
- 不写 repository 状态。
- 不读取 `configs/local/` 或 API key。
- 不调用 LLM 或中转站。
- 不选择真实 provider/model。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_execution_runs_disabled_endpoint_returns_skipped_runs_without_calling_llm -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入 `mode=enabled` 前，必须先设计 provider config、secret boundary、真实 LLM gateway adapter、schema validation、cost/token accounting、retry/idempotency、timeout/degradation 和 card show/silence lifecycle。
- 如果 skipped run 需要成为长期审计记录，必须先决定是否新增 run event 类型，以及它与 preview/request draft/schema/card 的事件关系。
- 如果长会 run 数量快速增长，需要分页或 session-local cursor，但不得改变 request draft / preview 的原始顺序。

## DEC-052: Live ASR 增加 LLM provider readiness 只读阻断端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-051` 已经提供 disabled executor action boundary。下一步如果直接接 provider config 或真实中转站，会把密钥读取、付费调用、schema/card 生命周期和 enabled executor 合同一次性混在一起。产品需要先能回答“为什么现在还不能执行真实 LLM”，并把后续启用所需的配置和决策显式暴露出来。

决策：

新增 `PCWEB-052`，提供只读端点：

- `GET /live/asr/sessions/{session_id}/llm-provider-readiness`

响应从现有 Live ASR audit record 和本地 executor projections 派生：

- `session_id`
- `source`
- `trace_kind`
- `readiness_status=not_ready`
- `executor_mode=disabled`
- `enabled_mode_status=blocked`
- `provider_protocol=openai_compatible_chat_completions`
- `provider_config_status=not_loaded`
- `provider_config_source=not_read`
- `credentials_status=not_read`
- `base_url_status=not_configured`
- `model_status=not_configured`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `request_draft_count`
- `execution_preview_count`
- `disabled_run_count`
- `queue_status`
- `can_execute_llm=false`
- `block_reasons`
- `required_config_fields`
- `next_required_decisions`

当 session 没有 request drafts 时，`queue_status=empty`，并额外包含 `no_request_drafts` blocker。missing session 返回 404。

原因：

- 把真实 enabled execution 的前置条件显式暴露，避免后续代码悄悄读取本地配置或调用中转站。
- 让 Web/API 调用方可以区分“队列为空”“执行器被关闭”“provider config 没有加载”“密钥没有读取”“enabled 模式尚未设计”。
- 保持成本边界：PCWEB-052 只报告 readiness，不读取任何 secret，也不估算 token。
- 为未来真实 provider config、secret manager、schema validation、cost accounting、retry/degradation 和 card lifecycle 分别留出可审查入口。

边界：

- 只读，不改变 Live ASR audit record。
- 不追加事件，不改变 `/events` 输出。
- 不读取 `configs/local/`。
- 不读取环境变量中的密钥、provider config 文件或 API key。
- 不调用 `asr_bakeoff.llm_smoke` 或 `load_llm_gateway_config`。
- 不调用 LLM 或中转站。
- 不选择真实 provider/model。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_readiness_endpoint_reports_not_ready_without_reading_config -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 provider config loader 前，必须先设计 secret boundary 和敏感扫描策略，明确哪些字段可被 UI/API 展示、哪些永远只能 masked。
- 接入 `mode=enabled` 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。
- 如果 readiness 需要展示多个 provider，需要新增 provider registry 合同；不得直接枚举或读取本地密钥文件。

## DEC-053: Live ASR 增加 LLM provider config secret/display boundary 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-052` 已经把 LLM provider readiness 固定为 `not_ready/disabled/blocked`，并明确下一道闸门是 provider config secret boundary。如果下一步直接实现真实 config loader，很容易把“读取本地中转站配置”“判断 key 是否存在”“展示 masked key/base_url/model”“执行真实调用”混在一个增量里。产品需要先定义未来配置字段的分类和展示策略，并证明 Web/API 状态接口不会读取或泄露真实配置。

决策：

新增 `PCWEB-053`，提供只读端点：

- `GET /live/asr/sessions/{session_id}/llm-provider-config-boundary`

响应从现有 Live ASR audit record 派生 session/source/trace kind，但主体是静态 template-only policy：

- `boundary_status=template_only`
- `provider_protocol=openai_compatible_chat_completions`
- `config_load_status=not_loaded`
- `config_source_status=not_read`
- `credentials_status=not_read`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`
- `fields`
- `allowed_response_fields`
- `forbidden_response_fields`
- `secret_storage_policy`
- `next_required_decisions`

字段 policy 固定包含：

- `base_url`：`public_endpoint`，未来只允许 origin-only 或 masked 展示，本阶段不返回真实值。
- `api_key`：`secret`，`display_policy=never_display`，`response_value_policy=never_return_value`。
- `model`：`public_model_id`，未来 loader mask review 后可展示。
- `timeout_seconds`：`non_secret_runtime`，未来校验后可展示。
- `ca_bundle_path`：`local_path_sensitive`，未来只允许 basename 或不展示，本阶段不返回真实路径或 basename。

`api_key` 可以作为字段名元数据或 forbidden field 名称出现，但不得作为顶层响应值、configured value、masked real value、presence/validity/length/hash/prefix/suffix/fingerprint 出现。missing session 返回 404。transcript-only session 同样返回 template-only policy。

原因：

- 把未来真实中转站/OpenAI-compatible provider 接入前的密钥展示规则先变成可测合同。
- 明确“字段名元数据”与“字段真实值”不是一回事，避免 UI/API 因看到 `api_key` 字段名就误以为可以显示 key 状态。
- 防止 masked key、masked base_url、CA bundle basename 这类看似安全的展示在未授权读取阶段泄露真实配置存在性或环境信息。
- 让后续 provider config loader、secret manager、masked provider status、enabled executor 可以拆成独立、可审查增量。

边界：

- 只读，不改变 Live ASR audit record。
- 不追加事件，不改变 `/events` 输出。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不调用 `asr_bakeoff.llm_smoke` 或 `load_llm_gateway_config`。
- 不使用任何 masked real key/base_url/path helper，因为 mask 之前必须先读取真实值。
- 不报告 key/file/config 是否存在、是否有效、长度、hash、prefix、suffix 或 fingerprint。
- 不调用 LLM 或中转站。
- 不选择真实 provider/model。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_template_without_reading_config -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_template_without_reading_config tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_template_for_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_config_boundary_endpoint_returns_404_for_missing_session tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 provider config loader 前，必须先设计 loader 输入、secret storage adapter、masked provider status response 和敏感扫描策略。
- 接入任何 masked provider status 前，必须定义哪些字段可以读取、如何授权读取、如何避免 key presence/validity/length/hash/prefix/suffix/fingerprint 泄露。
- 接入 `mode=enabled` 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。

## DEC-054: Live ASR 增加 LLM provider masked status template 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-053` 已经定义 provider config 字段分类和展示边界，但后续 UI/API 仍需要一个“provider 状态响应长什么样”的合同。如果直接在真实 config loader 中同时设计 status response，很容易把读取配置、mask 真实值、展示 provider 状态和 enabled executor 混成一个不可审查的大增量。产品需要先定义 future masked provider status envelope，同时证明当前 Web MVP 仍不读取真实配置或密钥。

决策：

新增 `PCWEB-054`，提供只读端点：

- `GET /live/asr/sessions/{session_id}/llm-provider-masked-status`

响应从现有 Live ASR audit record 派生 session/source/trace kind，但主体是静态 template-only status envelope：

- `status_kind=masked_provider_status`
- `status_mode=template_only`
- `provider_protocol=openai_compatible_chat_completions`
- `provider_status=not_configured`
- `config_load_status=not_loaded`
- `config_source_status=not_read`
- `credentials_status=not_read`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`
- `display_values`
- `display_value_status`
- `masked_value_policy`
- `forbidden_status_signals`
- `block_reasons`
- `next_required_decisions`

`display_values` 固定为：

- `base_url_origin=null`
- `model=null`
- `timeout_seconds=null`
- `ca_bundle_name=null`
- `api_key=null`

`api_key` 可以作为 display placeholder、policy key 或 forbidden signal 名称出现，但不得作为真实值、masked real value、presence/validity/length/hash/prefix/suffix/fingerprint 出现。missing session 返回 404。transcript-only session 同样返回 template-only status envelope。

原因：

- 把 future provider status UI/API 合同先变成可测、可审查的零读取响应。
- 避免后续把“状态展示”变成“配置探测”，尤其是 key 是否存在、是否有效、长度、hash、prefix/suffix/fingerprint 这类侧漏。
- 明确 masked status 目前仍不 mask 任何真实值，因为 mask 之前必须先读取真实配置；真实 loader 需要单独设计和授权。
- 为后续 provider config loader、secret storage adapter、authorized masked status loader、enabled executor 拆出清晰边界。

边界：

- 只读，不改变 Live ASR audit record。
- 不追加事件，不改变 `/events` 输出。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不调用 `asr_bakeoff.llm_smoke` 或 `load_llm_gateway_config`。
- 不使用任何需要真实 secret/provider URL/path 的 masking helper。
- 不返回真实值、masked real value、key/file/config 是否存在、是否有效、长度、hash、prefix、suffix 或 fingerprint。
- 不调用 LLM 或中转站。
- 不选择真实 provider/model/base URL/CA path。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_template_without_reading_config -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_template_without_reading_config tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_template_for_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_masked_status_endpoint_returns_404_for_missing_session tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

- 接入真实 provider config loader 前，必须先设计 loader 输入、secret storage adapter、authorized masked status loader 和敏感扫描策略。
- 接入任何非 null display value 前，必须定义授权读取条件、字段级展示策略、masking 规则和禁止侧漏清单。
- 接入 `mode=enabled` 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。

## DEC-055: Live ASR 增加 LLM provider config request-body validation 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-052` 到 `PCWEB-054` 已经把 provider readiness、config 字段边界和 masked status 合同拆开，但后续真正接入 OpenAI-compatible 中转站前，还需要一个更小的可测步骤：验证“调用方显式提交的 config draft 形状是否合规”。如果直接读取 `configs/local/` 或环境变量做校验，会把 config loader、secret storage、masked status、enabled executor 和付费调用混到一起，也容易在 422 错误或状态响应中泄露 API key。

决策：

新增 `PCWEB-055`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-provider-config-validation`

端点只校验请求体中的 draft config，并从 Live ASR audit record 派生 `session_id`、`source` 和 `trace_kind`。成功响应固定表达：

- `validation_kind=provider_config_request_body`
- `validation_status=valid`
- `validation_mode=request_body_only`
- `provider_protocol=openai_compatible_chat_completions`
- `config_source_status=request_body_only`
- `config_file_status=not_read`
- `credentials_status=provided_but_not_returned`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`

校验规则：

- `provider_protocol` 必须是 `openai_compatible_chat_completions`。
- request body 必须是 JSON object；top-level string/list 等非 object 输入返回 generic 422，不回显原始输入。
- `base_url` 必须是带 host 的 HTTPS URL，且不得包含 userinfo credentials；响应只返回 origin。
- `api_key` 必须存在且非空，但不得返回、mask、hash、计数、落盘或用于调用。
- `model` 必须是非空字符串，可作为非 secret model id 展示。
- `timeout_seconds` 可选，范围为 1 到 120。
- `ca_bundle_path` 可选，必须是无路径穿越的相对路径；响应只返回 basename。
- 未知字段直接 422，避免 `authorization`、`bearer_token`、`raw_config` 等字段进入响应合同。

原因：

- 让 provider config 的 UI/API 输入形状先可测，而不引入额外收费或真实 provider 调用。
- 手写请求体校验和 generic 422 detail，避免 FastAPI/Pydantic 错误详情回显原始 `api_key` 或 `authorization`。
- 只返回 safe derived display values，保持 `api_key` 只能作为字段名/policy 名称出现，不能作为真实值或 masked real value 出现。
- 为后续 secret storage adapter、authorized config file loader 和 enabled executor 继续拆小增量。

边界：

- 只校验 caller-provided JSON request body。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不调用 `asr_bakeoff.llm_smoke` 或 `load_llm_gateway_config`。
- 不发网络请求，不调用远程 ASR/LLM 或中转站。
- 不存储、日志化、mask、hash、截取 prefix/suffix、计算长度或报告 `api_key` presence/validity/fingerprint。
- 不改变 Live ASR audit record，不追加 validation event。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_validates_request_body_without_reading_config -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_validates_request_body_without_reading_config tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_accepts_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_returns_404_for_missing_session tests/test_app.py::test_asr_live_llm_provider_config_validation_endpoint_rejects_invalid_request_without_leaking_secret -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-055-live-asr-llm-provider-config-validation-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-055-live-asr-llm-provider-config-validation.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 provider config loader 前，必须先设计 loader 授权、secret storage adapter、错误响应脱敏、日志脱敏和本地配置生命周期。
- 接入 enabled executor 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。
- 允许 HTTP、本地开发 URL、远程 ASR provider 或非 OpenAI-compatible provider 前，必须单独记录安全和成本决策。

## DEC-056: Live ASR 增加 LLM provider config loader preflight 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-055` 已经能校验调用方显式提交的 provider config draft，但真实产品还需要一个更接近本地配置文件读取的边界：用户未来可能选择一个本地 provider config 文件，让系统加载中转站配置。这个步骤如果直接读取文件，会立刻涉及本地路径隐私、secret storage、错误响应脱敏、读取审计、masked status 和 enabled executor。为了不把密钥读取和付费调用揉在一起，需要先增加 metadata-only preflight 合同。

决策：

新增 `PCWEB-056`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-provider-config-loader-preflight`

端点只校验 future config loader request 的形状和授权意图，并从 Live ASR audit record 派生 `session_id`、`source` 和 `trace_kind`。成功响应固定表达：

- `preflight_kind=provider_config_loader`
- `preflight_status=accepted`
- `preflight_mode=metadata_only`
- `loader_mode=preflight_only`
- `provider_protocol=openai_compatible_chat_completions`
- `config_source_status=caller_supplied_path_metadata`
- `config_file_status=not_read`
- `config_existence_status=not_checked`
- `credentials_status=not_read`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`
- `safe_to_load_config=false`

校验规则：

- request body 必须是 JSON object；非 object 输入返回 generic 422，不回显原始输入。
- `loader_mode` 必须是 `preflight_only`。
- `provider_protocol` 必须是 `openai_compatible_chat_completions`。
- `config_path` 必须是非空本地文件系统路径字符串；不得使用 URL/netloc，不得包含 NUL/control characters，且不得包含 POSIX `/../` 或 Windows `\..\` 形式的路径穿越。
- `requested_fields` 只能包含 `base_url`、`api_key`、`model`、`timeout_seconds`、`ca_bundle_path`，且不得重复。
- `authorization` 必须精确表达 `user_confirmed_local_config_access=true`、`allow_secret_read=false`、`allow_llm_call=false`。
- 未知字段直接 422，避免 `authorization_header`、`bearer_token`、`raw_config`、`base_url`、`model`、`api_key` 等字段进入响应合同。

原因：

- 让 future config loader 的请求形状和授权开关先变成可测合同。
- 明确 preflight 不等于读取本地配置文件，不判断文件存在，不返回本地路径、文件名、父目录或任何 path-derived label。
- 避免通过文件路径、文件存在性、文件大小、mtime、hash/fingerprint 等侧信道泄漏用户环境信息。
- 继续保持默认无额外费用：不调用远程 ASR/LLM，不估算 token，不生成 schema/card。

边界：

- 只校验 caller-provided JSON request body。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不调用 `asr_bakeoff.llm_smoke` 或 `load_llm_gateway_config`。
- 不检查 config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不返回 raw config path、absolute path、basename、parent directory 或 path-derived label；错误响应同样不得回显 URL path、file URL、control-character path 或重复字段里的敏感值。
- 不读取、存储、日志化、mask、hash、截取 prefix/suffix、计算长度或报告 `api_key` presence/validity/fingerprint。
- 不改变 Live ASR audit record，不追加 preflight event。
- 不发网络请求，不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_contract_without_reading_config -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_rejects_invalid_request_without_leaking_path_or_secret -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_contract_without_reading_config tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_accepts_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_returns_404_for_missing_session tests/test_app.py::test_asr_live_llm_provider_config_loader_preflight_endpoint_rejects_invalid_request_without_leaking_path_or_secret -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-056-live-asr-llm-provider-config-loader-preflight-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-056-live-asr-llm-provider-config-loader-preflight.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 provider config file reader 前，必须先设计授权 UI、secret storage adapter、读取审计、错误响应脱敏、日志脱敏、路径隐私策略和配置生命周期。
- 接入 masked status loader 前，必须定义哪些字段可展示、哪些字段永不展示，以及如何避免 key/path side-channel。
- 接入 enabled executor 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。

## DEC-057: Live ASR 增加 LLM provider secret storage policy 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-056` 已经定义 future config loader request 的预检合同，但真实读取 provider config 前仍缺少一个更基础的密钥存储边界：API key 不能被复制进 session JSON、Live ASR audit event、报告、日志、浏览器存储或仓库配置文件。后续如果直接实现本地配置读取，很容易把 keychain/env/企业 secret provider、masked status 和 enabled executor 混到一起。因此先增加 template-only secret storage policy，让 UI/API 先有可测的密钥引用策略。

决策：

新增 `PCWEB-057`，提供只读端点：

- `GET /live/asr/sessions/{session_id}/llm-provider-secret-storage-policy`

端点只从 Live ASR audit record 派生 `session_id`、`source` 和 `trace_kind`，并返回静态策略：

- `policy_kind=provider_secret_storage`
- `policy_status=template_only`
- `provider_protocol=openai_compatible_chat_completions`
- `config_source_status=not_read`
- `secret_storage_status=not_connected`
- `credentials_status=not_read`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`
- `safe_to_read_secret=false`

策略字段：

- `recommended_storage_order=os_keychain -> enterprise_secret_provider -> environment_variable_for_development_only`
- `allowed_secret_references=keychain_item_reference, enterprise_secret_reference, env_var_name_reference`
- `forbidden_storage_locations=repository_files, configs_local_plaintext_api_key, session_json, live_asr_audit_events, logs, reports, browser_local_storage`
- `required_loader_guards=explicit_user_authorization, path_privacy_redaction, secret_value_redaction, no_secret_in_error_response, no_secret_in_audit_event, no_secret_in_logs, no_secret_in_browser_storage`

原因：

- 真实 provider config loader 前必须先有密钥存储策略，否则后续很容易把密钥值、masked key、key presence 和调用状态混成一个不可审查的大增量。
- 明确当前阶段仍然不连接 keychain、不读取 env secret、不读取 config file、不判断 key 是否存在或有效。
- 继续保持默认无额外费用：不调用远程 ASR/LLM，不估算 token，不生成 schema/card。

边界：

- 只读 template policy。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不返回 raw、masked、hashed、prefix、suffix、fingerprint、length、presence 或 validity credential signal。
- 不改变 Live ASR audit record，不追加 policy event。
- 不发网络请求，不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_secret_storage_policy_endpoint_returns_template_without_reading_secrets -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_secret_storage_policy_endpoint_returns_template_without_reading_secrets tests/test_app.py::test_asr_live_llm_provider_secret_storage_policy_endpoint_accepts_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_secret_storage_policy_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_secret_storage_policy_endpoint_returns_404_for_missing_session tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-057-live-asr-llm-provider-secret-storage-policy-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-082：PCWEB-082 创建静态 Tauri shell scaffold

日期：2026-07-02

状态：Accepted

背景：

PCWEB-081 已经完成 native bridge command contract，并明确下一桌面增量必须进入 `create_tauri_shell_scaffold_against_bridge_contract`。继续做只读 preflight panel 会偏离真实 PC 客户端路径，但直接启用音频、worker、权限、打包和密钥读取又会一次性打开过多风险。

决策：

创建 `PCWEB-082` 静态 Tauri scaffold：`code/desktop_tauri/src-tauri` 包含 `Cargo.toml`、`build.rs`、`tauri.conf.json`、`capabilities/default.json`、`src/main.rs` 和 `src/lib.rs`。`tauri.conf.json` 使用 `devUrl=http://127.0.0.1:8765/`、`frontendDist=../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static`、空 `beforeDevCommand`/`beforeBuildCommand`、单一 `main` window 和 `bundle.active=false`。只绑定 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个 no-op command，分别映射 `runtime.get_status`、`session.prepare`、`asr_worker.health`，并返回 `noop_bound/noop_only/tauri_ipc_bound`、`safe_to_execute_real_action=false`、`captures_audio=false`、`spawns_process=false`、`calls_remote_provider=false`、`writes_local_files=false`。

原因：

- 真实桌面壳风险需要从文档和 Web preflight 推进到可审查源码。
- Tauri-first 仍是当前推荐客户端路径，Electron 保留为 fallback。
- 只接三个 no-op command 能验证 PCWEB-081 contract 到 native IPC handler 的最短链路，同时避免误开音频、权限、worker、密钥和付费调用。
- 不引入 `package.json`、`Cargo.lock` 或构建产物，可以避免环境膨胀和不可追踪依赖变更。

替代方案：

- 使用 Tauri CLI 初始化完整项目：Rejected，容易生成 Node frontend、lock files、bundle 默认配置或构建产物。
- 继续只做 Web/API preflight：Rejected，PCWEB-081 已经是最后一个 bridge/process preflight。
- 直接绑定 audio/worker commands：Rejected，权限 UX、audio adapter、worker lifecycle 和本地数据生命周期尚未独立验收。

边界：

- 不运行 `cargo build`、`cargo check`、`cargo tauri dev`、`npm install` 或任何 package manager。
- 不创建 `Cargo.lock`、`package.json`、npm/pnpm/yarn lock files、`node_modules`、`target`、installer/signing/notarization artifacts。
- 不绑定 `audio.permissions_status`、`audio.devices_list`、`audio.capture_start`、`audio.capture_stop` 或 `asr_worker.start`。
- 不请求或探测 macOS/Windows 权限，不枚举设备，不捕获 microphone/system audio。
- 不启动 native bridge 或 ASR worker，不访问 native audio/process API。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio/local data，不调用远程 ASR、LLM 或中转站。

验证方式：

- `tests/test_desktop_tauri_scaffold.py` 静态验证 scaffold 文件、Tauri config、capability、三条 no-op command、no-side-effect response fields 和 forbidden artifacts/snippets。
- `tests/test_quality_gate.py` 验证 `pc-web` 和 `all-local` 都包含 `root-pytest`，且不会运行 cargo/npm/Tauri CLI、LLM smoke 或 `configs/local`。
- `python3 tools/run_quality_gate.py --profile pc-web` 必须把 root scaffold contract tests 纳入本地免费质量门禁。

关联文档：

- `docs/pcweb-082-tauri-shell-scaffold-spike-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-082-tauri-shell-scaffold-spike.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-083：PCWEB-083 创建桌面 build readiness policy

日期：2026-07-02

状态：Accepted

背景：

PCWEB-082 已经创建静态 Tauri scaffold，但仍禁止 `cargo check`、Tauri dev/build、依赖安装和构建产物。下一步如果直接运行 cargo，可能生成 `Cargo.lock`、`target`、下载依赖或暴露缓存清理问题；如果继续不记录准入条件，后续开发容易在没有边界的情况下冒进。

决策：

新增 `code/desktop_tauri/build-readiness.policy.json` 和 `tools/desktop_build_readiness.py`。policy 明确 `policy_status=build_readiness_policy_only`、`toolchain_version_probe_only`、`safe_to_run_cargo_check_now=false`、`safe_to_run_tauri_dev_now=false`、`safe_to_run_tauri_build_now=false`、`safe_to_install_dependencies_now=false`、`safe_to_generate_lockfiles_now=false` 和 `safe_to_generate_build_artifacts_now=false`。工具默认只返回静态报告；显式 probe 模式只允许 `rustc --version` 和 `cargo --version`，不得运行 build/package commands。执行边界由 `tools/desktop_build_readiness.py` 的硬编码白名单强制执行，custom policy 不能扩大可执行探针范围；不在白名单内的命令返回 `returncode=126`，且不会传给 runner。

原因：

- 允许版本探测可以判断本机是否接近 Tauri build 前置条件，同时不生成 lock/build artifacts。
- 在运行 `cargo check` 前先记录 preconditions，可避免环境膨胀、依赖网络获取和不受控缓存。
- 继续维持 no-audio/no-worker/no-secret/no-paid-call 边界，避免把桌面构建验证和平台能力启用混在一起。

替代方案：

- 直接运行 `cargo check`：Rejected，当前尚未决定 `Cargo.lock`、`target`、网络依赖获取和清理策略。
- 直接运行 `cargo tauri dev`：Rejected，会进入运行时桌面壳和更复杂的权限/IPC/窗口生命周期。
- 不做 policy，继续只靠文档提醒：Rejected，后续很容易因为手动命令绕过质量门禁。

边界：

- 不运行 `cargo check`、`cargo build`、`cargo tauri dev`、`cargo tauri build`。
- 不运行 `npm install`、`npm ci`、`pnpm install`、`yarn install`、`npm run tauri dev/build`、`pnpm run tauri dev/build`、`yarn tauri dev/build`、`npx tauri dev/build` 或 frontend package manager / Tauri launcher。
- 不生成 `Cargo.lock`、dependency lock files、`node_modules`、`target`、dist、bundle、installer、签名或公证产物。
- 不绑定 audio commands，不请求权限、不枚举设备、不捕获 microphone/system audio。
- 不启动 ASR worker，不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

未来启用 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 前必须满足：

- `explicit_user_approval_for_build_artifacts`
- `cargo_lock_policy_decided`
- `target_dir_policy_decided`
- `network_dependency_fetch_policy_decided`
- `cache_cleanup_policy_decided`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

验证方式：

- `tests/test_desktop_build_readiness_policy.py` 证明 policy 和工具默认 no-build，显式 `toolchain_version_probe_only` 只运行版本命令，并保持 `safe_to_run_cargo_check_now=false`。
- post-review regression 证明 custom policy 即使把 `cargo check` 写进 `allowed_probe_commands`，runner 也不会执行它；报告会返回 blocked probe result。
- tests 证明 forbidden side effects 被 policy 和 readiness report 同步保留，禁止命令覆盖常见 npm/pnpm/yarn/npx Tauri launcher 入口。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 cargo/npm/Tauri CLI 或读取 `configs/local`。

关联文档：

- `docs/pcweb-083-desktop-build-readiness-policy-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-083-desktop-build-readiness-policy.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-084：PCWEB-084 创建桌面 cargo check artifact policy

日期：2026-07-02

状态：Accepted

背景：

PCWEB-083 已经允许只读 readiness report 和 `rustc/cargo` 版本探针，但未来第一次 `cargo check` 仍缺少明确的 artifact policy。直接运行 Cargo 可能同时生成 `Cargo.lock`、写入 `target`、下载 crates、膨胀缓存并引入清理风险；如果这些边界不先机器化，后续很容易把构建验证和真实运行/打包/权限能力混在一起。

决策：

新增 `code/desktop_tauri/cargo-check.policy.json` 和 `tools/desktop_cargo_check_policy.py`。policy 明确 `policy_status=cargo_check_artifact_policy_only`、`safe_to_run_cargo_check_now=false`、`safe_to_install_toolchain_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。未来第一次获批命令为 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`，环境必须包含 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`；未来 repeat check 为 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml --locked --offline`，同样使用该 target dir。首次获批 dependency-resolution run 可以生成 `code/desktop_tauri/src-tauri/Cargo.lock`，生成后应作为桌面 app 的可复现 artifact 提交；当前 PCWEB-084 不生成该文件。

原因：

- 对 desktop app 而言，`Cargo.lock` 是可复现构建的重要输入；但它应在首次获批依赖解析时生成，而不是被静态 scaffold 或 readiness report 偷偷创建。
- `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target` 把 Cargo build output 移出 `src-tauri/target`，并放在已忽略的临时 artifacts 区域，便于清理且避免污染源码树。
- 默认不联网抓依赖，避免把“本地免费 no-run policy”变成不可控的依赖下载动作；等 lock/cache 存在后 repeat check 应优先使用 `--locked --offline`。
- `desktop_cargo_check_policy.py` 只读 policy 和 artifact path existence，并做 schema validation，不运行 subprocess、不读取 `configs/local/` 或 secret。

替代方案：

- 直接运行 `cargo check`：Rejected，当前机器 Rust 工具链不可用，且尚未获得真实构建产物/网络依赖解析的显式执行批准。
- 把 `target` 写到 `code/desktop_tauri/src-tauri/target`：Rejected，会污染源码目录并增加误提交风险。
- 永久禁止 `Cargo.lock`：Rejected，桌面 app 不是 library-only crate，未来可执行产物需要 lockfile 支持可复现性。
- 立即使用 `--locked --offline` 作为首次检查：Rejected，当前尚无 `Cargo.lock` 和依赖缓存，首次会失败且不能完成依赖解析。

边界：

- 不运行 `cargo check`、`cargo build`、`cargo tauri dev`、`cargo tauri build`。
- 不安装 Rust、Tauri CLI、npm/pnpm/yarn/npx dependencies 或系统包。
- 不联网抓 crates，不生成 `Cargo.lock`，不创建 `artifacts/tmp/desktop_tauri_target` 或 `src-tauri/target`。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

未来启用第一次真实 `cargo check` 前必须满足：

- `explicit_user_approval_for_first_cargo_check`
- `rust_toolchain_available`
- `first_dependency_resolution_network_fetch_approved_or_cache_preseeded`
- `cargo_lock_policy_acknowledged`
- `cargo_target_dir_policy_acknowledged`
- `cleanup_policy_acknowledged`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

验证方式：

- `tests/test_desktop_cargo_check_artifact_policy.py` 证明 policy 和工具默认 no-run、future command/env、Cargo.lock/target/network/cleanup/side-effect boundary、read-only artifact scan 和 malformed policy validation。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 cargo/npm/pnpm/yarn/npx/Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-084 已落到 README、traceability、acceptance、privacy/data-flow、project structure、roadmap 和 decision log。

关联文档：

- `docs/pcweb-084-desktop-cargo-check-artifact-policy-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-084-desktop-cargo-check-artifact-policy.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-085：PCWEB-085 创建桌面 Rust toolchain readiness policy

日期：2026-07-02

状态：Accepted

背景：

PCWEB-084 已经决定未来第一次 `cargo check` 的 `Cargo.lock`、`CARGO_TARGET_DIR`、network fetch 和 cleanup policy，但当前机器是否具备 Rust/Tauri 前置工具链仍需要可追溯判断。直接安装 Rust、修改 shell profile 或运行 `cargo check` 会引入环境变更、依赖下载和构建产物；如果继续只凭人工记忆判断工具链，又容易让下一步真实构建不可复现。

决策：

新增 `code/desktop_tauri/rust-toolchain-readiness.policy.json` 和 `tools/desktop_rust_toolchain_readiness.py`。policy 明确 `policy_status=rust_toolchain_readiness_policy_only`、`toolchain_probe_mode=local_version_and_platform_probe_only`、`safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。工具默认不执行外部命令；显式 probe 模式只允许 `rustc --version`、`cargo --version`、`rustup --version` 和 `xcode-select -p`，并由工具层硬编码白名单强制执行。`xcode-select -p` 只返回 `path_present`/`path_missing` 和脱敏 stdout/stderr，不返回本机开发者工具路径。审查后进一步决定：自定义 policy 不能把任何 PCWEB-085 安装、shell profile、Cargo、Tauri、依赖抓取、Cargo.lock、target 或 `configs/local` safety flag 放宽为 true；report 输出也强制保持这些安全开关为 false。

原因：

- `rustc` 和 `cargo` 是未来第一次 `cargo check` 的硬前置；`rustup` 是推荐的工具链管理方式，但不是唯一可能来源。
- macOS 上 Tauri/Rust 构建还依赖平台开发工具，先用 `xcode-select -p` 做 presence-only 探针可以避免暴露本机路径。
- 保持默认 no-command，让文档/测试/质量门禁仍然是零安装、零构建、零额外费用。
- 显式 probe 只做版本/平台 readiness，不下载依赖、不修改 shell profile、不生成 artifact。

替代方案：

- 直接运行 rustup 安装：Rejected，会修改用户环境和 shell profile，且不是当前阶段必须。
- 直接运行 `cargo check`：Rejected，仍需显式批准真实构建和依赖解析，且本阶段只验证工具链 readiness。
- 只保留 PCWEB-083 的 `rustc/cargo` 版本探针：Rejected，缺少 rustup/toolchain management warning、macOS 平台 prerequisite 和 xcode path 脱敏边界。
- 返回 `xcode-select -p` 原始路径：Rejected，本地路径不应出现在通用 readiness report 中。

边界：

- 不安装 Rust、rustup、Xcode Command Line Tools、Visual Studio Build Tools、Tauri CLI、npm/pnpm/yarn packages 或系统包。
- 不运行 `cargo check`、`cargo build`、`cargo tauri dev`、`cargo tauri build`、`rustup update`、`rustup toolchain install`、install scripts、package managers 或 shell-profile modification commands。
- 不生成 `Cargo.lock`、target output、dependency caches、node modules、dist、bundle、installer、签名或公证产物。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

未来启用第一次真实 `cargo check` 前必须满足：

- `explicit_user_approval_for_first_cargo_check`
- `rustc_available`
- `cargo_available`
- `macos_command_line_tools_available_or_non_macos_equivalent`
- `pcweb_084_artifact_policy_acknowledged`
- `first_dependency_resolution_network_fetch_approved_or_cache_preseeded`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

验证方式：

- `tests/test_desktop_rust_toolchain_readiness.py` 证明 policy 和工具默认 no-command，显式 `local_version_and_platform_probe_only` 只运行四条 allowlisted probe，custom policy 不能扩大白名单或放宽 safety flags，`xcode-select -p` stdout/stderr 路径会脱敏，missing executable 无 traceback。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 cargo/npm/pnpm/yarn/npx/Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-085 已落到 README、traceability、acceptance、privacy/data-flow、project structure、roadmap 和 decision log。

关联文档：

- `docs/pcweb-085-desktop-rust-toolchain-readiness-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-085-desktop-rust-toolchain-readiness.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-088：PCWEB-088 创建桌面 Rust post-install probe approval policy

日期：2026-07-02

状态：Accepted

背景：

PCWEB-087 已经把 Rust 安装说明固定为 `manual_user_run_only` approval packet。下一步如果用户在本 repo 外人工安装了 Rust，需要确认本 repo 允许做哪些只读 post-install probe、哪些输出必须脱敏、哪些条件仍然阻止 `cargo check`。如果没有这个边界，容易把“用户已经安装 Rust”误解成“可以立即运行 rustc/cargo/rustup/xcode-select 或 cargo check”。因此需要先把 probe allowlist 和 no-cargo-check boundary 固化为静态审批包。

决策：

新增 `code/desktop_tauri/rust-post-install-probe-approval.policy.json` 和 `tools/desktop_rust_post_install_probe_approval.py`。policy 明确 `probe_approval_mode=no_probe_execution_approval_packet_only`、`probe_execution_status=not_run`、`external_command_execution_status=not_run`、`cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。工具默认只读取 policy 并输出 static approval packet，不运行 `rustc --version`、`cargo --version`、`rustup --version`、`xcode-select -p`、cargo check、Tauri、package manager 或 shell command；custom `--policy` path 必须在读文件前拒绝 `configs/local`、local runtime、outputs、temporary artifact 和 audio sample roots，包括 repo 外部但路径 parts 命中这些敏感 roots 的情况。PCWEB-088 允许记录 future probe command allowlist，但这些命令文本仍只是审批材料，不是可执行动作。评审加固后，forbidden path component matching 使用大小写不敏感比较，避免 macOS 风格 `CONFIGS/LOCAL` 等路径绕过；`forbidden_default_side_effects` 也固定为工具侧 canonical list，custom policy 不能删除或替换，invalid/blocked report 会返回可信 canonical list 而不是回显不可信 custom policy。最终复审后，invalid custom policy 也不能控制报告顶层 `pcweb_id`、`policy_name` 或 `policy_status`；这些字段由工具侧 canonical constants 输出，`policy_name` 也进入 validation。

原因：

- 安装完成后的版本探测看似只读，但仍会执行本机二进制、可能回显本机路径或暴露 PATH/shell/cargo/rustup 状态，必须单独授权。
- `xcode-select -p` 输出本机路径，未来即使执行也必须使用 `presence_only_no_path`，不能回显路径。
- `cargo check` 会触发依赖解析、network/cache、`Cargo.lock` 和 target artifacts，不能因为版本 probe 可用而自动启用。
- 保持默认零额外费用、零构建产物、零密钥读取、零音频触达和零远程 provider 调用。

替代方案：

- 复用 PCWEB-085 probe 直接运行版本命令：Rejected，PCWEB-085 是 readiness/probe 边界，不代表用户人工安装后的 post-install probe 已获批准。
- 安装后直接运行 `cargo check`：Rejected，仍缺 PCWEB-084 artifact policy re-acknowledgement、network/cache policy、target dir policy、lockfile policy 和 explicit first cargo check approval。
- 把 `xcode-select -p` 原始输出作为调试信息保存：Rejected，会泄露本机 developer tools path。
- 只在 README 里口头约定：Rejected，后续工具和测试需要机器可验证的 policy/report contract。

边界：

- 不运行 `rustc`、`cargo`、`rustup`、`xcode-select`、`curl`、`sh`、`rustup-init`、`brew`、`xcode-select --install`、Visual Studio installer、WebView2 installer、apt/dnf/yum、winget/scoop/choco、npm/pnpm/yarn/npx、Tauri CLI 或 shell commands。
- 不安装、更新、卸载、修复、probe 或 validate Rust、rustup、Cargo、Xcode、Xcode Command Line Tools、Visual Studio Build Tools、WebView2、Linux system packages、Tauri CLI、Node 或 frontend dependencies。
- 不读取 PATH、shell profiles、cargo home、rustup home、package-manager state、dependency caches、registry、keychain、credential stores、system settings、`Cargo.lock` 或 target output。
- 不生成 `Cargo.lock`、target output、dependency caches、`node_modules`、dist、bundle、installer、签名、公证、update、app-store 或 mobile artifacts。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

未来启用真实 post-install probe 前必须满足：

- `explicit_user_approval_for_post_install_probe`
- `rust_toolchain_install_completed_by_user`
- `approved_post_install_probe_command_allowlist`
- `approved_probe_output_redaction_policy`
- `approved_no_cargo_check_boundary_reconfirmed`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

未来即使 post-install probe 显示 toolchain 可用，第一次 `cargo check` 仍必须等待：

- `pcweb_084_artifact_policy_reacknowledged`
- `first_dependency_resolution_network_or_cache_policy_approved`
- `cargo_target_dir_artifact_tmp_approved`
- `cargo_lock_generation_commit_policy_approved`
- `explicit_user_approval_for_first_cargo_check`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

验证方式：

- `tests/test_desktop_rust_post_install_probe_approval.py` 证明 policy 和工具默认 `no_probe_execution_approval_packet_only`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false`，custom policy 不能添加 probe commands、放宽 safety flags、删除 approval tokens、移除 redaction requirements、移除 forbidden side effects、覆盖顶层 report identity/status 或把 cargo check 标记为 ready，custom policy path 不能读取 `configs/local` 等敏感 roots，且 mixed-case 禁区路径也会在读文件前被拒绝，工具源不包含命令执行入口。
- 2026-07-02 post-review hardening 验证：`python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py -q` (`8 passed`)、`python3 -m pytest tests/test_desktop_rust_post_install_probe_approval.py tests/test_quality_gate.py -q` (`15 passed`)、full desktop/root regression (`61 passed`)、Web README docs gate (`1 passed`)、`python3 tools/run_quality_gate.py --profile pc-web` 通过，以及 `python3 tools/run_quality_gate.py --profile all-local --no-browser` 通过。最终复审 identity/status 加固也按 RED/GREEN 验证：先出现 `1 failed, 7 passed`，修复后 `8 passed`。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 rustc、rustup、cargo、xcode-select、npm/pnpm/yarn/npx、Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-088 已落到 README、traceability、acceptance、privacy/data-flow、project structure、roadmap 和 decision log。

关联文档：

- `docs/pcweb-088-desktop-rust-post-install-probe-approval-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-088-desktop-rust-post-install-probe-approval.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-089：PCWEB-089 创建桌面 Rust post-install probe result intake policy

日期：2026-07-02

状态：Accepted

背景：

PCWEB-088 只定义了未来 post-install probe 的审批包和 expected result schema，没有执行 probe，也没有定义如何安全摄入 probe status。下一步如果用户手动提供或未来已批准探针生成 bounded status，需要一个不执行命令、不接受 raw output/path/env/home/cache/secret 的 result intake 边界。否则很容易把 `stdout`、本机 Xcode path、PATH、cargo/rustup home、provider config 或 API key 一起写进报告，或者把 toolchain available 误解成可以运行 `cargo check`。

决策：

新增 `code/desktop_tauri/rust-post-install-probe-result-intake.policy.json` 和 `tools/desktop_rust_post_install_probe_result_intake.py`。policy 明确 `result_intake_mode=manual_result_validation_only`、`accepted_result_source=caller_provided_json_only`、`probe_execution_status=not_run`、`external_command_execution_status=not_run`、`cargo_check_readiness=blocked_until_pcweb_084_and_user_approval`、`safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false` 和 `safe_to_run_cargo_check_now=false`。工具默认只读取 policy 和可选 caller-provided bounded JSON result，不运行 `rustc`、`cargo`、`rustup`、`xcode-select`、cargo check、Tauri、package manager 或 shell command；custom `--policy` 和 `--result-json` path 必须在读文件前拒绝 `configs/local`、local runtime、outputs、temporary artifact 和 audio sample roots，包括 repo 外部但路径 parts 命中这些敏感 roots 的情况。invalid custom policy 不能控制报告顶层 `pcweb_id`、`policy_name`、`policy_status`、allowed result fields、status enums、forbidden raw fields 或 safety flags；这些字段由工具侧 canonical constants 输出。

原因：

- result intake 需要和 probe execution 分离；`PCWEB-089` 只摄入 caller-provided status，不执行本机二进制。
- raw stdout/stderr、command text、local path、env、cargo home、rustup home、dependency cache、provider config、api_key、authorization、bearer token 等都可能泄露本机或密钥信息。
- 即使 result 显示 `rustc_status=available`、`cargo_status=available`、`rustup_status=available`，第一次 `cargo check` 仍会触发依赖、network/cache、`Cargo.lock` 和 target artifacts，必须继续等待 PCWEB-084 和显式用户批准。
- 保持默认零额外费用、零构建产物、零密钥读取、零音频触达和零远程 provider 调用。

替代方案：

- 在 PCWEB-088 approval packet 中直接接受 raw probe output：Rejected，会把审批材料和不可信输入混在一起。
- 直接运行 `rustc --version` 等 probe 并解析输出：Rejected，真实 probe execution 必须另起显式授权增量。
- result 显示工具链可用后自动解锁 `cargo check`：Rejected，仍缺 PCWEB-084 artifact policy re-acknowledgement、network/cache policy、target dir policy、lockfile policy 和 explicit first cargo check approval。
- 只在 README 里说明如何填写 status：Rejected，缺少机器可验证的 schema、path guard 和 custom policy hardening。

边界：

- 不运行 `rustc`、`cargo`、`rustup`、`xcode-select`、`curl`、`sh`、`rustup-init`、`brew`、Visual Studio installer、WebView2 installer、apt/dnf/yum、winget/scoop/choco、npm/pnpm/yarn/npx、Tauri CLI 或 shell commands。
- 不解析或保存 raw stdout/stderr，不接受 command text、executable path、developer tools path、PATH、env、cwd、shell profile、cargo home、rustup home、dependency cache、target dir、Cargo.lock path、provider config、api_key、Authorization header 或 bearer token。
- 不生成 `Cargo.lock`、target output、dependency caches、`node_modules`、dist、bundle、installer、签名、公证、update、app-store 或 mobile artifacts。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不读取 provider config、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

验证方式：

- `tests/test_desktop_rust_post_install_probe_result_intake.py` 证明 policy 和工具默认 `manual_result_validation_only`、`caller_provided_json_only`、`safe_to_accept_raw_probe_output_now=false`、`safe_to_run_post_install_probe_now=false`、`safe_to_run_cargo_check_now=false`，valid caller-provided result 会被 normalized 但 cargo check 仍保持 `blocked_until_pcweb_084_and_user_approval`，raw output/path/command/secret 字段、unknown fields、invalid enum 和 invalid Xcode not-applicable combination 会被拒绝。
- root tests 证明 custom policy 不能放宽 schema、status enums、forbidden raw fields、safety flags 或顶层 report identity/status；policy/result path 不能读取 `configs/local` 等敏感 roots，且 mixed-case 禁区路径也会在读文件前被拒绝，工具源不包含命令执行入口。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 rustc、rustup、cargo、xcode-select、npm/pnpm/yarn/npx、Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-089 已落到 README、desktop README、traceability、acceptance、privacy/data-flow、project structure、roadmap 和 decision log。

关联文档：

- `docs/pcweb-089-desktop-rust-post-install-probe-result-intake-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-089-desktop-rust-post-install-probe-result-intake.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `code/desktop_tauri/README.md`
- `README.md`

## DEC-090：PCWEB-090 创建桌面 first cargo check execution boundary

日期：2026-07-02

状态：Accepted

背景：

PCWEB-084 已定义未来首次 `cargo check` 的 command、`CARGO_TARGET_DIR`、`Cargo.lock`、target、network fetch 和 cleanup policy。PCWEB-089 已定义如何安全摄入 bounded toolchain status，但它刻意不解锁 Cargo。下一步需要把这两类证据合并成一个可审计的 first cargo check execution boundary：证明什么时候可以生成手动执行包，同时继续防止工具自动运行 Cargo、联网抓依赖或生成 artifacts。

决策：

新增 `code/desktop_tauri/first-cargo-check-execution.policy.json` 和 `tools/desktop_first_cargo_check_execution_boundary.py`。policy 明确 `execution_boundary_mode=explicit_manual_execution_packet_only`、`accepted_artifact_policy_source=pcweb_084_cargo_check_policy_only`、`accepted_toolchain_result_source=pcweb_089_normalized_result_only`、`cargo_check_execution_status=not_run`、`external_command_execution_status=not_run` 和 `approval_status=explicit_user_approval_not_recorded`。工具默认只读取 PCWEB-090 policy、PCWEB-084 cargo-check artifact policy 和可选 PCWEB-089 bounded result；当 artifact policy valid 且 bounded result 显示 `rustc_status/cargo_status/rustup_status=available` 时，最多返回 `execution_packet_status=ready_for_explicit_user_approval` 和手动执行包，command 固定为 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`，env 固定为 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`。

原因：

- 这一步把 PCWEB-084 与 PCWEB-089 串起来，减少后续真实 `cargo check` 前的人工口径漂移。
- 首次 `cargo check` 仍可能触发依赖解析、网络/cache、`Cargo.lock` 和 target output，不能因为工具链 status 可用就自动执行。
- 手动执行包比 README 文字更可测试：command、env、allowed artifacts、preconditions 和禁用 flags 都进入 root pytest。
- 保持默认零构建产物、零依赖下载、零密钥读取、零音频触达和零远程 provider 调用。

替代方案：

- 直接在工具里运行 `cargo check`：Rejected，当前仍缺显式执行授权，并且会产生依赖下载、lockfile 和 target output 风险。
- 让 PCWEB-089 的 valid result 自动设置 `safe_to_run_cargo_check_now=true`：Rejected，会把工具链可用和构建/依赖/产物批准混为一谈。
- 只在文档里写下一条命令：Rejected，无法通过测试防止 command/env/artifact/precondition 漂移。

边界：

- 不运行 `cargo check`、`cargo build`、Tauri dev/build、package manager、npm/pnpm/yarn/npx、shell command 或 dependency fetch。
- 不生成 `Cargo.lock`、target output、dependency cache、`node_modules`、dist、bundle、installer、签名、公证、update、app-store 或 mobile artifacts。
- 不读取 PATH、shell profile、cargo home、rustup home、raw probe output、provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。
- custom PCWEB-090 policy 不能放宽 identity、command、env、artifacts、preconditions 或 safety flags；custom PCWEB-084 policy drift 会阻塞手动执行包。

验证方式：

- `tests/test_desktop_first_cargo_check_execution_boundary.py` 证明 policy 和工具默认 `explicit_manual_execution_packet_only`、`safe_to_run_cargo_check_now=false`、valid PCWEB-089 result 只生成 `ready_for_explicit_user_approval` 手动包，missing/invalid toolchain result 会阻塞，raw/path/secret-like values 不回显，custom PCWEB-090 policy drift、custom PCWEB-084 artifact policy drift 和 forbidden paths 都会阻塞。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 cargo、rustup、rustc、xcode-select、npm/pnpm/yarn/npx、Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-090 已落到 README、desktop README、traceability、acceptance、privacy/data-flow、project structure、roadmap、decision log、current status 和 progress report。

关联文档：

- `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-090-desktop-first-cargo-check-execution-boundary.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/project-current-status-2026-07-02.md`
- `docs/project-progress-report-2026-07-02.md`
- `code/web_mvp/README.md`
- `code/desktop_tauri/README.md`
- `README.md`

## DEC-087：PCWEB-087 创建桌面 Rust toolchain install approval packet

日期：2026-07-02

状态：Accepted

背景：

PCWEB-086 已经确认当前不能自动安装 Rust，只能记录 installation decision。下一步如果直接把官方安装命令交给自动化执行，会修改用户环境、下载网络资源、改动 shell/PATH 或引入 Cargo/rustup cache；如果完全不记录具体安装说明，后续用户审批时又缺少可复核材料。因此需要新增一个只给人审的 approval packet，把官方来源、平台差异、风险、回滚和 post-install verification order 固化，但仍保持 no-install/no-command。

决策：

新增 `code/desktop_tauri/rust-toolchain-install-approval.policy.json` 和 `tools/desktop_rust_toolchain_install_approval_packet.py`。policy 明确 `approval_packet_mode=manual_user_run_only`、`manual_instruction_text_status=inert_text_only`、`recommended_install_provider=official_rustup`、`safe_to_execute_install_now=false`、`safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false`、`safe_to_run_rustup_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。工具默认只读取 policy 并输出 static approval packet，不执行 manual command text，不导入命令执行入口，不读取密钥或 `configs/local/`；custom `--policy` path 必须在读文件前拒绝 `configs/local`、local runtime、outputs、temporary artifact 和 audio sample roots，包括 repo 外部但路径 parts 命中这些敏感 roots 的情况。PCWEB-087 允许保存 macOS/Linux 官方 rustup shell command 和 Windows `rustup-init.exe` 说明，但这些只能作为 inert text 出现在报告中；如果 custom policy validation 失败，报告不得回显不可信 custom packet sections，而应保留 canonical official sources/manual instruction text 或空的非关键 sections，同时保持 `approval_packet_status=blocked_by_policy_validation`。

原因：

- 安装 Rust 是用户环境变更，不应由“按推荐做”的一般开发授权隐式触发。
- 用户后续如果批准安装，需要看到官方来源、平台差异、PATH/shell profile 风险、网络下载风险和 rollback/verification 计划。
- 记录 manual command text 能减少口口相传和临时复制错误，但必须把 command text 与执行能力彻底分离。
- 仍保持默认零额外费用、零构建产物、零密钥读取、零音频触达和零远程 provider 调用。

替代方案：

- 自动运行官方 rustup 命令：Rejected，会下载并修改用户环境，且未获得专门 installation approval。
- 把命令写进 README 后由质量门禁执行：Rejected，质量门禁必须继续是 no-install/no-build。
- 只链接官方文档，不记录审批 tokens 和风险：Rejected，后续容易绕过 shell/PATH、rollback、post-install probe 和 no-audio/no-secret/no-remote 边界。
- 使用 Homebrew/winget/choco/scoop/apt 作为默认安装路径：Rejected，会引入额外 package-manager state，当前推荐仍是官方 rustup，package-manager 命令最多作为未来手动讨论文本。

边界：

- 不执行 manual instruction text，不运行 `curl`、`sh`、`rustup-init`、`rustup`、`cargo`、`brew`、`xcode-select --install`、Visual Studio installer、WebView2 installer、apt/dnf/yum、winget/scoop/choco、npm/pnpm/yarn/npx、Tauri CLI 或 shell commands。
- 不安装、更新、卸载或修复 Rust、rustup、Cargo、Xcode Command Line Tools、Visual Studio Build Tools、WebView2、Linux system packages、Tauri CLI、Node 或 frontend dependencies。
- 不修改 `.zshrc`、`.bashrc`、`.bash_profile`、`.profile`、PATH、cargo home、rustup home、login shell settings、registry、keychain、credential stores、system settings 或 package-manager state。
- 不生成 `Cargo.lock`、target output、dependency caches、`node_modules`、dist、bundle、installer、签名、公证、update、app-store 或 mobile artifacts。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

未来启用真实 Rust toolchain install 前必须满足：

- `explicit_user_approval_for_rust_toolchain_install`
- `approved_install_provider_official_rustup`
- `approved_shell_profile_modification_policy`
- `approved_network_download_policy_for_rustup`
- `approved_post_install_probe_policy`
- `no_audio_worker_secret_remote_boundary_reconfirmed`
- `approved_manual_user_run_only_boundary`
- `approved_rustup_uninstall_or_rollback_understanding`

验证方式：

- `tests/test_desktop_rust_toolchain_install_approval_packet.py` 证明 policy 和工具默认 `manual_user_run_only`、manual text inert、`safe_to_execute_install_now=false`，custom policy 不能放宽 safety flags、删除 approval tokens、移除 official sources 或切换到 executable mode，custom policy path 不能读取 `configs/local` 等敏感 roots，工具源不包含命令执行入口。
- Post-review hardening 证明 custom policy path guard 对 repo 内/外 forbidden roots 都在读文件前生效，且 invalid custom policy 不会让报告丢失 canonical official URLs 或展示 executable manual instruction boundary。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 cargo/npm/pnpm/yarn/npx/Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-087 已落到 README、traceability、acceptance、privacy/data-flow、project structure、roadmap 和 decision log。

关联文档：

- `docs/pcweb-087-desktop-rust-toolchain-install-approval-packet-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-087-desktop-rust-toolchain-install-approval-packet.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-086：PCWEB-086 创建桌面 Rust toolchain installation decision policy

日期：2026-07-02

状态：Accepted

背景：

PCWEB-085 的本地显式 probe 已经证明当前机器缺少 `rustc`、`cargo` 和 `rustup`，但 macOS Command Line Tools 可用。下一步如果直接安装 Rust，会修改用户环境、可能下载网络资源、修改 shell profile 或产生 toolchain/cache 状态；如果继续跳过安装决策，又无法进入未来第一次受控 `cargo check`。因此需要先把安装边界、批准条件和后续验证顺序固定下来。

决策：

新增 `code/desktop_tauri/rust-toolchain-installation.policy.json` 和 `tools/desktop_rust_toolchain_installation_decision.py`。policy 明确 `policy_status=rust_toolchain_installation_decision_policy_only`、`installation_decision_mode=no_install_decision_report_only`、`recommended_install_provider=official_rustup`、`safe_to_install_toolchain_now=false`、`safe_to_modify_shell_profile_now=false`、`safe_to_run_install_command_now=false`、`safe_to_run_rustup_now=false`、`safe_to_run_cargo_check_now=false`、`safe_to_fetch_dependencies_now=false`、`safe_to_generate_cargo_lock_now=false` 和 `safe_to_generate_target_dir_now=false`。工具默认只读取 policy，不执行任何命令，不导入 `subprocess`，不运行 curl/sh/rustup/cargo/package manager，不读取密钥或 `configs/local/`；custom `--policy` path 也必须在读文件前拒绝 `configs/local`、local runtime、outputs、temporary artifact 和 audio sample roots。未来任何 Rust 安装都必须另有显式批准，且批准需要覆盖官方 rustup provider、shell profile modification policy、network download policy、post-install probe policy 和 no-audio/no-secret/no-remote boundary 复核。

原因：

- 当前本机确实缺少 Rust 工具链，但安装工具链属于用户环境变更，不应由普通开发增量隐式触发。
- 官方 rustup 是跨平台 Rust 工具链推荐管理路径，但是否允许它下载/修改 shell profile 需要单独批准。
- PCWEB-084 已决定 future cargo check 的 `CARGO_TARGET_DIR` 和 lockfile policy；PCWEB-086 只解决安装批准边界，不打开 cargo check。
- 保持默认零额外费用、零构建产物、零密钥读取和零远程 provider 调用，符合当前 SDD/TDD 阶段约束。

替代方案：

- 直接运行 rustup install：Rejected，会修改用户环境和网络状态，且没有单独 approval token。
- 直接运行 `cargo check`：Rejected，当前缺少 `cargo`，且会引入依赖解析、`Cargo.lock` 和 target artifact。
- 通过 Homebrew 或系统 package manager 自动安装 Rust：Rejected，会引入额外 package-manager state，且不是本阶段需要。
- 不记录安装路径，只继续做 Web/mock：Rejected，会让桌面路径在工具链缺失处长期悬空。

边界：

- 不运行 `curl`、`sh`、`rustup-init`、`rustup`、`cargo`、`brew`、`xcode-select --install`、Visual Studio installer、apt/dnf/yum、winget/scoop/choco、npm/pnpm/yarn/npx、Tauri CLI 或 shell commands。
- 不安装 Rust、rustup、Cargo、Xcode Command Line Tools、Visual Studio Build Tools、system packages、Tauri CLI、npm packages 或 frontend dependencies。
- 不修改 `.zshrc`、`.bashrc`、`.bash_profile`、`.profile`、PATH、cargo home、rustup home、shell startup files、launch agents、registry、keychain、credential stores 或 system settings。
- 不生成 `Cargo.lock`、target output、dependency caches、node modules、dist、bundle、installer、签名、公证、update 或 app-store artifacts。
- 不请求权限、不枚举设备、不捕获 microphone/system audio、不启动 ASR worker。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 runtime/session/audio data，不调用远程 ASR、LLM 或中转站。

未来启用真实 Rust toolchain install 前必须满足：

- `explicit_user_approval_for_rust_toolchain_install`
- `approved_install_provider_official_rustup`
- `approved_shell_profile_modification_policy`
- `approved_network_download_policy_for_rustup`
- `approved_post_install_probe_policy`
- `no_audio_worker_secret_remote_boundary_reconfirmed`

验证方式：

- `tests/test_desktop_rust_toolchain_installation_decision.py` 证明 policy 和工具默认 no-command、no-install，custom policy 不能放宽 safety flags 或删除 approval tokens，custom policy path 不能读取 `configs/local` 等敏感 roots，工具源不包含命令执行入口。
- `tests/test_quality_gate.py` 继续证明默认 quality gate 不运行 cargo/npm/pnpm/yarn/npx/Tauri CLI 或读取 `configs/local`。
- Web README docs gate 证明 PCWEB-086 已落到 README、traceability、acceptance、privacy/data-flow、project structure、roadmap 和 decision log。

关联文档：

- `docs/pcweb-086-desktop-rust-toolchain-installation-decision-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-086-desktop-rust-toolchain-installation-decision.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `code/web_mvp/README.md`
- `README.md`

## DEC-080：Desktop runtime decision boundary

日期：2026-07-02

决策：

新增 `PCWEB-080`，在创建真实 desktop shell 前先提供一个 no-dependency/no-runtime/no-process/no-capture/no-paid-call 的 runtime decision boundary。后端新增：

- `GET /desktop/runtime-boundary`

Web 工作台新增：

- `desktop-runtime-boundary-panel`

响应锁定当前推荐但未创建的运行时和进程模型：

- `desktop_runtime_mode=decision_preflight_only`
- `desktop_runtime_boundary_status=blocked_before_runtime_creation`
- `recommended_desktop_runtime=tauri_first_electron_fallback`
- `desktop_runtime_decision_status=recommended_not_created`
- `desktop_process_model_status=planned_not_started`
- `ui_reuse_status=web_mvp_static_assets_reusable`
- `core_isolation_status=platform_independent`
- `native_bridge_status=not_created`
- `asr_worker_process_model=sidecar_worker_planned`
- `macos_target_status=apple_silicon_first`
- `windows_target_status=deferred_adapter`
- `desktop_runtime_phase_count=8`
- `desktop_runtime_safe_to_create_shell=false`
- `desktop_runtime_safe_to_start_native_bridge=false`
- `desktop_runtime_safe_to_spawn_worker=false`
- `desktop_runtime_safe_to_package_installer=false`
- `desktop_runtime_safe_to_request_permissions=false`
- `desktop_runtime_safe_to_capture_audio=false`
- `desktop_runtime_safe_to_call_remote_asr=false`
- `desktop_runtime_safe_to_call_llm=false`

原因：

- PCWEB-079 已暴露“选择 desktop shell runtime 和 process model”作为下一决策；PCWEB-080 把该决策展开成可测试、可审计的边界。
- 当前最重要的产品价值仍是实时状态和建议卡片；直接创建 Tauri/Electron 客户端会提前引入权限、打包、签名、更新、sidecar worker 和音频采集复杂度。
- Tauri-first 适合复用当前 Web UI，且桌面壳较轻；Electron 保留为 native bridge、packaging、updater 或音频采集受阻时的 fallback。
- ASR 依赖重，必须作为 sidecar worker 或独立 worker process 规划，不能塞进 UI 进程。
- core 必须保持平台无关，desktop 只作为 shell + platform adapter。

替代方案：

- 立即创建 Tauri 项目和 Rust/Node 依赖：不采用。本增量只锁决策边界，避免依赖和构建复杂度提前进入仓库。
- 直接创建 Electron 客户端：不采用。Electron 只作为 fallback，不作为当前首选实现。
- 直接 fork 现成会议转写项目作为客户端：不采用。开源项目可继续作为参考或局部能力来源，但当前核心价值链已经在本地 Web MVP 中被拆成可测合同，直接 fork 容易把产品退化成转写工具。
- 把 runtime boundary 做成“开始采集/请求权限”的设置向导：不采用。PCWEB-080 只展示状态和 next decisions，不执行任何系统动作。

边界：

- 不创建 Tauri/Electron 项目文件、依赖锁文件、desktop package、installer、签名、公证或上架产物。
- 不启动 native bridge。
- 不 spawn ASR worker、LLM worker 或任何后台进程。
- 不捕获 microphone/system audio。
- 不请求或探测 macOS/Windows 权限。
- 不访问 CoreAudio、ScreenCaptureKit、WASAPI、native runtime/process/audio API。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 audio chunk、本地数据目录、browser storage、session JSON、runtime marker、安装包或签名产物。
- 不调用远程 ASR、LLM 或中转站。
- 不修改 PCWEB-079 `/desktop/shell-readiness` 的 8 phase readiness contract。

验证方式：

- 先写 failing API/static/browser/docs tests，要求 `/desktop/runtime-boundary`、`desktop-runtime-boundary-panel`、8 phase runtime boundary、Tauri-first/Electron-fallback、sidecar worker、false `desktop_runtime_safe_to_*` flags 和 PCWEB-080 文档存在。
- 再实现最小 response-only endpoint 和 frontend panel。
- 验证 `create_app(data_dir=...)` + runtime boundary GET 不创建 `sessions/` 或 `live_asr_sessions/`。
- 通过 focused tests 后，运行浏览器 smoke、backend regression 和 `python3 tools/run_quality_gate.py --profile pc-web`。

关联文档：

- `docs/pcweb-080-desktop-runtime-boundary-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-080-desktop-runtime-boundary.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-081：Desktop native bridge command contract

日期：2026-07-02

决策：

新增 `PCWEB-081`，在真实 Tauri shell scaffold 之前先定义 response-only native bridge/process command contract。后端新增：

- `GET /desktop/native-bridge-contract`

Web 工作台新增：

- `desktop-native-bridge-contract-panel`

响应锁定当前只可展示、不可执行的原生桥契约：

- `desktop_bridge_contract_mode=contract_preflight_only`
- `desktop_bridge_contract_status=specified_not_bound`
- `native_bridge_status=not_created`
- `desktop_shell_runtime_status=not_created`
- `bridge_transport_status=not_created`
- `bridge_command_contract_status=specified_not_bound`
- `bridge_process_lifecycle_status=specified_not_started`
- `bridge_resource_policy_status=specified_not_enforced`
- `bridge_error_contract_status=specified`
- `bridge_audit_contract_status=response_only`
- `bridge_platform_adapter_status=not_created`
- `desktop_bridge_command_count=8`
- `desktop_bridge_phase_count=8`
- `desktop_bridge_safe_to_create_native_bridge=false`
- `desktop_bridge_safe_to_bind_ipc=false`
- `desktop_bridge_safe_to_invoke_commands=false`
- `desktop_bridge_safe_to_request_permissions=false`
- `desktop_bridge_safe_to_enumerate_devices=false`
- `desktop_bridge_safe_to_capture_audio=false`
- `desktop_bridge_safe_to_spawn_worker=false`
- `desktop_bridge_safe_to_write_local_files=false`
- `desktop_bridge_safe_to_call_remote_asr=false`
- `desktop_bridge_safe_to_call_llm=false`

Command catalog 初始锁定 8 个未来命令：

- `runtime.get_status`
- `session.prepare`
- `audio.permissions_status`
- `audio.devices_list`
- `audio.capture_start`
- `audio.capture_stop`
- `asr_worker.start`
- `asr_worker.health`

原因：

- PCWEB-080 已推荐 Tauri-first/Electron fallback 和 sidecar worker 模型，但还缺一个稳定、可测试的 native bridge command catalog。
- 真实桌面端的核心风险在 IPC、权限、音频采集、sidecar worker lifecycle、本地存储和错误脱敏；先定义 command/error/resource/audit contract 可以让下一步 Tauri spike 只绑定少量 no-op 命令，而不是边建壳边发明调用形状。
- PCWEB-081 是最后一个 bridge/process preflight。PCWEB-081 绿灯后，下一桌面增量必须进入 `create_tauri_shell_scaffold_against_bridge_contract`，限定为加载现有 Web UI 并按本 contract 绑定 2-3 个 no-op bridge commands。

替代方案：

- 立即创建真实 Tauri/Electron scaffold：不采用。本增量只定义 contract，不引入 Rust/Node/Tauri/Electron 依赖、构建链、权限 entitlements、installer、签名、公证或更新机制。
- 继续增加更抽象的 preflight-only bridge/status panels：不采用。PCWEB-081 已把 bridge/process command contract 落成可测试边界，后续再堆抽象面板属于 scope drift。
- 直接绑定 IPC/native command handler：不采用。没有真实 shell scaffold、权限 UX、audio adapter、sidecar worker packaging 和 local data lifecycle 前，绑定 handler 容易制造“可调用但不可安全执行”的假象。
- 把 audio/worker command 从契约里删掉：不采用。保留 `audio.capture_start` 和 `asr_worker.start` 能提前锁住高风险命令的安全元数据，但它们必须保持 contract-only、not-bound、`safe_to_execute_now=false`。

边界：

- 不创建 Tauri/Electron 项目文件、`src-tauri`、`Cargo.toml`、`package.json`、lock files、desktop package、installer、签名、公证或上架产物。
- 不绑定 native bridge、IPC、websocket bridge、localhost bridge 或 command handler。
- 不启动 native bridge。
- 不 spawn ASR worker、LLM worker 或任何后台进程。
- 不请求或探测 macOS/Windows 权限。
- 不枚举音频设备。
- 不捕获 microphone/system audio。
- 不访问 CoreAudio、ScreenCaptureKit、WASAPI、native runtime/process/audio API。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 audio chunk、本地数据目录、browser storage、bridge audit 文件、session JSON、runtime marker、安装包或签名产物。
- 不调用远程 ASR、LLM 或中转站。
- 不修改 PCWEB-079 `/desktop/shell-readiness` 或 PCWEB-080 `/desktop/runtime-boundary` 的现有 contract。

验证方式：

- 先写 failing API/static/browser/docs tests，要求 `/desktop/native-bridge-contract`、`desktop-native-bridge-contract-panel`、8 command contracts、8 bridge phases、error/resource policy、false `desktop_bridge_safe_to_*` flags 和 PCWEB-081 文档存在。
- 再实现最小 response-only endpoint 和 frontend panel。
- 验证 `create_app(data_dir=...)` + native bridge contract GET 不创建 `sessions/` 或 `live_asr_sessions/`。
- 通过 focused tests 后，运行浏览器 smoke、backend regression 和 `python3 tools/run_quality_gate.py --profile pc-web`。

关联文档：

- `docs/pcweb-081-desktop-native-bridge-contract-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-081-desktop-native-bridge-contract.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-077：Live ASR card lifecycle readiness summary

日期：2026-07-02

决策：

新增 `PCWEB-077`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries`

该端点复用 PCWEB-076 append result audit event persistence preflight，把 PCWEB-065 到 PCWEB-076 的低层 card lifecycle chain 压缩成 response-only UI readiness summary。当前仅支持：

- `mode=summary_only`

响应必须新增：

- `card_lifecycle_readiness_summary_mode=summary_only`
- `card_lifecycle_readiness_summary_status=summarized`
- `card_lifecycle_overall_readiness_status=blocked_until_enabled|safe_replay_existing_events|blocked_by_partial_replay|blocked_by_retry_replay_conflict|blocked_by_idempotency_store_write_preflight`
- `source_preflight_kind=append_result_audit_event_persistence_preflight`
- `source_preflight_endpoint`
- `source_preflight_mode=preflight_only`
- `source_preflight_status=analyzed`
- `source_readiness_status`
- `source_check_count`
- `card_lifecycle_summary_phase_count=12`
- `card_lifecycle_summary_phases`
- `card_lifecycle_block_reasons`
- `card_lifecycle_next_required_decisions`
- `card_lifecycle_safe_to_execute_llm=false`
- `card_lifecycle_safe_to_create_card=false`
- `card_lifecycle_safe_to_append_events=false`
- `card_lifecycle_safe_to_mutate_events=false`
- `card_lifecycle_safe_to_begin_transaction=false`
- `card_lifecycle_safe_to_commit_transaction=false`
- `card_lifecycle_safe_to_write_idempotency_store=false`
- `card_lifecycle_safe_to_persist_append_result_audit_event=false`

每个 summary phase 必须包含：

- `phase_id`
- `phase_status`
- `phase_mode`
- `phase_kind`
- `write_boundary_status`
- `item_count`
- `safe_to_write=false`
- `source_status_field`
- `source_status_value`

十二个 phase 为：

- `card_lifecycle_preview`
- `append_preflight`
- `append_disabled_run`
- `append_repository_dry_run`
- `append_transaction_disabled_run`
- `append_result_audit_preview`
- `retry_replay_preflight`
- `append_event_serializer_dry_run`
- `append_mutation_preflight`
- `append_transaction_commit_preflight`
- `append_idempotency_store_write_preflight`
- `append_result_audit_event_persistence_preflight`

状态规则：

- fresh append 且无 replay/conflict/upstream blocker 时返回 `blocked_until_enabled`，并列出启用 audit persistence、idempotency write、transaction commit、retry/replay policy 和 lifecycle mutation 的 next decisions。
- 完整相同 lifecycle event 重放返回 `safe_replay_existing_events`，但必须解释为 no-new-write replay evidence，不得被 UI 解释为允许创建卡片、append event、写 idempotency store 或写 audit event。
- 部分重放返回 `blocked_by_partial_replay`，必须把 partial replay blocker 放进 summary block reasons。
- retry/replay mismatch、重复 idempotency evidence 或 marker conflict 返回 `blocked_by_retry_replay_conflict`。
- idempotency-store/transaction/mutation/serializer preflight blocked 且 retry/replay 未给出更具体解释时返回 `blocked_by_idempotency_store_write_preflight`。

命名和安全约束：

- 不使用泛化的 `readiness_status` 字段，避免和 provider readiness 混淆；必须使用 `card_lifecycle_overall_readiness_status` 或 `card_lifecycle_readiness_summary_*`。
- 不把 PCWEB-076 响应整包 spread 到 summary 响应。PCWEB-071 的 `safe_to_replay_existing_events=true` 是 replay source evidence，不是 UI action permission；summary 只能暴露 scoped source fields 和 scoped `card_lifecycle_safe_to_* = false` flags。
- `source_status_field` 和 `source_status_value` 用于 UI click-through 和测试追踪，不替代原始 PCWEB-076 详情端点。

原因：

- PCWEB-065..076 已经锁定低层 lifecycle 边界，但低层响应太密，不适合前端直接判断“这张候选卡现在能不能生成、卡在哪里、下一步开什么闸门”。
- 用户价值在实时会议中看到清晰建议和阻断原因，而不是只得到一串调试型 preflight JSON。
- 先做 response-only summary 能把 UI 合同固定下来，同时继续保持零额外费用、零密钥读取、零 `/events` mutation 和零真实写入。

替代方案：

- 让前端直接消费 PCWEB-076 全量响应：不采用。上游字段过密，且 safe replay source evidence 可能被误读成可执行动作。
- 在 PCWEB-076 中追加 summary 字段：不采用。PCWEB-076 是 audit event persistence preflight，summary 是 UI/product projection，混在一起会扩大低层接口职责。
- 直接启用真实 card lifecycle write path：不采用。当前还没有 enabled event append repository、transaction commit、idempotency store write、audit event persistence、rollback/compensation 和 retry/replay resolution policy。

边界：

- 仅 card lifecycle readiness summary。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不写入 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store 或 marker。
- 不开启、提交或回滚 repository transaction。

验证方式：

- 先写 failing tests 覆盖 fresh summary、complete safe replay no-new-write summary、partial replay summary、retry/replay conflict summary、upstream source blocker summary、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator、source preflight call 和 phase projection helpers。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-077-live-asr-card-lifecycle-readiness-summary-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-077-live-asr-card-lifecycle-readiness-summary.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-078：Live ASR card lifecycle readiness UI

日期：2026-07-02

决策：

新增 `PCWEB-078`，在 Web 工作台中展示 PCWEB-077 的 response-only readiness summary。前端在 Live ASR terminal summary 后，从当前 stream 中已经展示的 no-LLM `llm_request_draft_event`、EvidenceSpan、state event 和 transcript final/revision timing 派生 local contract probe，然后调用：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-readiness-summaries`

并把结果渲染到：

- `card-lifecycle-readiness-panel`

显示内容：

- `card_lifecycle_overall_readiness_status`
- `card_lifecycle_summary_phase_count=12`
- `card_lifecycle_summary_phases`
- `card_lifecycle_block_reasons`
- `card_lifecycle_next_required_decisions`
- `llm_call_status=not_called`
- `event_append_status=not_appended`
- `audit_event_append_status=not_appended`
- `idempotency_store_write_status=not_written`
- scoped `card_lifecycle_safe_to_* = false`

实现补充决策：

- Live ASR SSE terminal path 收到 `evaluation_summary` 时只能拿到单个终端事件，因此 readiness probe 必须改用浏览器内存中累计的完整 Live ASR event stream；非 SSE fallback 仍可直接使用一次性拉取的完整 event list。
- local contract probe 统一使用 core gate 当前允许的 `owner_gap` card type。`ActionItem`、`Risk`、`OpenQuestion` 的正式卡类型以后需要单独扩展 core whitelist 和产品 taxonomy；PCWEB-078 不在 UI probe 中提前发明 `action_gap`、`risk_gap` 或 `followup_gap`。
- 如果所有 visible request drafts 都存在 stale/missing evidence 或 candidate degradation reasons，UI 必须显示本地 no-summary 状态并跳过 readiness POST；不能对本地已知不合格的 probe 生成 readiness summary。
- EventSource error recovery 通过 JSON `/events` fallback 拿到 terminal `evaluation_summary` 时，也必须执行 Live ASR terminal side effects，让 draft/readiness UI 与 no-EventSource fallback 行为一致。

原因：

- PCWEB-077 已经把低层 preflight chain 压缩成 UI-friendly summary，但如果不展示到工作台，产品仍然停留在隐藏接口能力。
- 当前 Live ASR 模式展示了 request draft 和候选队列，却没有解释为什么候选还不能成为正式建议卡。PCWEB-078 把“阻塞在哪里、下一步需要哪个闸门”放到会议中可见的位置。
- 这是前端 consumption 层，不打开真实 LLM、card engine、event append repository、idempotency store 或 audit persistence，因此仍符合默认本地无额外收费边界。
- 使用 `owner_gap` 是为了让 response-only probe 复用现有 gate 验证链路；它不表示所有未来建议卡都归为 owner gap。

替代方案：

- 让用户通过 API 手动查看 PCWEB-077：不采用。会议中产品价值需要在工作台中直接可见。
- 把 readiness summary 渲染成 `.suggestion-card`：不采用。它不是正式建议卡，不能影响正式卡片数量、反馈按钮或 quality 统计。
- 在 terminal summary 前对每个 request draft 都调用 readiness summary：不采用。当前先验证单个合格候选的 end-to-end UI，避免过早引入调度、节流和并发问题。

边界：

- 仅 Web workbench UI consumption。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用远程 ASR/LLM 或中转站。
- 不请求 formal `/sessions/{session_id}/report.md`。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `.suggestion-card`。
- 不生成真实 `suggestion_silenced`。
- 不写入 append result audit event、idempotency store、marker、summary artifact 或 Live ASR audit record。
- 不开启、提交或回滚 repository transaction。

验证方式：

- 先写 failing static/browser/docs tests，要求 `card-lifecycle-readiness-panel`、`llm-card-lifecycle-readiness-summaries`、12 phase UI 和 PCWEB-078 文档存在。
- 实现最小前端接入后，运行 focused pytest、browser smoke、backend regression 和 `python3 tools/run_quality_gate.py --profile pc-web`。

关联文档：

- `docs/pcweb-078-live-asr-card-lifecycle-readiness-ui-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-078-live-asr-card-lifecycle-readiness-ui.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-079：Desktop shell readiness boundary

日期：2026-07-02

决策：

新增 `PCWEB-079`，在进入真实 Mac-first desktop shell 前先提供一个 no-capture/no-permission/no-worker/no-paid-call 的 readiness boundary。后端新增：

- `GET /desktop/shell-readiness`

Web 工作台新增：

- `desktop-readiness-panel`

响应和 UI 必须展示：

- `desktop_readiness_mode=preflight_only`
- `desktop_readiness_status=blocked_before_desktop_shell`
- `desktop_shell_status=not_started`
- `target_platform_status=macos_first_windows_deferred`
- `audio_capture_status=not_connected`
- `microphone_permission_status=not_requested`
- `system_audio_permission_status=not_requested`
- `asr_worker_status=not_started`
- `llm_provider_status=not_connected`
- `desktop_readiness_phase_count=8`
- `desktop_readiness_phases`
- `desktop_readiness_blockers`
- `desktop_readiness_next_decisions`
- `desktop_safe_to_capture_audio=false`
- `desktop_safe_to_request_permissions=false`
- `desktop_safe_to_start_asr_worker=false`
- `desktop_safe_to_call_remote_asr=false`
- `desktop_safe_to_call_llm=false`
- `desktop_safe_to_write_audio_chunks=false`

原因：

- PCWEB-078 已经把 Live ASR card lifecycle readiness 展示到工作台，但下一阶段是桌面采集和安装包，风险边界完全不同。
- 直接实现麦克风/系统音频采集会同时引入系统权限、native API、ASR worker 生命周期、数据目录、安装签名和可能的 provider 调用，容易让产品从“可验证 Copilot”滑成“能录音转写的客户端”。
- 当前最重要的产品价值仍是实时状态和建议卡片；PCWEB-079 先把进入桌面壳之前的 blocker、phase 和 next decisions 展示给用户和开发者，避免误把 Web MVP 的 mock/live skeleton 当成真实桌面能力。
- 继续保持默认零额外费用、零权限弹窗、零真实采集和零配置读取。

替代方案：

- 直接接入 macOS 麦克风/系统音频采集：不采用。权限、隐私、worker、数据生命周期和质量验证还没有通过独立边界。
- 把 readiness 做成设置向导并提供“开始采集”按钮：不采用。PCWEB-079 只展示状态，不执行任何系统动作。
- 只在文档里记录桌面 blocker：不采用。工作台必须可见，否则后续开发和演示容易误判当前能力。

实现补充决策：

- Web 工作台启动路径必须保持 passive：PCWEB-079 时只请求 `/desktop/shell-readiness` 和 fixture list，不自动 `POST /demo/fixtures/{fixture_id}/sessions`。PCWEB-080 extends this passive startup read set with read-only `/desktop/runtime-boundary`; three startup reads remain no-write/no-capture GET paths. 用户显式加载 fixture 后才创建 demo session。
- `MEETING_COPILOT_DATA_DIR` 下的 JSON repository 目录必须 lazy-create：仅构造 app 或 GET readiness 不创建 `sessions/` 或 `live_asr_sessions/`，直到真实 session/audit record 写入时才创建目录。

边界：

- 不捕获 microphone audio。
- 不捕获 system audio。
- 不请求或探测 macOS/Windows 权限。
- 不访问 CoreAudio、ScreenCaptureKit、WASAPI、loopback、virtual device 或任何 native audio API。
- 不启动 ASR worker。
- 不加载 ASR 模型。
- 不调用远程 ASR。
- 不调用 LLM 或中转站。
- 不读取 provider config、API key、Authorization header、bearer token、环境密钥、keychain、secret adapter 或 `configs/local/`。
- 不写 audio chunk、本地数据目录、browser storage、安装包、签名、公证、上架或 auto-update 产物。

验证方式：

- 先写 failing API/static/browser/docs tests，要求 `/desktop/shell-readiness`、`desktop-readiness-panel`、8 phase readiness、false `desktop_safe_to_*` flags 和 PCWEB-079 文档存在。
- 实现最小 backend helper、startup renderer 和只读 UI panel。
- 运行 focused pytest、browser smoke、backend regression 和 `python3 tools/run_quality_gate.py --profile pc-web`。

关联文档：

- `docs/pcweb-079-desktop-shell-readiness-boundary-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-079-desktop-shell-readiness-boundary.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-076：Live ASR card lifecycle append result audit event persistence preflight

日期：2026-07-02

决策：

新增 `PCWEB-076`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-event-persistence-preflights`

该端点复用 PCWEB-075 append idempotency store write preflight，把未来 lifecycle append 的 append result audit event persistence 合同映射成 response-only persistence preflight checks。当前仅支持：

- `mode=preflight_only`

响应必须保留或新增：

- `append_result_audit_event_persistence_preflight_mode=preflight_only`
- `append_result_audit_event_persistence_preflight_status=analyzed`
- `append_result_audit_event_persistence_readiness_status=blocked_until_enabled|safe_replay_existing_events|blocked_by_partial_replay|blocked_by_retry_replay_conflict|blocked_by_idempotency_store_write_preflight`
- `append_result_audit_event_persistence_preflight_check_count`
- `append_result_audit_event_persistence_preflight_checks`
- `audit_event_append_status=not_appended`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `repository_transaction_status=not_started`
- `repository_transaction_commit_status=not_committed`
- `repository_transaction_rollback_status=not_started`
- `safe_to_persist_append_result_audit_event=false`
- `safe_to_write_audit_events=false`
- `safe_to_write_idempotency_store=false`
- `safe_to_begin_transaction=false`
- `safe_to_commit_transaction=false`
- `safe_to_rollback_transaction=false`
- `safe_to_mutate_events=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

每个 audit event persistence preflight check 必须包含：

- deterministic `append_result_audit_event_persistence_preflight_check_id`
- PCWEB-070 `audit_event_id`
- PCWEB-070 `audit_idempotency_key`
- PCWEB-070 `transaction_run_id`
- PCWEB-070 `transaction_run_status`
- PCWEB-070 `append_run_id`
- PCWEB-070 `repository_result_id`
- PCWEB-070 `repository_result_status`
- PCWEB-070 `repository_idempotency_key`
- PCWEB-070 `preflight_append_status`
- PCWEB-070 `preflight_conflict_status`
- PCWEB-070 `audit_repository_transaction_status`
- PCWEB-070 `repository_write_status`
- PCWEB-070 `transaction_write_status`
- future `future_append_result_audit_event_id`
- `future_append_result_audit_event_type=card_lifecycle_append_result`
- `future_append_result_audit_event_status=would_persist_if_enabled|not_required_existing_replay|blocked`
- `append_result_audit_event_persistence_reason`
- PCWEB-075 `idempotency_store_write_preflight_check_id`
- `idempotency_store_write_preflight_check_status`
- `idempotency_store_write_readiness_status`
- `future_idempotency_record_status`
- PCWEB-074 `transaction_commit_readiness_status`
- PCWEB-074 `transaction_commit_preflight_check_id`
- PCWEB-073 `mutation_preflight_check_id`
- PCWEB-071 `retry_replay_check_id`
- PCWEB-072 `serializer_result_id`
- `event_type`
- `future_event_id`
- append `idempotency_key`
- future `transaction_idempotency_key`
- `would_append_sequence`
- `would_append_after_sequence`
- `append_status`
- `conflict_status`
- disabled `safe_to_*` flags

命名约束：

- `audit_repository_transaction_status` 表示 PCWEB-070 audit preview 来源里的 repository transaction status。
- `repository_transaction_status=not_started` 仍表示 PCWEB-076 本端点没有开启新的 repository transaction。
- 二者不得复用同一个字段，否则会混淆“上游预览 provenance”和“当前 preflight 禁写边界”。

状态规则：

- fresh append 且无 replay/conflict/upstream blocker 时返回 `blocked_until_enabled`，每个 check 暴露 future audit event identity，但不能写入。
- 完整相同 lifecycle event 重放返回 `safe_replay_existing_events`，每个 check 为 `persistence_not_required_for_safe_replay`，这表示不应再写新的 append result audit event。
- 部分重放返回 `blocked_by_partial_replay`，不得为缺失事件写入 audit event 或补写尾部。
- retry/replay mismatch、重复 idempotency evidence 或 marker conflict 返回 `blocked_by_retry_replay_conflict`。
- idempotency-store/transaction/mutation/serializer preflight blocked 且 retry/replay 未给出更具体解释时返回 `blocked_by_idempotency_store_write_preflight`。

原因：

- PCWEB-070 已能预览 append result audit event identity，但真实启用前必须明确 audit persistence 的 fresh-write、no-write replay 和 blocked 规则。
- 真实产品的实时建议卡需要可解释的审计轨迹；如果 audit event persistence 在 partial replay 或 upstream blocked 时误写，会制造“看似完成”的错误审计证据。
- `safe_replay_existing_events` 在 PCWEB-076 中必须被解释为“无需也不得再写 append result audit event”，而不是“允许新增审计记录”。
- 先做 response-only preflight 能继续保持零额外费用、零密钥读取和零 `/events` mutation，同时把未来 enabled audit persistence 的输入输出固定下来。

替代方案：

- 直接启用真实 audit event persistence：不采用。当前还没有 enabled audit repository、transaction commit、idempotency store enabled write、rollback/compensation 和 retry/replay resolution policy。
- 把 audit event persistence 混入 PCWEB-075 idempotency store write preflight：不采用。幂等记录和审计事件是两个不同 durable artifact，混在一起会让 safe replay 和 fresh append 的语义变模糊。
- 对 safe replay 也写入新的 audit event：不采用。safe replay 已经有既有事件证据，再写会污染审计轨迹。
- 遇到 partial replay 时先写缺失尾部的 audit event：不采用。这会制造“有审计记录但无生命周期事件”的危险状态。

边界：

- 仅 append result audit event persistence preflight。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不写入 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store 或 marker。
- 不开启、提交或回滚 repository transaction。
- 即使 `append_result_audit_event_persistence_readiness_status=blocked_until_enabled`，`safe_to_persist_append_result_audit_event`、`safe_to_write_audit_events`、`safe_to_begin_transaction`、`safe_to_commit_transaction`、`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 fresh future-audit-event blocked-until-enabled、complete safe replay no-persistence、partial replay blocked、retry/replay conflict blocked、upstream idempotency-store preflight blocked、schema-invalid silenced path、policy-blocked silenced path、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 audit event persistence preflight helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-076-live-asr-card-lifecycle-append-result-audit-event-persistence-preflight-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-076-live-asr-card-lifecycle-append-result-audit-event-persistence-preflight.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-074：Live ASR card lifecycle append transaction commit preflight

日期：2026-07-02

决策：

新增 `PCWEB-074`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-commit-preflights`

该端点复用 PCWEB-073 append mutation preflight 和 PCWEB-071 retry/replay preflight，把未来 lifecycle append 的事务提交合同映射成 response-only commit readiness checks。当前仅支持：

- `mode=preflight_only`

响应必须保留或新增：

- `append_transaction_commit_preflight_mode=preflight_only`
- `append_transaction_commit_preflight_status=analyzed`
- `transaction_commit_readiness_status=blocked_until_enabled|safe_replay_existing_events|blocked_by_partial_replay|blocked_by_retry_replay_conflict|blocked_by_mutation_preflight`
- `transaction_commit_preflight_check_count`
- `transaction_commit_preflight_checks`
- `repository_transaction_status=not_started`
- `repository_transaction_commit_status=not_committed`
- `repository_transaction_rollback_status=not_started`
- `event_append_status=not_appended`
- `audit_event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_begin_transaction=false`
- `safe_to_commit_transaction=false`
- `safe_to_rollback_transaction=false`
- `safe_to_mutate_events=false`
- `safe_to_append_events=false`
- `safe_to_write_idempotency_store=false`
- `safe_to_write_audit_events=false`
- `safe_to_create_card=false`

每个 transaction commit preflight check 必须包含：

- deterministic `transaction_commit_preflight_check_id`
- PCWEB-073 `mutation_preflight_check_id`
- `mutation_preflight_check_status`
- PCWEB-071 `retry_replay_check_id`
- per-event `retry_replay_resolution_status`
- PCWEB-072 `serializer_result_id`
- `serialization_status`
- `event_type`
- `future_event_id`
- `serialized_event_id`
- `preview_event_id`
- append `idempotency_key`
- future `transaction_idempotency_key`
- `would_append_sequence`
- `would_append_after_sequence`
- `append_status`
- `conflict_status`
- `repository_transaction_status=not_started`
- `repository_transaction_commit_status=not_committed`
- `repository_transaction_rollback_status=not_started`
- `event_append_status=not_appended`
- `audit_event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- disabled `safe_to_*` flags

状态规则：

- fresh append 且无 retry/replay conflict 时返回 `blocked_until_enabled`，不能返回 ready/allowed。
- 完整相同 lifecycle event 重放返回 `safe_replay_existing_events`，但这只表示既有事件可解释为幂等 replay，不表示允许新事务 commit。
- 部分重放返回 `blocked_by_partial_replay`，不得把缺失事件视作可补写尾部。
- retry/replay mismatch、重复 idempotency evidence 或 marker conflict 返回 `blocked_by_retry_replay_conflict`。
- mutation preflight blocked 且 retry/replay 未给出更具体解释时返回 `blocked_by_mutation_preflight`。

原因：

- PCWEB-073 已能分析未来 mutation eligibility，但真实 commit 还需要额外约束：事务开始/提交/回滚、idempotency store、append result audit persistence、retry/replay 语义和失败补偿。
- 直接启用 commit 会同时打开 event append、idempotency 写入、audit 写入、partial replay 补写、重复卡片和回滚失败风险。
- `safe_replay_existing_events` 是本轮最容易被误解的状态，必须在合同层明确它不是 commit 许可。
- 先做 response-only commit preflight 能把 future enabled repository transaction 的输入、输出和禁用语义固定下来，同时保持零额外费用、零密钥读取和零 `/events` mutation。

替代方案：

- 直接启用真实 repository transaction commit：不采用。当前还没有 enabled idempotency store、audit event persistence、rollback/compensation 和 retry/replay resolution policy。
- 继续只依赖 PCWEB-073 mutation preflight：不采用。mutation eligibility 不足以表达 safe replay、partial replay、repository transaction begin/commit/rollback 禁用状态。
- 命名为 `transaction-commits` 或 `commit-runs`：不采用。会误导调用方以为真实 commit 已开放；本轮仍是 preflight-only。
- 遇到 partial replay 时补写缺失事件：不采用。这会产生半事务追加风险，必须阻断到 future enabled policy 明确以后。

边界：

- 仅 transaction commit preflight。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成或写入 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 不开启、提交或回滚 repository transaction。
- 即使 `transaction_commit_readiness_status=safe_replay_existing_events`，`safe_to_begin_transaction`、`safe_to_commit_transaction`、`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 fresh blocked-until-enabled、complete safe replay but no commit、partial replay blocked、retry/replay conflict blocked、schema-invalid silenced path、policy-blocked silenced path、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 commit preflight helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-074-live-asr-card-lifecycle-append-transaction-commit-preflight-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-074-live-asr-card-lifecycle-append-transaction-commit-preflight.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-075：Live ASR card lifecycle append idempotency store write preflight

日期：2026-07-02

决策：

新增 `PCWEB-075`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-idempotency-store-write-preflights`

该端点复用 PCWEB-074 append transaction commit preflight，把未来 lifecycle append 的 idempotency store write 合同映射成 response-only write preflight checks。当前仅支持：

- `mode=preflight_only`

响应必须保留或新增：

- `idempotency_store_write_preflight_mode=preflight_only`
- `idempotency_store_write_preflight_status=analyzed`
- `idempotency_store_write_readiness_status=blocked_until_enabled|safe_replay_existing_events|blocked_by_partial_replay|blocked_by_retry_replay_conflict|blocked_by_transaction_commit_preflight`
- `idempotency_store_write_preflight_check_count`
- `idempotency_store_write_preflight_checks`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `repository_transaction_status=not_started`
- `repository_transaction_commit_status=not_committed`
- `repository_transaction_rollback_status=not_started`
- `event_append_status=not_appended`
- `audit_event_append_status=not_appended`
- `safe_to_write_idempotency_store=false`
- `safe_to_begin_transaction=false`
- `safe_to_commit_transaction=false`
- `safe_to_rollback_transaction=false`
- `safe_to_mutate_events=false`
- `safe_to_append_events=false`
- `safe_to_write_audit_events=false`
- `safe_to_create_card=false`

每个 idempotency store write preflight check 必须包含：

- deterministic `idempotency_store_write_preflight_check_id`
- future `future_idempotency_record_id`
- future `future_idempotency_record_key`
- `future_idempotency_record_status=would_write_if_enabled|not_required_existing_replay|blocked`
- `idempotency_store_write_reason`
- PCWEB-074 `transaction_commit_preflight_check_id`
- `transaction_commit_preflight_check_status`
- PCWEB-073 `mutation_preflight_check_id`
- `mutation_preflight_check_status`
- PCWEB-071 `retry_replay_check_id`
- per-event `retry_replay_resolution_status`
- PCWEB-072 `serializer_result_id`
- `serialization_status`
- `event_type`
- `future_event_id`
- `serialized_event_id`
- `preview_event_id`
- append `idempotency_key`
- future `transaction_idempotency_key`
- `would_append_sequence`
- `would_append_after_sequence`
- `append_status`
- `conflict_status`
- `repository_transaction_status=not_started`
- `repository_transaction_commit_status=not_committed`
- `repository_transaction_rollback_status=not_started`
- `event_append_status=not_appended`
- `audit_event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- disabled `safe_to_*` flags

状态规则：

- fresh append 且无 retry/replay conflict 时返回 `blocked_until_enabled`，每个 check 暴露 future idempotency record identity，但不能写入。
- 完整相同 lifecycle event 重放返回 `safe_replay_existing_events`，每个 check 为 `write_not_required_for_safe_replay`，这表示不应再写新的 idempotency record。
- 部分重放返回 `blocked_by_partial_replay`，不得为缺失事件写入 idempotency record 或补写尾部。
- retry/replay mismatch、重复 idempotency evidence 或 marker conflict 返回 `blocked_by_retry_replay_conflict`。
- mutation/serializer/transaction preflight blocked 且 retry/replay 未给出更具体解释时返回 `blocked_by_transaction_commit_preflight`。

原因：

- PCWEB-074 已能分析未来 transaction commit readiness，但真实启用前还必须明确 idempotency store 的写入条件、no-write replay 条件和阻断条件。
- 真实产品的实时建议卡必须能抵御 LLM response 重试、网络重放和延迟提交；如果 idempotency store 合同不清楚，后续会出现重复卡片、半写入和伪造 replay evidence。
- `safe_replay_existing_events` 在 PCWEB-075 中必须被解释为“无需也不得再写 idempotency record”，而不是“允许写入或 commit”。
- 先做 response-only preflight 能继续保持零额外费用、零密钥读取和零 `/events` mutation，同时把未来 enabled idempotency store 的输入输出固定下来。

替代方案：

- 直接启用真实 idempotency store write：不采用。当前还没有 enabled store backend、transaction commit、append audit persistence、rollback/compensation 和 retry/replay resolution policy。
- 把 idempotency store write 混入 PCWEB-074 commit preflight：不采用。commit readiness 和幂等记录写入条件是两个不同合同，混在一起会让 safe replay 和 fresh append 的语义变模糊。
- 对 safe replay 也写入新的 idempotency record：不采用。safe replay 已经有既有事件证据，再写会污染 replay evidence。
- 遇到 partial replay 时先写缺失尾部的 idempotency record：不采用。这会制造半事务和“有幂等记录但无事件”的危险状态。

边界：

- 仅 idempotency store write preflight。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成或写入 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store 或 marker。
- 不开启、提交或回滚 repository transaction。
- 即使 `idempotency_store_write_readiness_status=blocked_until_enabled`，`safe_to_write_idempotency_store`、`safe_to_begin_transaction`、`safe_to_commit_transaction`、`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 fresh future-idempotency-record blocked-until-enabled、complete safe replay no-write、partial replay blocked、retry/replay conflict blocked、schema-invalid silenced path、policy-blocked silenced path、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 idempotency store write preflight helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-075-live-asr-card-lifecycle-append-idempotency-store-write-preflight-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-075-live-asr-card-lifecycle-append-idempotency-store-write-preflight.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-073：Live ASR card lifecycle append mutation preflight

日期：2026-07-02

决策：

新增 `PCWEB-073`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-mutation-preflights`

该端点复用 PCWEB-072 append event serializer，把 canonical future lifecycle event objects 映射成“未来 enabled mutation 之前的 response-only eligibility checks”。当前仅支持：

- `mode=preflight_only`

响应必须保留或新增：

- `append_mutation_preflight_mode=preflight_only`
- `append_mutation_preflight_status=analyzed`
- `append_mutation_readiness_status=blocked_until_enabled|blocked_by_serializer_preflight`
- `mutation_preflight_check_count`
- `mutation_preflight_checks`
- `repository_transaction_status=not_started`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_mutate_events=false`
- `safe_to_commit_transaction=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

每个 mutation preflight check 必须包含：

- deterministic `mutation_preflight_check_id`
- PCWEB-072 `serializer_result_id`
- `serialization_status`
- `event_type`
- `future_event_id`
- `serialized_event_id`
- `preview_event_id`
- append `idempotency_key`
- `would_append_sequence`
- `would_append_after_sequence`
- `append_status`
- `conflict_status`
- `repository_transaction_status=not_started`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_mutate_event=false`
- `safe_to_append_event=false`

原因：

- PCWEB-072 已锁定未来 persisted event object，但仍需要一个独立边界表达“这些 canonical events 只能被分析为未来 mutation 单元，当前还不能写入”。
- 使用 `append-mutation-preflights` 命名，而不是 `commit-preflights`，是为了避免误导调用方以为 PCWEB-073 已经接近或允许真实 commit；本阶段仍只是写入前 eligibility analysis。
- 该边界让后续 enabled repository transaction、idempotency store write、append result audit persistence 和 retry/replay policy 有更清晰的输入对象，同时保持零额外费用、零密钥读取和零 `/events` mutation。

替代方案：

- 直接启用真实 append mutation：不采用。会同时引入 repository transaction、idempotency store、失败补偿、retry/replay policy 和 card lifecycle 持久化风险。
- 命名为 `append-commit-preflights`：不采用。当前不开始、不提交、不回滚 repository transaction，commit 字样会让边界过度接近 enabled 写入。
- 只依赖 PCWEB-072 serializer：不采用。serializer 证明 event object shape，但没有单独表达 mutation eligibility、transaction/idempotency 写入仍禁用和调用方下一步决策。
- 在 PCWEB-071 retry/replay preflight 里顺带做 mutation readiness：不采用。retry/replay conflict resolution 和未来 mutation eligibility 是不同决策边界。

边界：

- 仅 append mutation preflight。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 不开启、提交或回滚 repository transaction。
- 即使 `append_mutation_readiness_status=blocked_until_enabled`，`safe_to_mutate_events`、`safe_to_commit_transaction`、`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 allowed mutation preflight、schema-invalid silenced mutation preflight、policy-blocked silenced mutation preflight、serializer/preflight conflict preservation、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 mutation preflight helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-073-live-asr-card-lifecycle-append-mutation-preflight-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-073-live-asr-card-lifecycle-append-mutation-preflight.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-072：Live ASR card lifecycle append event serializer dry-run

日期：2026-07-02

决策：

新增 `PCWEB-072`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-event-serializer-dry-runs`

该端点复用 PCWEB-066 append preflight 和 PCWEB-065 lifecycle preview，把每个 append plan item 映射成“未来 enabled repository append 会写入的 canonical lifecycle event object”。当前仅支持：

- `mode=dry_run_only`

响应必须保留或新增：

- `append_event_serializer_mode=dry_run_only`
- `append_event_serializer_status=serialized`
- `append_event_serialization_status=would_serialize_if_enabled|blocked_by_preflight`
- `append_event_count`
- `serialized_append_events`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

每个 serialized event 必须包含：

- deterministic `id` 和 `event_id`
- `event_type`
- `sequence` 等于 append preflight 的 `would_append_sequence`
- preview event 的 `at_ms`
- `source=live_asr_stream`
- `trace_kind=live_event`
- top-level `idempotency_key`
- payload 内与 top-level 一致的 `idempotency_key`
- `request_id`
- `request_draft_event_id`
- `card_id`
- preview-derived event payload
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `safe_to_append_event=false`

原因：

- PCWEB-065/066 已知道未来会追加哪些事件以及追加顺序/幂等键，但还没有锁定“真实 repository append 最终会写入什么事件对象”。
- 如果没有 serializer 合同，未来 enabled append 可能与 preview/preflight 漂移，导致 payload 幂等键不一致、request/card linkage 丢失或 replay 判断不可靠。
- 先做 serializer dry-run 可以把未来持久化对象变成可测试、可审查的本地响应，同时继续保持零额外费用、零密钥读取和零 `/events` mutation。

替代方案：

- 直接启用真实 append：不采用。会同时引入 repository transaction、idempotency store、冲突恢复、卡片持久化和失败补偿，风险过大。
- 只依赖 PCWEB-065 preview events：不采用。preview events 不包含 append sequence、append idempotency status 和 preflight conflict 结果。
- 只依赖 PCWEB-066 append plan：不采用。append plan 不包含完整 event-specific payload，无法代表未来 persisted event object。
- 在 PCWEB-071 retry/replay preflight 中顺带返回 serialized events：不采用。retry/replay 判断和 event serialization 是两个不同边界，混在一起会模糊审查重点。

边界：

- 仅 append event serializer dry-run。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 不开启、提交或回滚 repository transaction。
- 即使 `append_event_serialization_status=would_serialize_if_enabled`，`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 allowed serializer、schema-invalid silenced serializer、policy-blocked silenced serializer、preflight conflict preservation、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 serializer helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-072-live-asr-card-lifecycle-append-event-serializer-dry-run-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-072-live-asr-card-lifecycle-append-event-serializer-dry-run.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-069：Live ASR card lifecycle append transaction disabled run

日期：2026-07-02

决策：

新增 `PCWEB-069`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-transaction-runs`

该端点复用 PCWEB-068 repository dry-run，然后把每个 repository result 映射成 transaction disabled run envelope。当前仅支持：

- `mode=disabled`

响应必须保留或新增：

- `transaction_run_mode=disabled`
- `transaction_run_status=skipped`
- `transaction_run_count`
- `transaction_runs`
- `transaction_run_id`
- `transaction_idempotency_key`
- `skip_reason=repository_transaction_disabled|repository_preflight_blocked`
- `repository_transaction_status=disabled`
- `transaction_write_status=disabled`
- `idempotency_store_write_status=not_written`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `repository_dry_run_status=would_append_if_enabled|blocked_by_preflight`
- `append_preflight_status=allowed|blocked`
- `append_errors`
- `repository_results`
- request/draft/candidate/state/gap/evidence/segment linkage
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_commit_transaction=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

原因：

- PCWEB-068 已经描述未来 repository result envelope，但还没有显式表达未来 repository transaction action 的输入、结果、幂等键和禁用状态。
- 真正打开 `/events` mutation 前，需要先证明事务层会如何包装 allowed append、schema/policy silenced append 和 preflight conflict。
- 直接启用真实 transaction 会同时引入 commit/rollback、idempotency store 写入、append result audit event、重试、冲突恢复和正式 card lifecycle，风险过大。
- 保持默认零额外费用、零密钥读取和零 `/events` mutation，符合当前“先本地可验、再开启真实执行”的阶段约束。

替代方案：

- 直接让 PCWEB-068 写入 `/events`：不采用。会绕过 transaction boundary 和幂等写入合同。
- 只保留 PCWEB-068，不增加 transaction disabled run：不采用。后续 enabled append 缺少事务动作 envelope，难以测试 commit/retry/rollback 语义。
- 让 PCWEB-069 返回 `safe_to_commit_transaction=true`：不采用。当前没有真实 repository transaction 和 idempotency store 写入，不能给出真实安全承诺。

边界：

- 仅 transaction disabled run。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成 append result audit event。
- 不开启 repository transaction，不 commit，不 rollback。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 即使 `repository_dry_run_status=would_append_if_enabled`，`transaction_run_status` 也必须是 `skipped`，`repository_transaction_status` 也必须是 `disabled`，`event_append_status` 也必须是 `not_appended`，`idempotency_store_status` 和 `idempotency_store_write_status` 也必须是 `not_written`，`safe_to_commit_transaction`、`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。
- transaction run identity 不得依赖冒号、下划线等标点折叠后的字符串；真实写入前继续沿用可逆或等价 collision-resistant tuple encoding。
- 在 disabled 阶段，`transaction_run_id` 只作为 response/envelope-local display id；后续真实持久化、replay、conflict detection 和 idempotency store 写入必须以 `transaction_idempotency_key` 作为 canonical durable identity。

验证方式：

- 先写 failing tests 覆盖 allowed transaction disabled run、schema-invalid silenced transaction run、policy-blocked silenced transaction run、preflight blocked conflict、missing session、unknown request、JSON persistence bytes unchanged、request shape 422、delimiter collision、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 transaction disabled-run helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-069-live-asr-card-lifecycle-append-transaction-disabled-run-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-069-live-asr-card-lifecycle-append-transaction-disabled-run.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-070：Live ASR card lifecycle append result audit preview

日期：2026-07-02

决策：

新增 `PCWEB-070`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-result-audit-previews`

该端点复用 PCWEB-069 transaction disabled run，然后把每个 transaction run 映射成 response-only append result audit event preview。当前仅支持：

- `mode=preview_only`

响应必须保留或新增：

- `append_result_audit_mode=preview_only`
- `append_result_audit_status=previewed`
- `append_result_audit_event_status=preview_only`
- `append_result_audit_event_count`
- `append_result_audit_events`
- `audit_event_id`
- `audit_event_type=card_lifecycle_append_result`
- `audit_result_status=skipped_transaction_disabled|blocked_by_preflight`
- `audit_idempotency_key`
- `transaction_run_id`
- `transaction_idempotency_key`
- `repository_result_id`
- `repository_result_status=would_append_if_enabled|blocked_by_preflight`
- `repository_dry_run_status=would_append_if_enabled|blocked_by_preflight`
- `append_preflight_status=allowed|blocked`
- `append_errors`
- `audit_event_append_status=not_appended`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- request/draft/candidate/state/gap/evidence/segment linkage
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_write_audit_events=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

原因：

- PCWEB-069 已经描述未来事务动作 envelope，但还没有显式表达 enabled append 后需要写入的 result audit event 形状。
- 真实打开 `/events` mutation 前，需要先证明 result audit 如何引用 transaction run、repository result、preflight conflict、schema/card/silenced lifecycle 和幂等键。
- 直接启用真实 audit event 写入会同时引入 transaction commit、idempotency store 写入、retry/replay conflict resolution 和正式 card lifecycle，风险过大。
- 保持默认零额外费用、零密钥读取和零 `/events` mutation，符合当前“先本地可验、再开启真实执行”的阶段约束。

替代方案：

- 在 PCWEB-069 中直接写入 append result audit event：不采用。PCWEB-069 仍是 disabled transaction boundary，不能混入真实 mutation。
- 只保留 transaction disabled run，不增加 audit preview：不采用。后续 enabled append 缺少结果审计事件合同，难以解释“为什么写入、为什么跳过、如何重放”。
- 将 audit preview 合并到正式 report path：不采用。append result audit 属于 live event persistence 层，不是会后报告生成层。

边界：

- 仅 append result audit preview。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成真实 append result audit event。
- 不开启 repository transaction，不 commit，不 rollback。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 即使 `repository_dry_run_status=would_append_if_enabled`，`append_result_audit_event_status` 也必须是 `preview_only`，`audit_event_append_status` 也必须是 `not_appended`，`safe_to_write_audit_events`、`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。
- audit preview identity 不得依赖冒号、下划线等标点折叠后的字符串；真实写入前继续沿用可逆或等价 collision-resistant tuple encoding。
- `transaction_idempotency_key` 仍是未来事务写入的 canonical durable identity；`audit_idempotency_key` 只代表未来 result audit event 的 preview identity。

验证方式：

- 先写 failing tests 覆盖 allowed audit preview、schema-invalid silenced audit preview、policy-blocked silenced audit preview、preflight blocked conflict、missing session、unknown request、JSON persistence bytes unchanged、request shape 422、delimiter collision、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 append result audit preview helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-070-live-asr-card-lifecycle-append-result-audit-preview-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-070-live-asr-card-lifecycle-append-result-audit-preview.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-071：Live ASR card lifecycle retry/replay preflight

日期：2026-07-02

决策：

新增 `PCWEB-071`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-retry-replay-preflights`

该端点复用 PCWEB-070 append result audit preview，然后把每个 future lifecycle append item 映射成 response-only retry/replay check。当前仅支持：

- `mode=preflight_only`

响应必须保留或新增：

- `retry_replay_preflight_mode=preflight_only`
- `retry_replay_preflight_status=analyzed`
- `retry_replay_resolution_status=no_existing_append|safe_to_replay|blocked_by_partial_replay|blocked_by_conflict`
- `retry_replay_check_count`
- `retry_replay_checks`
- `retry_replay_check_id`
- `retry_replay_check_status`
- `resolution_status=no_existing_append|safe_replay_same_event|blocked_mismatched_replay|blocked_existing_idempotency_key|blocked_partial_replay`
- `existing_event_match_status`
- `existing_idempotency_match_status`
- `existing_event_id`
- `existing_idempotency_key`
- `existing_idempotency_conflict_count`
- `existing_idempotency_conflict_event_ids`
- `safe_to_replay_event`
- `safe_to_replay_existing_events`
- `safe_to_mutate_events=false`
- `safe_to_append_events=false`
- `safe_to_create_card=false`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `idempotency_store_write_status=not_written`
- request/draft/candidate/state/gap/evidence/segment linkage
- `append_result_audit_events`
- `append_errors`
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`

原因：

- PCWEB-066 到 PCWEB-070 已经能表达 future append chain，但现有 blocked conflict 不能区分“同一次请求的安全重放”和“别的事件占用了同一 event id/idempotency key”。
- 真实打开 `/events` mutation 前，必须先有明确的幂等 retry/replay 分类，否则后续 enabled append 可能重复写卡、跳过必要事件或错误吞掉冲突。
- `safe_replay_same_event` 不能只看 event id、event type、append idempotency key 和 card id；还必须匹配 request id、request draft event id，以及 `suggestion_card` 的 nested `payload.card.id`，否则可能把不同请求或不同卡片 payload 的历史事件误判成同一事件重放。
- 如果同一 event 同时携带 top-level `idempotency_key` 和 `payload.idempotency_key`，两处 key 必须全部等于预期 append idempotency key；任何内部不一致都必须阻断为 mismatched replay。
- 同一个 append idempotency key 如果同时出现在匹配 lifecycle event 和其他 marker/事件上，必须阻断为 `blocked_existing_idempotency_key`；重复幂等证据代表审计状态不唯一，不能自动重放。
- partial replay 是真实事故恢复中最危险的状态之一，不能因为一部分事件已存在就自动追加剩余事件；当前阶段必须先显式阻断并可观测。
- 保持默认零额外费用、零密钥读取和零 `/events` mutation，符合当前“先本地可验、再开启真实执行”的阶段约束。

替代方案：

- 在 PCWEB-066 append preflight 中直接把已有 event id 视为成功：不采用。PCWEB-066 只负责 append plan 预检，不能证明 existing event 是否同一请求、同一 card identity、同一 idempotency key。
- 在 PCWEB-070 append result audit preview 中混入 retry/replay 逻辑：不采用。PCWEB-070 负责 result audit preview，不应该承担 repository conflict-resolution policy。
- 直接实现 enabled append + retry queue：不采用。会同时引入 repository transaction、idempotency store、audit event write、rollback/recovery 和正式 card lifecycle，风险过大。

边界：

- 仅 retry/replay preflight。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成真实 append result audit event。
- 不开启 repository transaction，不 commit，不 rollback。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 即使 `retry_replay_resolution_status=safe_to_replay`，`safe_to_append_events`、`safe_to_mutate_events` 和 `safe_to_create_card` 也必须保持 false；它只代表未来 enabled path 可以把该请求解释成已应用重放，不代表当前会执行写入。
- `blocked_by_partial_replay` 是独立顶层状态；它不归入 generic `blocked_by_conflict`，便于后续 recovery policy 单独处理“已写一部分、缺一部分”的事故恢复。

验证方式：

- 先写 failing tests 覆盖 no existing append、safe same-event replay、mismatched replay conflict、idempotency-marker conflict、partial replay、missing session、unknown request、JSON persistence bytes unchanged、request shape 422、no-secret-read/no-event-mutation。
- 评审后新增 failing tests 覆盖 same event/key 但 request/draft/card metadata 不一致、匹配 lifecycle event 同时存在重复 idempotency marker、以及同一 event 内 top-level/payload idempotency key 不一致的情况。
- 再实现最小 endpoint、payload validator、event/idempotency indexes 和 retry/replay classifier；idempotency index 必须是一对多，不能只保留第一个匹配事件。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。
- 最终验证：focused PCWEB-071 + README gate 为 13 passed，backend regression 为 231 passed，`pc-web` 为 core 34 passed + web backend 234 passed + browser smoke passed，`all-local --no-browser` 为 ASR runtime 65 passed + ASR bakeoff 18 passed + core 34 passed + web backend 234 passed。

关联文档：

- `docs/pcweb-071-live-asr-card-lifecycle-retry-replay-preflight-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-071-live-asr-card-lifecycle-retry-replay-preflight.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-068：Live ASR card lifecycle append repository dry-run

日期：2026-07-02

决策：

新增 `PCWEB-068`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-repository-dry-runs`

该端点复用 PCWEB-067 disabled append run，然后把每个 append plan / append run 映射成 repository dry-run result envelope。当前仅支持：

- `mode=dry_run_only`

响应必须保留或新增：

- `repository_dry_run_mode=dry_run_only`
- `repository_dry_run_status=would_append_if_enabled|blocked_by_preflight`
- `repository_append_count`
- `repository_results`
- `repository_result_id`
- `repository_result_status=would_append_if_enabled|blocked_by_preflight`
- `repository_idempotency_key`
- `repository_write_status=dry_run_only`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- `append_preflight_status=allowed|blocked`
- `append_errors`
- `append_runs`
- request/draft/candidate/state/gap/evidence/segment linkage
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

审查修正：

- `repository_result_id` 和 `repository_idempotency_key` 使用 percent-encoded tuple components，而不是复用会折叠标点的 display token。`card:dry_run:001` 必须和 `card_dry_run_001` 保持不同 repository identity。
- PCWEB-068 的 no-secret-read 测试护栏扩展到 arbitrary `configs/local/**` path、直接 secret env 读取、keychain adapter、provider config loader 和 outbound LLM/HTTP seam。
- PCWEB-068 的持久化 no-mutation 测试增加 session JSON bytes 前后不变断言。
- README gate 必须断言 `PCWEB-068`、repository dry-run endpoint、`repository_write_status=dry_run_only` 和 no repository transaction 文案。

原因：

- PCWEB-067 已经有 disabled action endpoint，但还没有显式表达未来 repository append transaction 的输入、结果、幂等键和失败分类。
- 真正打开 `/events` mutation 之前，需要先证明 repository 层会如何处理 allowed append、schema/policy silenced append 和 preflight conflict。
- 直接启用真实 repository append 会同时引入持久化事务、idempotency store 写入、重试、冲突恢复、append result audit event 和正式 card lifecycle，风险过大。
- 保持默认零额外费用、零密钥读取和零 `/events` mutation，符合当前“先本地可验、再开启真实执行”的阶段约束。

替代方案：

- 直接把 PCWEB-067 skipped runs 写入 `/events`：不采用。会绕过 repository transaction 和幂等写入合同。
- 只保留 PCWEB-067，不增加 repository dry-run：不采用。后续 enabled append 缺少 repository 层结果 envelope，难以测试重试/冲突恢复。
- 让 PCWEB-068 返回 `safe_to_append_events=true`：不采用。当前没有真实 repository transaction 和 idempotency store 写入，不能给出真实安全承诺。

边界：

- 仅 repository append dry-run。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 即使 `repository_dry_run_status=would_append_if_enabled`，`event_append_status` 也必须是 `not_appended`，`idempotency_store_status` 也必须是 `not_written`，`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。
- dry-run repository identity 不得依赖冒号、下划线等标点折叠后的字符串；真实写入前继续沿用可逆或等价 collision-resistant tuple encoding。

验证方式：

- 先写 failing tests 覆盖 allowed repository dry-run、schema-invalid silenced repository result、policy-blocked silenced repository result、preflight blocked conflict、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 repository dry-run helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-068-live-asr-card-lifecycle-append-repository-dry-run-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-068-live-asr-card-lifecycle-append-repository-dry-run.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 OS keychain adapter、企业 secret provider adapter 或 env secret reader 前，必须单独设计授权、错误响应脱敏、日志脱敏、敏感扫描和平台差异。
- 接入真实 provider config file reader 前，必须引用该策略并证明不会把 secret value 写入 audit event、session JSON、报告、日志或浏览器存储。
- 接入 enabled executor 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。

## DEC-058: Live ASR 增加 LLM provider config reader dry-run 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-056` 已经有 provider config loader preflight，`PCWEB-057` 已经有 secret storage policy，但真实读取 provider config 前仍缺少一个可测的授权读取器边界。直接读取本地 config 文件会立刻涉及路径隐私、文件存在性侧信道、secret reference 解析、keychain/env/企业 secret provider、masked status、日志脱敏、读取审计和 enabled executor。为了继续保持默认零额外费用和不碰真实密钥，需要先实现 dry-run 合同：调用方可以提交未来读取器需要的 path reference、secret reference 和授权状态，但当前端点仍明确阻断读取与执行。

决策：

新增 `PCWEB-058`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-provider-config-reader-dry-run`

端点只校验 future authorized config reader request 的形状，并从 Live ASR audit record 派生 `session_id`、`source` 和 `trace_kind`。成功响应固定表达：

- `dry_run_kind=authorized_config_file_reader`
- `dry_run_status=blocked`
- `dry_run_mode=dry_run_only`
- `provider_protocol=openai_compatible_chat_completions`
- `config_source_status=caller_supplied_path_reference`
- `config_file_status=not_read`
- `config_existence_status=not_checked`
- `secret_reference_status=provided_not_resolved`
- `secret_storage_status=not_connected`
- `credentials_status=not_read`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_read_config=false`
- `safe_to_read_secret=false`
- `safe_to_execute=false`

校验规则：

- request body 必须是 JSON object；非 object 输入返回 generic 422，不回显原始输入。
- top-level fields 必须精确为 `reader_mode`、`provider_protocol`、`config_path`、`secret_reference` 和 `authorization`。
- `reader_mode` 必须是 `dry_run_only`。
- `provider_protocol` 必须是 `openai_compatible_chat_completions`。
- `config_path` 必须是非空本地文件系统路径字符串；拒绝 URL/file URL、NUL/control characters、ASCII DEL、POSIX `/../` 和 Windows `\..\` 路径穿越。
- `secret_reference` 必须精确包含 `reference_type` 和 `reference_id`；`reference_type` 只能是 `keychain_item_reference`、`enterprise_secret_reference` 或 `env_var_name_reference`；`reference_id` 必须是非空字符串且不得包含 control characters 或 ASCII DEL。
- `authorization` 必须精确表达 `user_confirmed_local_config_access=true`、`acknowledged_secret_storage_policy=true`、`allow_config_file_read=false`、`allow_secret_read=false`、`allow_llm_call=false`、`allow_event_mutation=false`。
- 未知字段直接 422，避免 `raw_config`、`api_key`、`authorization_header` 或 bearer token 进入响应合同。

原因：

- 让 future config file reader 的授权请求先可测，而不引入文件读取、密钥读取、keychain 访问或付费 LLM 调用。
- 明确 dry-run 不是 preflight 的重复：preflight 验证 loader 请求形状，dry-run 额外验证 secret reference 和读/密钥/执行/事件变更授权必须全部关闭。
- 阻断路径和 secret reference 泄漏：成功与 422 错误都不返回 raw path、basename、parent directory、path-derived label 或 secret reference id。
- 阻断文件状态侧信道：不检查文件存在性、可读性、大小、mtime、hash 或 fingerprint。
- 继续保持默认无额外费用：不调用远程 ASR/LLM，不估算 token，不生成 schema/card。

边界：

- 只校验 caller-provided JSON request body。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不返回 raw config path、absolute path、basename、parent directory、path-derived label 或 secret reference id；错误响应同样不得回显 URL path、file URL、control-character path、API key、authorization、bearer token 或 raw config。
- 不读取、存储、日志化、mask、hash、截取 prefix/suffix、计算长度或报告 `api_key` presence/validity/fingerprint。
- 不改变 Live ASR audit record，不追加 dry-run event。
- 不发网络请求，不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_reader_dry_run_endpoint_returns_contract_without_reading_config_or_secret -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_config_reader_dry_run_endpoint_accepts_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_config_reader_dry_run_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_config_reader_dry_run_endpoint_returns_404_without_leaking_submitted_path_or_secret_reference tests/test_app.py::test_asr_live_llm_provider_config_reader_dry_run_endpoint_rejects_invalid_requests_without_leaking_submitted_values -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-058-live-asr-llm-provider-config-reader-dry-run-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 provider config file reader 前，必须单独设计读取授权 UI、路径隐私策略、读取审计、错误响应脱敏、日志脱敏、secret reference resolver 和本地配置生命周期。
- 接入真实 OS keychain adapter、企业 secret provider adapter 或 env secret reader 前，必须证明 secret value 不会进入 audit event、session JSON、报告、日志、浏览器存储或仓库文件。
- 接入 enabled executor 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。

## DEC-059: Live ASR 增加 LLM provider masked status loader dry-run 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-054` 已经有 template-only masked provider status envelope，`PCWEB-058` 已经有 authorized config reader dry-run，但二者之间仍缺少一个可测的 UI 装载边界：未来设置页需要知道“授权读取配置后哪些字段可以展示”，同时当前阶段不能读取配置、不能推断状态、不能暴露路径或 secret reference，也不能调用中转站。直接把 masked status loader 接到真实文件读取会引入配置文件侧信道、secret resolver、keychain/env/企业 secret provider、授权 UI、日志脱敏和 enabled executor 风险。因此先实现一个 blocked dry-run 合同，验证 future masked status loader 请求和展示策略。

决策：

新增 `PCWEB-059`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-provider-masked-status-loader-dry-run`

端点只校验 future authorized masked status loader request 的形状，并从 Live ASR audit record 派生 `session_id`、`source` 和 `trace_kind`。成功响应固定表达：

- `dry_run_kind=authorized_masked_status_loader`
- `dry_run_status=blocked`
- `dry_run_mode=masked_status_dry_run_only`
- `provider_protocol=openai_compatible_chat_completions`
- `config_source_status=caller_supplied_path_reference`
- `config_file_status=not_read`
- `config_existence_status=not_checked`
- `secret_reference_status=provided_not_resolved`
- `secret_storage_status=not_connected`
- `credentials_status=not_read`
- `status_value_status=not_inferred`
- `llm_call_status=not_called`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_read_config=false`
- `safe_to_read_secret=false`
- `safe_to_infer_status=false`
- `safe_to_execute=false`

校验规则：

- request body 必须是 JSON object；非 object 输入返回 generic 422，不回显原始输入。
- top-level fields 必须精确为 `loader_mode`、`provider_protocol`、`config_path`、`secret_reference`、`requested_display_fields` 和 `authorization`。
- `loader_mode` 必须是 `masked_status_dry_run_only`。
- `provider_protocol` 必须是 `openai_compatible_chat_completions`。
- `config_path` 必须是非空本地文件系统路径字符串；拒绝 URL/file URL、NUL/control characters、ASCII DEL、POSIX `/../` 和 Windows `\..\` 路径穿越。
- `secret_reference` 必须精确包含 `reference_type` 和 `reference_id`；`reference_type` 只能是 `keychain_item_reference`、`enterprise_secret_reference` 或 `env_var_name_reference`；`reference_id` 必须是非空字符串且不得包含 control characters 或 ASCII DEL。
- `requested_display_fields` 必须是非空唯一列表，字段只能来自 `base_url_origin`、`model`、`timeout_seconds`、`ca_bundle_name` 和 `api_key`。
- `authorization` 必须精确表达 `user_confirmed_local_config_access=true`、`acknowledged_secret_storage_policy=true`、`allow_config_file_read=false`、`allow_secret_read=false`、`allow_llm_call=false`、`allow_event_mutation=false`、`allow_status_value_inference=false`。
- 未知字段直接 422，避免 `raw_config`、`api_key`、`authorization_header` 或 bearer token 进入响应合同。

原因：

- 让 future masked provider status loader 的 UI 请求先可测，而不引入文件读取、密钥读取、状态推断或付费 LLM 调用。
- 明确 masked status loader dry-run 不是 PCWEB-054 的重复：PCWEB-054 返回静态模板，PCWEB-059 验证 future loader request、requested display fields、secret reference 和 authorization envelope。
- 阻断路径和 secret reference 泄漏：成功与 422/404 错误都不返回 raw path、basename、parent directory、path-derived label 或 secret reference id。
- 阻断文件状态与密钥状态侧信道：不检查文件存在性、可读性、大小、mtime、hash 或 fingerprint；不返回 API key value、masked key、presence、validity、length、hash、prefix、suffix 或 fingerprint。
- 继续保持默认无额外费用：不调用远程 ASR/LLM，不估算 token，不生成 schema/card。

边界：

- 只校验 caller-provided JSON request body。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不从 path、env、config 或 secret reference 推断 `base_url_origin`、`model`、`timeout_seconds`、`ca_bundle_name` 或 provider readiness。
- 不返回 raw config path、absolute path、basename、parent directory、path-derived label 或 secret reference id；错误响应同样不得回显 URL path、file URL、control-character path、API key、authorization、bearer token 或 raw config。
- `display_values` 全部为 null；`api_key` 固定 `never_display` / `never_return_value_or_mask`。
- 不读取、存储、日志化、mask、hash、截取 prefix/suffix、计算长度或报告 `api_key` presence/validity/fingerprint。
- 不改变 Live ASR audit record，不追加 dry-run event。
- 不发网络请求，不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_returns_contract_without_reading_or_inferring_status -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_accepts_transcript_only_session tests/test_app.py::test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_returns_404_without_leaking_submitted_path_or_secret_reference tests/test_app.py::test_asr_live_llm_provider_masked_status_loader_dry_run_endpoint_rejects_invalid_requests_without_leaking_submitted_values -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-059-live-asr-llm-provider-masked-status-loader-dry-run-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 masked status loader 前，必须先完成 authorized config file reader、secret reference resolver、读取授权 UI、错误响应脱敏、日志脱敏和 status-value inference 策略。
- 接入真实 OS keychain adapter、企业 secret provider adapter 或 env secret reader 前，必须证明 secret value 不会进入 audit event、session JSON、报告、日志、浏览器存储或仓库文件。
- 接入 enabled executor 前，必须先设计 LLM gateway adapter、schema validation、token/cost accounting、timeout/retry/degradation 和 card show/silence lifecycle。

## DEC-060: Live ASR 增加 OpenAI-compatible request body preview 端点

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-048` 已经把 future LLM request draft 写入 no-LLM audit event，`PCWEB-049` 提供 request draft 查询，`PCWEB-050` 提供 execution preview。但真实中转站接入前仍缺少最关键的合同：未来到底会向 OpenAI-compatible chat completions 发送怎样的 request body。继续只做 provider 配置边界会让产品价值链偏慢；因此新增一个纯本地、只读、无调用的 request body preview，让 ASR -> state candidate -> suggestion candidate -> request draft -> future OpenAI request body 的链路可测。

决策：

新增 `PCWEB-060`，提供端点：

- `GET /live/asr/sessions/{session_id}/llm-openai-request-body-previews`

端点从 Live ASR audit record 中的 `llm_request_draft_event` 派生 deterministic OpenAI-compatible chat completions request body preview。成功响应固定表达：

- `provider_protocol=openai_compatible_chat_completions`
- `preview_status=body_preview_only`
- `llm_call_status=not_called`
- `credentials_status=not_read`
- `config_source_status=not_read`
- `schema_status=not_generated`
- `card_status=not_created`
- `cost_status=not_estimated`
- `safe_to_execute=false`

每个 preview 包含：

- request body preview id、request id、request draft event id、request draft sequence。
- target candidate/state、gap rule、source event/evidence/segment linkage。
- OpenAI-compatible `endpoint_family=chat_completions`、`http_method=POST`、`request_path=/v1/chat/completions`。
- `model=not_configured`、固定 preview defaults `temperature=0.2`、`max_output_tokens=600`。
- deterministic `messages`：一个 system message 和一个由 request draft metadata 派生的 user message。
- `response_format` 只声明 `SuggestionCardV1` schema target 和 `strict=true`，不生成完整 JSON Schema。
- metadata：request origin、source events、evidence ids、segment batch 和 candidate quality signals。
- forbidden request fields：`api_key`、`authorization`、`bearer_token`、`base_url`、`raw_config`、`config_path`。

原因：

- 让未来 enabled executor 的最核心输入先可测，而不引入付费调用、密钥读取或 provider config lifecycle。
- 明确 request body preview 不是 execution：没有 URL/base URL、没有 API key、没有 Authorization header、没有网络请求、没有 token/cost estimate。
- 保持产品价值路径前进：用户关心的是实时建议是否能形成，此端点把候选建议到 LLM prompt envelope 的边界具体化。
- 为后续真实 LLM gateway adapter、schema validation、cost accounting 和 card lifecycle 提供稳定输入合同。

边界：

- 只读。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不返回或派生 base URL、API key、authorization header、bearer token、raw config 或 config path。
- 不发网络请求，不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成完整 JSON Schema。
- 不做 schema validation。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 preview event。
- 不启动 enabled-mode fallback、后台 worker、retry queue、cursor 或分页。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_preview_queue_without_calling_llm tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_empty_queue_for_transcript_only_session tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_404_for_missing_session -q`
- `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-060-live-asr-openai-request-body-preview-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 LLM gateway adapter 前，必须先完成 provider config reader、secret storage adapter、timeout/retry/degradation、token/cost accounting、schema validation 和 logging redaction。
- 将完整 JSON Schema 写入 request body 前，必须单独评审 schema size、provider compatibility、schema validation failure lifecycle 和浏览器/日志脱敏边界。
- 将 request body preview 变成 enabled execution 前，必须证明请求、响应、错误、报告、audit event、session JSON 和浏览器存储不会包含 API key、Authorization header 或 raw provider config。

## DEC-061: Live ASR OpenAI request body preview 增加 draft payload redaction guard

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-060` 已经能从 no-LLM `llm_request_draft_event` 派生 OpenAI-compatible chat completions request body preview。该 preview 会把 draft payload 中的 `suggested_prompt`、`input_summary`、`request_origin`、`source_event_ids`、`evidence_span_ids` 和 `segment_batch` 反射到浏览器可见的 messages/metadata。当前 draft producer 是本地确定性逻辑，不包含 provider config 或密钥；但未来如果接入更多上游，draft payload 可能意外包含 `api_key`、Bearer、Authorization、raw_config、base_url、config_path、`configs/local` 或 relay domain 等敏感标记。

决策：

新增 `PCWEB-061`，在既有端点中加入本地 redaction guard：

- `GET /live/asr/sessions/{session_id}/llm-openai-request-body-previews`

端点仍只读、仍不改变原始 Live ASR audit record，但在生成 preview messages/metadata 前，对 draft payload 的可反射字符串做本地 deterministic 检测。命中以下高风险 marker 时，将整个字符串替换为：

- `[redacted:sensitive_draft_value]`

检测 marker：

- API-key-like token：`sk-...`
- bearer token phrase：`Bearer ...`
- authorization header marker：`Authorization:`
- raw config marker：`raw_config`
- API key marker：`api_key`
- base URL marker：`base_url`
- config path marker：`config_path`
- local config directory marker：`configs/local`
- relay domain marker

成功响应新增：

- `redaction_policy=local_sensitive_draft_value_guard.v1`
- `redaction_status=applied|not_needed`
- `redacted_preview_count=<number>`

每个 preview 新增：

- `redaction_status=applied|not_needed`
- `redacted_fields=[...]`

原因：

- request body preview 是未来 LLM 调用前最接近 prompt 的浏览器可见合同，必须先防止 draft payload 把敏感内容反射进 prompt。
- 保持 audit 真实性：`/events` 和 `/llm-request-drafts` 不被改写，仍保留原始输入；只有 request body preview 的 messages/metadata 输出被保护。
- 该 guard 不是完整 DLP，也不判断 secret 是否真实；它只阻断当前阶段最容易误反射的本地高风险标记。

边界：

- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成完整 JSON Schema。
- 不做 schema validation。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 preview/redaction event。
- 不改变 `/live/asr/sessions/{session_id}/llm-request-drafts`。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_redacts_sensitive_draft_payload_without_mutating_record -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_preview_queue_without_calling_llm tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_redacts_sensitive_draft_payload_without_mutating_record tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_empty_queue_for_transcript_only_session tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_404_for_missing_session -q`
- `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-061-live-asr-openai-request-body-preview-redaction-guard-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 LLM gateway adapter 前，必须把该 preview redaction guard 与日志脱敏、错误脱敏、request/response 存储策略、schema validation failure lifecycle 一起复审。
- 扩展 draft payload 字段或允许外部 provider 生成 draft 前，必须扩展 redaction guard 测试覆盖新增可反射字段。
- 若未来需要展示部分安全文本而不是整值 redaction，必须单独评审 partial masking 是否会泄漏 secret prefix/suffix/length/fingerprint。

## DEC-062: Live ASR OpenAI request body preview 增加 SuggestionCardV1 schema outline

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-060` 已经把 no-LLM request draft 转成 OpenAI-compatible chat completions request body preview，`PCWEB-061` 已经保护 draft payload 不把敏感 marker 反射进 messages/metadata。但 `response_format` 仍只声明 `SuggestionCardV1` 名称和 `strict=true`，没有把未来模型输出需要满足的字段合同展示出来。继续接 provider 配置或 enabled executor 前，需要先让 LLM 输出、schema validator、card lifecycle 和前端展示对齐同一个本地合同。

决策：

新增 `PCWEB-062`，在既有端点中扩展 `response_format.json_schema`：

- `GET /live/asr/sessions/{session_id}/llm-openai-request-body-previews`

每个 preview 的 `response_format.json_schema` 继续保持：

- `name=SuggestionCardV1`
- `strict=true`

并新增 outline-only 元数据：

- `schema_outline_status=outline_only`
- `schema_outline_source=local_contract_preview`
- `schema_outline.type=object`
- `schema_outline.required=[id,type,evidence_span_ids,state_refs,state_event_ids,gap_rule_id,trigger_reason,trigger_source,final_segment_at_ms,state_event_at_ms,card_created_at_ms,latency_ms,prompt_version,model,usage,schema_result,show_or_silence_decision,segment_batch,status]`
- `schema_outline.optional=[title,suggested_question]`
- `schema_outline.properties` 返回基础 type hints。
- `schema_outline.additional_properties_status=allowed_by_local_contract_extra`

该 outline 以当前本地 `SuggestionCardV1` dataclass/to_dict 合同为准：`status` 虽有默认值，但当前 `to_dict()` 总会输出，因此列入 required；`title` 和 `suggested_question` 只有非空时输出，因此列入 optional；`extra` 通过 `additional_properties_status` 明确为本地合同允许扩展，而不是正式 JSON Schema 语义。

原因：

- 真实 LLM 接入前，必须先固定“模型应该产出什么卡片结构”，否则 enabled executor、schema validator 和 card UI 会在后续实现时漂移。
- 使用 outline-only 而不是完整 JSON Schema，可以先暴露合同和测试边界，同时避免过早承诺 provider-specific schema dialect、schema size、validation failure lifecycle。
- 这一步直接服务产品亮点：实时会议建议需要稳定、可验证的结构化建议卡，而不只是音频转文字或 prompt 文本拼接。

替代方案：

- 直接生成完整 JSON Schema：暂不采用。需要单独评审 provider 兼容性、schema 严格度、错误生命周期、日志/浏览器脱敏和成本影响。
- 暂时继续只声明 schema name：不采用。会让未来 enabled executor 的输出合同不够可测，风险后移。
- 通过 dataclass 运行时 introspection 自动生成 outline：暂不采用。当前 Web backend 保持单文件局部 helper 风格，静态 outline 更可控；如果 core contract 变化，测试会提示同步更新。

边界：

- 只读。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不估算 token 或扣预算。
- 不生成 provider-specific 完整 JSON Schema。
- 不做 schema validation。
- 不解析模型响应。
- 不生成 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 schema outline preview event。
- 不改变 PCWEB-061 redaction 语义；redaction 仍只保护 messages/metadata。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_includes_suggestion_card_schema_outline_without_mutating_events -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_preview_queue_without_calling_llm tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_redacts_sensitive_draft_payload_without_mutating_record tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_includes_suggestion_card_schema_outline_without_mutating_events tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_empty_queue_for_transcript_only_session tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_openai_request_body_previews_endpoint_returns_404_for_missing_session -q`
- `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-062-live-asr-openai-request-body-schema-outline-preview-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 schema validator 前，必须把 outline-only preview 升级路线、失败分类、重试/降级、日志脱敏和浏览器展示边界一起评审。
- 将 outline 变成完整 JSON Schema 前，必须单独评审 provider 兼容性、schema size、required/optional 严格度、additional properties 策略和模型输出失败时的卡片生命周期。
- `SuggestionCardV1` core contract 发生字段变化时，必须同步更新 PCWEB-062 outline、测试和相关文档。

## DEC-063: Live ASR 增加 LLM schema validation dry-run

- 日期：2026-07-02
- 状态：Accepted

背景：

`PCWEB-060` 已定义 future OpenAI-compatible request body，`PCWEB-061` 已保护 prompt preview 反射，`PCWEB-062` 已暴露 outline-only `SuggestionCardV1` 输出合同。下一步如果直接接 enabled executor，会同时引入真实 LLM 响应解析、schema validation、卡片生命周期、费用和密钥读取，风险过大。需要先有一个无调用、无费用、无事件写入的 dry-run 入口，用来验证“如果未来模型返回这段 card-shaped JSON，本地合同会如何判定”。

决策：

新增 `PCWEB-063`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-schema-validation-dry-runs`

请求体只允许：

- `mode=dry_run_only`
- `request_id=<existing llm_request_draft_event.payload.request_id>`
- `candidate_response=<object>`

端点从 Live ASR audit record 查找对应 request draft，并对 caller-provided `candidate_response` 做本地 dry-run 校验。成功或失败的候选响应都返回 200：

- `validation_status=passed|failed`
- `schema_validation_status=dry_run_passed|dry_run_failed`
- `schema_result_status=not_generated`
- `card_status=not_created`
- `llm_call_status=not_called`
- `credentials_status=not_read`
- `config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_create_card=false`
- `validation_errors=[{field,code,message}]`

请求体 shape 错误返回 422；missing session 和 unknown request id 返回 404。

校验子集：

- `SuggestionCardV1.from_dict()` 本地合同。
- required 字段存在。
- `usage.total_tokens` 存在、为非负整数。
- `schema_result` 属于 `valid|failed|timeout|invalid`。
- `failed|timeout|invalid` 不得作为强建议通过。
- 时间字段为非负整数。
- `state_event_at_ms >= final_segment_at_ms`。
- `card_created_at_ms >= state_event_at_ms`。
- `latency_ms == card_created_at_ms - final_segment_at_ms`。

原因：

- 继续靠近真实实时建议价值链，但不提前打开付费调用和密钥读取。
- 让未来 enabled executor 的响应验收、失败分类和 card lifecycle 前置可测。
- 将“请求体错误”和“候选响应业务校验失败”区分开：前者 422，后者 200 + `dry_run_failed`，方便 UI/调试工具展示模型输出为何不能成卡。
- 不把 dry-run 误装成真实 `llm_schema_result`，避免污染 Live ASR audit。

替代方案：

- 直接实现 enabled executor：暂不采用。它需要 provider config reader、secret resolver、LLM gateway、response parser、schema validator、cost accounting、retry/degradation 和卡片生命周期一起设计。
- 在 `llm-execution-runs` 增加 `mode=enabled_dry_run`：暂不采用。当前 execution endpoint 的设计含义是 executor action boundary；schema validation dry-run 是独立的 response validation boundary，单独端点更清晰。
- 失败候选响应返回 422：不采用。候选响应 schema 不通过是 dry-run 的业务结果，不是 API request shape 错误。

边界：

- 仅 dry-run。
- 不读取 `configs/local/`。
- 不读取 `MEETING_COPILOT_LLM_CONFIG` 指向文件。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成 provider-specific 完整 JSON Schema。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 validation event。
- 不执行 cross-link enforcement；candidate response evidence/state/segment 是否必须与 request draft 完全一致留给后续 card creation policy。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_passes_candidate_response_without_calling_llm tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_reports_candidate_errors_without_creating_card tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_returns_404_for_unknown_request_id tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_returns_404_for_missing_session tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_request_shape_errors -q`
- `python3 -m pytest tests/test_app.py::test_web_mvp_readme_documents_scripted_browser_e2e_gate -q`
- `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`
- `python3 tools/run_quality_gate.py --profile pc-web`
- `python3 tools/run_quality_gate.py --profile all-local --no-browser`

关联文档：

- `docs/pcweb-063-live-asr-llm-schema-validation-dry-run-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

复审条件：

- 接入真实 LLM response parser 前，必须单独设计 chat-completions 响应 envelope、tool/json_schema 兼容性、错误脱敏和日志策略。
- 将 dry-run 结果转成真实 `llm_schema_result` event 前，必须评审 event mutation、幂等、失败重试、silence/show 决策和 card lifecycle。
- 引入 cross-link enforcement 前，必须定义 candidate response 与 request draft 的 evidence/state/segment 不一致时如何降级、提示或阻断。

## DEC-063A：PCWEB-063 schema validation dry-run 审查加固

日期：2026-07-02

决策：

- 保留 `PCWEB-063` 功能号，不新开业务能力；本次是审查后的合同加固。
- 在 PCWEB-063 dry-run 层增加严格 JSON integer 校验：`usage.total_tokens`、`final_segment_at_ms`、`state_event_at_ms`、`card_created_at_ms` 和 `latency_ms` 只接受 `type(value) is int`，显式拒绝 float、boolean 和 numeric string。
- 在请求 shape 层增加严格类型校验：`mode` 和 `request_id` 必须是 string，非 string 直接 422，不再用 `str(...)` 强转。
- 不改 `meeting_copilot_core.contracts.SuggestionCardV1` 的 `_required_int()`，避免影响已有 fixture、历史 JSON 和 core gate 的兼容面；PCWEB-063 作为 future model response dry-run boundary，可以在 Web/API 层施加更严格的模型输出合同。
- 暂不重命名 `validated_field_count`。它当前代表 dry-run contract 字段数，虽然名称可更精确，但已写入响应合同和文档，改名应留给后续 API revision。

原因：

- 审查发现原实现使用 `int(...)`，会让 `1.9`、`true`、`"0"` 这类 malformed candidate response 误通过，破坏“模型输出能否进入卡片生命周期”的前置验证价值。
- 真实 LLM response parser 接入前，dry-run 必须先建立严格、可重复、可解释的错误分类。
- 保持加固范围小，只修 PCWEB-063 边界，不引入真实 provider config、secret、LLM call、event mutation 或 card lifecycle。

新增验收：

- `mode/request_id` 的 numeric、boolean、null、object/array 值必须返回 422。
- `usage.total_tokens` 的 float、boolean、numeric string、negative 值必须返回 200 + `dry_run_failed` 和 deterministic `validation_errors`。
- timing 字段的 float、boolean、numeric string、negative、错误时间顺序和 latency 不一致必须返回 200 + `dry_run_failed`。
- blocking `schema_result` 只允许出现在非强卡 envelope 中，不能作为强建议通过。

验证方式：

- `python3 -m pytest tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_non_integer_candidate_fields tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_allows_blocking_schema_result_only_for_non_strong_cards tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_request_shape_errors -q`
- `python3 -m pytest tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_passes_candidate_response_without_calling_llm tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_reports_candidate_errors_without_creating_card tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_non_integer_candidate_fields tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_allows_blocking_schema_result_only_for_non_strong_cards tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_returns_404_for_unknown_request_id tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_reads_persisted_record_across_app_instances tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_returns_404_for_missing_session tests/test_app.py::test_asr_live_llm_schema_validation_dry_run_endpoint_rejects_request_shape_errors -q`

## DEC-064：Live ASR card creation policy dry-run

日期：2026-07-02

决策：

新增 `PCWEB-064`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-creation-policy-dry-runs`

该端点复用 PCWEB-063 的 caller-provided `candidate_response` schema validation dry-run，然后进一步校验“未来是否允许创建正式建议卡”的本地策略：

- candidate 与 request draft 的 `gap_rule_id` 必须一致。
- candidate 与 request draft 的 `evidence_span_ids`、`segment_batch`、`state_event_ids/source_event_ids` 必须一致。
- candidate `state_refs` 必须包含 request draft target 派生出的 state ref。
- evidence 必须存在；强建议只能引用 active evidence。
- state event 必须存在，且 target 必须匹配 candidate state ref。
- segment 必须存在，`final_segment_at_ms` 必须匹配 segment batch 的最大 final/revision event time。
- `state_event_at_ms` 必须匹配 referenced state event 的最大 event time。
- 强建议必须在 30 秒实时窗口内。
- 只有 `schema_result=valid`、`show_or_silence_decision=show`、`status=new` 且 request draft 无 candidate degradation reasons 时，才返回 `would_create_card_if_enabled=true`。

响应仍然保持 dry-run：

- `policy_status=allowed|blocked`
- `card_creation_policy_status=dry_run_allowed|dry_run_blocked`
- `schema_validation_status=dry_run_passed|dry_run_failed`
- `schema_result_status=not_generated`
- `card_status=not_created`
- `llm_call_status=not_called`
- `credentials_status=not_read`
- `config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_create_card=false`
- `would_create_card_if_enabled=true|false`
- `would_silence_candidate_if_enabled=true|false`
- `policy_errors=[{field,code,message}]`

原因：

- PCWEB-063 只回答“是不是 card-shaped JSON”，没有回答“这张卡是否真的可展示给用户”。
- 实时会议产品的价值边界在 evidence-grounded、time-aware、state-linked 的建议，而不是任意通过 schema 的 JSON。
- 直接创建正式卡片会把 response parser、真实 `llm_schema_result`、card persistence、silence lifecycle、幂等和用户反馈一起引入，风险过大。
- 先做 policy dry-run 可以把成卡资格规则前置成可测合同，同时继续保持默认零额外费用和零密钥读取。

替代方案：

- 直接将 PCWEB-063 通过的候选响应写为 `suggestion_card`：不采用。它会跳过 evidence/state/linkage/timing policy，容易让错误建议进入 UI。
- 直接实现真实 `llm_schema_result` event：不采用。它需要 enabled executor、response parser、事件写入幂等和失败重试策略一起设计。
- 把 policy 规则塞回 PCWEB-063：不采用。schema validation 和 card creation policy 是两个不同边界，分开更容易审查和回归。

边界：

- 仅 dry-run。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 policy event。
- 即使 `would_create_card_if_enabled=true`，`safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 allowed、schema blocked、linkage mismatch、stale/missing evidence、state/segment/timing mismatch、too-late strong card、candidate degradation、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint 和 policy helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-064-live-asr-card-creation-policy-dry-run-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-065：Live ASR card lifecycle preview dry-run

日期：2026-07-02

决策：

新增 `PCWEB-065`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-preview-dry-runs`

该端点复用 PCWEB-063 的 schema validation dry-run 和 PCWEB-064 的 card creation policy dry-run，然后进一步预览“未来 enabled lifecycle 会追加哪些事件”：

- schema validation 通过且 card creation policy 允许时，预览 `llm_schema_result` 和 `suggestion_card`。
- schema validation 失败时，预览 `llm_schema_result` 和 `suggestion_silenced`，`silence_reason=schema_validation_failed`。
- schema validation 通过但 card creation policy 阻断时，预览 `llm_schema_result` 和 `suggestion_silenced`，`silence_reason=card_creation_policy_blocked`。
- preview events 只出现在响应体中，必须带 `preview_only=true` 和 `would_append_if_enabled`，不得写入 Live ASR audit record。

响应必须保留：

- `future_lifecycle_status=would_create_card|would_silence_candidate`
- `would_append_event_types_if_enabled`
- `preview_events`
- request/draft/candidate/state/gap/evidence/segment linkage
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

原因：

- PCWEB-064 只回答“未来是否允许创建卡片”，还没有回答“如果真实 lifecycle 启用，会留下哪些可审计事件”。
- 实时会议产品的可信度来自完整链路：ASR evidence -> state -> request draft -> schema result -> card/silenced lifecycle；PCWEB-065 先把最后一段事件形状前置成可测合同。
- 直接写真实 `llm_schema_result`、`suggestion_card` 或 `suggestion_silenced` 会同时引入 event mutation、幂等、repository lifecycle、feedback lifecycle、真实 response parser 和 enabled executor，风险过大。
- 继续保持默认零额外费用和零密钥读取，把付费调用压到最后的 enabled executor 开关之后。

替代方案：

- 直接在 PCWEB-064 allowed 时创建 `suggestion_card`：不采用。会跳过 lifecycle preview、幂等和真实事件持久化设计。
- 只返回 `would_create_card_if_enabled=true/false`，不预览事件：不采用。这样前端和审计链路仍不知道未来事件形状。
- 先实现真实 `llm_schema_result` 持久化：不采用。它需要真实 response parser、event mutation 幂等和失败重试策略一起设计。

边界：

- 仅 dry-run lifecycle preview。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 lifecycle event。
- 即使 future lifecycle 会创建卡片，`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 allowed lifecycle preview、schema-invalid silenced preview、policy-blocked silenced preview、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint 和 lifecycle preview helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-065-live-asr-card-lifecycle-preview-dry-run-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-066：Live ASR card lifecycle append preflight dry-run

日期：2026-07-02

决策：

新增 `PCWEB-066`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-preflight-dry-runs`

该端点复用 PCWEB-065 的 lifecycle preview，然后进一步预检“如果未来真的把这些 preview events 写入 Live ASR audit record，事件 ID、幂等键和追加顺序是否安全”。

响应必须保留：

- `append_preflight_mode=dry_run_only`
- `append_preflight_status=allowed|blocked`
- `append_plan`
- `append_errors`
- `existing_event_count`
- `last_existing_sequence`
- `append_plan_count`
- `future_event_id`
- `idempotency_key`
- `would_append_sequence`
- `would_append_after_sequence`
- request/draft/candidate/state/gap/evidence/segment linkage
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

原因：

- PCWEB-065 已经知道未来会产生哪些 lifecycle events，但还没有证明这些事件在真实 audit log 中可以安全地“只追加一次、按顺序追加、不覆盖已有事件”。
- 真实会中 LLM response 可能被重试、重复提交或延迟到达；如果没有幂等键和事件冲突预检，正式卡片和用户反馈链路会不可信。
- 直接启用真实 event mutation 会同时引入 repository append API、幂等存储、重试冲突处理和正式 card lifecycle，风险过大。
- 继续保持默认零额外费用、零密钥读取和零 `/events` mutation，把付费调用和真实写入继续压到后续 enabled lifecycle 之后。

替代方案：

- 直接把 PCWEB-065 preview events 写入 `/events`：不采用。会提前打开 mutation、幂等和回滚问题。
- 只在响应里返回 `safe_to_append_events=true`：不采用。当前没有真实 append repository/idempotency store，不能给出真实安全承诺。
- 等 enabled executor 完成后再补幂等：不采用。会让真实 LLM/card lifecycle 在最危险的地方缺少前置合同。

边界：

- 仅 append preflight dry-run。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 即使 `append_preflight_status=allowed`，`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 allowed append preflight、schema-invalid silenced append plan、policy-blocked silenced append plan、existing event/idempotency conflict、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint 和 append preflight helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-066-live-asr-card-lifecycle-append-preflight-dry-run-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`

## DEC-067：Live ASR card lifecycle append disabled run

日期：2026-07-02

决策：

新增 `PCWEB-067`，提供端点：

- `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-append-runs`

该端点复用 PCWEB-066 append preflight，然后把每个 append plan item 映射成 skipped append run envelope。当前仅支持：

- `mode=disabled`

响应必须保留或新增：

- `append_run_mode=disabled`
- `append_run_status=skipped`
- `append_run_count`
- `append_runs`
- `append_preflight_status=allowed|blocked`
- `append_errors`
- `future_event_id`
- `idempotency_key`
- `preflight_idempotency_key`
- `preflight_append_status`
- `preflight_conflict_status`
- `skip_reason=event_append_disabled|append_preflight_blocked`
- `event_append_status=not_appended`
- `idempotency_store_status=not_written`
- request/draft/candidate/state/gap/evidence/segment linkage
- `validation_errors`
- `policy_errors`
- `llm_call_status=not_called`
- `credentials_status=config_source_status=not_read`
- `cost_status=not_estimated`
- `safe_to_append_events=false`
- `safe_to_create_card=false`

原因：

- PCWEB-066 已能证明未来 append plan 的事件 ID、幂等键和追加顺序是否安全，但还没有一个明确的 action endpoint 表达“当前 append 仍禁用”。
- 未来真实桌面端会需要从 LLM response -> schema result -> card/silenced lifecycle -> audit event append 的可调用边界；先落 disabled run 能锁住 API shape、幂等链路和无副作用行为。
- 直接启用真实 event append 会同时引入 repository append API、idempotency store、重试、冲突恢复、正式卡片持久化和用户反馈链路，风险过大。
- 保持默认零额外费用、零密钥读取和零 `/events` mutation，符合当前“先本地可验、再开启真实执行”的阶段约束。

替代方案：

- 直接把 PCWEB-066 append plan 写入 `/events`：不采用。会提前打开 mutation、幂等写入和回滚问题。
- 只保留 PCWEB-066 preflight，不增加 action endpoint：不采用。后续 enabled append 缺少与 executor 类似的动作边界，调用方无法区分“已预检”和“已尝试执行但禁用”。
- 在 `llm-execution-runs` 中顺带返回 lifecycle append runs：不采用。LLM execution 与 card lifecycle event persistence 是两个不同阶段，混在一起会模糊边界。

边界：

- 仅 disabled append-run boundary。
- 不读取 `configs/local/`。
- 不读取环境变量中的 secret、provider config、API key 或 authorization header。
- 不访问 macOS keychain、Windows Credential Manager、企业 secret provider 或任何 secret adapter。
- 不调用 `load_llm_gateway_config`。
- 不检查 provider config file 是否存在、是否可读、大小、mtime、权限、hash 或 fingerprint。
- 不调用远程 ASR/LLM 或中转站。
- 不解析真实 chat-completions response envelope。
- 不估算 token 或扣预算。
- 不生成真实 `llm_schema_result`。
- 不生成正式 `suggestion_card`。
- 不生成真实 `suggestion_silenced`。
- 不生成 append result audit event。
- 不改变 Live ASR audit record，不追加 lifecycle event，不写入 idempotency store。
- 即使 `append_preflight_status=allowed`，`append_run_status` 也必须是 `skipped`，`safe_to_append_events` 和 `safe_to_create_card` 也必须保持 false。

验证方式：

- 先写 failing tests 覆盖 allowed disabled append run、schema-invalid silenced run、policy-blocked silenced run、preflight blocked conflict、missing session、unknown request、JSON persistence、request shape 422、no-secret-read/no-event-mutation。
- 再实现最小 endpoint、payload validator 和 disabled run helper。
- 通过 focused tests 后，运行 `python3 -m pytest tests/test_app.py tests/test_live_events.py -q`、`python3 tools/run_quality_gate.py --profile pc-web`、`python3 tools/run_quality_gate.py --profile all-local --no-browser`。

关联文档：

- `docs/pcweb-067-live-asr-card-lifecycle-append-disabled-run-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-067-live-asr-card-lifecycle-append-disabled-run.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/end-to-end-design-checklist.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`
- `docs/privacy-and-data-flow.md`
- `code/web_mvp/README.md`
## DEC-091：PCWEB-091 创建 Tauri no-op shell local run smoke readiness boundary

日期：2026-07-02

状态：Accepted

背景：

PCWEB-082 已创建 Tauri 静态 scaffold，PCWEB-090 已把首次 `cargo check` 收束为 explicit manual execution packet。下一步需要靠近真实桌面壳，但当前仍没有显式授权运行 Cargo/Tauri、抓依赖、生成 lock/target 或请求音频权限。因此需要先定义 Tauri no-op shell local run smoke 的 no-command readiness boundary，避免把“scaffold 静态可验证”误解成“可以自动运行 Tauri”。

决策：

新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。policy/report 模式固定为 `readiness_report_only`，验证 PCWEB-082 scaffold、`devUrl=http://127.0.0.1:8765/`、`frontendDist`、minimal capability、exact no-op command catalog、generated artifact blockers 和 PCWEB-090 no-command boundary。全部验证通过时最多返回 `smoke_packet_status=ready_for_explicit_tauri_run_approval`。

原因：

- 真实桌面壳是当前主线，继续扩 fixture-only dry-run 会降低有效进展。
- 直接运行 Tauri 会过早引入 Rust/Cargo dependency fetch、lock/target artifacts、环境差异和清理策略。
- 先静态验证 no-op shell smoke 条件，可以让后续真实 run approval 更可审计。

替代方案：

- 直接运行 Tauri：不采用。当前没有显式 Tauri run approval，也没有生成/清理 artifacts 的执行记录。
- 继续做 ASR/provider 横评：不采用作为主线。ASR 评测已收束为 targeted gate。
- 提前加入音频 command：不采用。音频采集属于 PCWEB-093+ Mac adapter contract。

边界：

- 保持 `safe_to_run_tauri_dev_now=false`。
- 保持 `safe_to_run_cargo_check_now=false`。
- 保持 `safe_to_capture_audio_now=false`。
- 不运行 Tauri/Cargo/package manager。
- 不抓依赖，不生成 `Cargo.lock`、target、installer、签名或公证产物。
- 不请求麦克风或系统音频权限。
- 不启动 ASR worker。
- 不读取 provider config、`configs/local` 或密钥。
- 不调用远程 ASR/LLM。

验证方式：

- `tests/test_desktop_tauri_noop_shell_run_smoke.py` 证明 policy 和工具默认 `readiness_report_only`、valid scaffold 只返回 `ready_for_explicit_tauri_run_approval`、`safe_to_run_tauri_dev_now=false` 和 `safe_to_capture_audio_now=false`，并覆盖 devUrl/bundle/capability/command catalog/generated artifact/PCWEB-090 policy/forbidden path drift blocking。
- Web README docs gate 证明 PCWEB-091 已落到 README、desktop README、traceability、acceptance、privacy/data-flow、project structure、roadmap、decision log、current status、progress report 和 stage status。

关联文档：

- `docs/pcweb-091-tauri-noop-shell-local-run-smoke-plan.md`
- `docs/superpowers/specs/2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke-design.md`
- `docs/superpowers/plans/2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke.md`
- `docs/requirements-traceability-matrix.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/implementation-roadmap.md`

复审条件：

如果用户另起显式批准真实 Tauri/Cargo run，必须新建后续增量记录实际命令、artifact policy、清理策略和验证结果；不得把 PCWEB-091 的 readiness report 当成执行授权。

## DEC-093：公开视频和合成音频先行，真实麦克风会议由用户最终验证

日期：2026-07-02

状态：Accepted

2026-07-03 superseded note：本决策中的“公开视频”旧措辞已被 DEC-156 / DEC-158 收窄为“官方授权公开数据集 / no-download manifest”。后续不得抓取 Bilibili、YouTube、播客、直播回放、公开课、公开视频或版权链不清平台音频；公开音频只保留 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111 和 AISHELL-1/OpenSLR SLR33 等官方授权来源的 no-download 路径。

背景：

用户确认：转写相关验证需要我先去网上寻找公开视频/公开数据集并进行模拟，最终真实麦克风会议由用户自己验证。此前项目已有 ASR bake-off、Web MVP 和桌面 runtime validation 计划，但需要把“公开/合成音频前置自测”和“真实麦克风最终验收”的职责边界单独写入文档，避免后续误读真实用户录音、引入隐藏付费 ASR 或把 synthetic 成功误判成真实产品成功。

决策：

下一阶段转写验证采用三层数据路径：

```text
公开授权中文音频 / 公开会议数据集
  -> 验证 ASR、多人会议声学、切句、延迟和 event contract

自建中文技术会议脚本 + 本地合成/模拟音频
  -> 验证技术实体、工程 gap candidate、EvidenceSpan 和非工程 0 卡

用户真实麦克风会议
  -> 最终产品价值验证，由用户在前置门槛达标后执行
```

我负责前两层的计划、脚本、工具和本地自测。真实麦克风会议不由我自动执行，不读取用户真实录音，不读取 `data/asr_eval/local_samples/`。

原因：

- 直接进入真实会议风险太高，桌面壳、麦克风采集、本地 ASR worker 和建议卡片时机尚未全部跑通。
- 公开会议数据能验证 ASR 实时性、重叠说话、远场和 segment/event 管线，但不能证明软件工程会议建议价值。
- 合成技术会议脚本能验证产品差异化：owner、deadline、rollback、test、metric 等缺口能否被低频、带证据地识别。
- 真实麦克风会议必须作为最后一层验证，避免把 synthetic 或 fixture 成功包装成产品成功。

替代方案：

- 直接读取用户已提供的真实录音：拒绝。当前阶段不需要，也会增加隐私和敏感路径风险。
- 默认接阿里/讯飞等远程实时 ASR：拒绝作为默认路线。远程 ASR 只能作为显式质量对照，不新增默认收费项。
- 只用公开数据集验证：拒绝。公开会议数据通常不是软件工程会议，无法验证 Copilot 价值。
- 只用合成音频验证：拒绝。无法覆盖真实多人会议声学、重叠说话和远场问题。

影响范围：

- `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`
- `docs/superpowers/plans/2026-07-02-public-audio-and-synthetic-meeting-asr-validation.md`
- `docs/asr-evaluation-dataset.md`
- `data/asr_eval/public_sources.json`
- 后续 `tools/public_audio_sample_extraction_plan.py`
- 后续 `tools/synthetic_meeting_script_report.py`
- 后续 `tools/synthetic_audio_generation_plan.py`

成本/隐私影响：

- 默认不增加远程 ASR 收费。
- LLM 中转站仍是唯一默认远程模型成本，但 M4/M5 前置转写验证阶段不调用 LLM。
- 原始公开音频、合成音频、真实录音、runtime chunks、模型缓存都不得提交仓库。
- 公开来源必须有明确授权和来源 URL；需要登录、限制二次处理或授权不清的资源不能进入默认自动下载白名单。

验证方式：

- `tests/test_public_audio_source_whitelist.py` 确认公开来源白名单不下载、不读真实音频、不读 `configs/local`。
- 后续 `tests/test_public_audio_sample_extraction_plan.py` 确认 bounded extraction plan 只输出计划，不下载。
- 后续 `tests/test_synthetic_meeting_scripts.py` 确认 API/release/incident/architecture/non-engineering 场景覆盖。
- 后续 `tests/test_synthetic_audio_generation_plan.py` 确认本地合成音频计划不调用远程 TTS/ASR/LLM。
- 后续 ASR event gate 必须输出 duration、RTF、first partial latency、final latency P95、segment count、technical entity recall。

复审条件：

- 如果公开/合成音频阶段无法产出稳定 `partial/final/revision` 事件，不能进入真实麦克风会议。
- 如果合成技术会议无法稳定产生合理 gap candidate，产品路线必须回到 core/scheduler/gap rules，不应继续做安装包。
- 如果本地 ASR 无法达到关键技术实体 recall 80%，且没有通向 90% 的方案，再复审远程 ASR 可选高质量模式。
- 如果真实会议 useful / would-have-asked 低于 40%，或 wrong/too_late/too_intrusive 高于 20-25%，必须降级或停止实时 Copilot 路线。

## DEC-094：公开音频只采用授权清晰数据集，不抓取版权不清视频音频

日期：2026-07-03

状态：Accepted

背景：

用户要求转写自测阶段由我去网上寻找音频和模拟，真实麦克风会议由用户最终验证。为避免后续误把公开视频、播客或会议录播抓进评测集，需要把公开来源选择规则写入决策日志。

决策：

公开音频默认只采用授权清晰的数据集来源：

- AISHELL-4 / OpenSLR SLR111：中文真实多人会议主集。
- AliMeeting / OpenSLR SLR119：中文真实多人会议主集，适合 near-field/far-field 对照。
- AISHELL-1 / OpenSLR SLR33：普通话干净朗读 smoke，不用于会议价值证明。
- THCHS-30 / OpenSLR SLR18：可选普通话/噪声 smoke，不用于会议价值证明。

不抓取 Bilibili、YouTube、播客、电视节目、直播切片、公开课、技术大会回放或会议录播作为自动评测音频。第三方重打包但授权链不清的数据也不进入默认自动评测集。

原因：

- 公开视频通常只授权观看，不等于允许下载、切分、处理、再分发或作为产品评测输入。
- AISHELL-4 和 AliMeeting 虽然不是软件工程会议，但能合法覆盖真实多人会议声学、重叠说话、远场和切句风险。
- 产品语义价值由自建中文技术会议脚本和最终真实麦克风会议验证补足。

替代方案：

- 抓取技术大会公开视频：不采用，技术相关性高但授权风险高。
- 使用 SpeechIO/第三方视频音频重打包：不采用作为音频来源，可只借鉴场景分类和指标。
- 只使用合成音频：不采用，无法覆盖真实会议声学。

影响范围：

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-evaluation-dataset.md`
- `data/asr_eval/public_sources.json`
- `tools/public_audio_sample_extraction_plan.py`

成本/隐私影响：

- 不增加远程 ASR 费用。
- 不引入版权不清的下载缓存或派生音频。
- 原始公开音频只允许进入 ignored 目录，不提交仓库。

验证方式：

- 公开音频下载前必须先生成 bounded sample extraction plan。
- 所有来源必须有 source id、URL、license、用途边界和 not-for 边界。
- 敏感扫描不得出现真实用户音频路径、`configs/local` 或 API key。

复审条件：

如果后续找到明确授权的中文技术会议公开数据集，可作为新 source id 追加，但必须先完成 license、下载条款和派生输出边界审查。

## DEC-095：sherpa 作为性能基线，FunASR/normalizer 作为中文技术词质量主线

日期：2026-07-03

状态：Accepted

背景：

已完成一次 `api-review-001` 本地合成音频和 sherpa-onnx streaming ASR smoke。链路可跑且速度较快，但关键技术实体识别质量明显不足。

决策：

sherpa-onnx 暂定为性能基线和轻量备选，不作为中文技术会议默认质量候选。下一步质量主线转向：

```text
FunASR/Paraformer
  + hotword / glossary / normalizer
  + 技术实体纠错
  + EvidenceSpan-backed state/gap/card gate
```

原因：

- sherpa smoke：duration 约 16.83s，latency 516ms，RTF 0.030667，partial/final/end_of_stream 事件完整，证明性能和事件链路可跑。
- 同一 smoke 中 raw 技术实体 recall 为 0.0，normalized recall 仅 0.25，未恢复 `payment-gateway`、`request_id`、`40012`。
- 产品价值依赖技术实体和工程语义；速度快但实体丢失会让建议卡片失去证据基础。

替代方案：

- 直接把 sherpa 作为默认 ASR：不采用，质量风险过高。
- 直接切远程 ASR：不采用作为默认，增加成本和隐私复杂度。
- 只靠 LLM 修正 ASR 文本：不采用作为唯一方案，ASR 丢失实体时 LLM 容易猜测或幻觉。

影响范围：

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`
- `docs/project-stage-status-and-next-work-2026-07-02.md`
- `docs/requirements-traceability-matrix.md`
- `tools/synthetic_asr_smoke_report.py`

成本/隐私影响：

- 继续优先本地 ASR，不新增默认付费 ASR。
- FunASR 若需要模型下载，不能静默下载；应先记录模型缓存状态和体积边界。

验证方式：

- 使用相同 synthetic wav 跑 FunASR 本地 smoke。
- 输出同一 `synthetic_asr_smoke_report` 指标。
- 技术实体 normalized recall 必须接近或超过 0.8，才可进入 first real-mic pilot。

复审条件：

如果 FunASR + normalizer 仍长期无法保留关键技术实体，再复审可选远程 ASR、高质量模式或产品降级为会后结构化纪要。

## DEC-096：FunASR synthetic smoke 必须先过 runtime cache preflight，legacy cache 不足以执行

日期：2026-07-03

状态：Accepted

背景：

按主计划尝试用 `api-review-001.wav` 继续 FunASR synthetic smoke。执行前发现本机存在 ModelScope legacy/hub cache，因此最初 readiness 误判为 ready；显式执行时 FunASR/ModelScope 实际使用 runtime cache layout，并在 runtime cache 缺失时开始下载约 840MB online streaming 模型。

决策：

新增 `tools/funasr_synthetic_smoke_readiness.py` 和 `tests/test_funasr_synthetic_smoke_readiness.py`。后续 FunASR synthetic smoke 必须先通过 runtime cache preflight，但 preflight 通过仍不等于离线执行已被证明：

- synthetic audio 输入只能来自 `artifacts/tmp/synthetic_audio`。
- events 输出只能进入 `artifacts/tmp/asr_events`。
- provider/transcript/smoke reports 只能进入 `artifacts/tmp/asr_reports`。
- FunASR venv 必须存在。
- ModelScope runtime cache 必须包含本次 streaming 命令实际会加载的 online paraformer 必需文件；VAD cache 不作为该命令的 preflight 条件。
- legacy/hub cache 只能作为提示，不足以解锁执行。
- preflight 通过时报告状态为 `cache_preflight_passed_offline_execution_not_proven`，且 `safe_to_execute_local_funasr_now=false`。
- invalid/blocked report 不能回显调用方传入的绝对路径。
- 报告不得输出本机绝对 cache 路径。
- `safe_to_download_models=false`，除非后续用户明确批准模型下载或预置 runtime cache。

原因：

- 避免隐藏模型下载、磁盘膨胀和不可追溯依赖变化。
- 避免把“曾经有旧缓存/旧输出”误当成当前环境可重复执行证据。
- 保持默认不增加远程 ASR/LLM 调用，也不读取真实用户音频或 `configs/local`。

替代方案：

- 继续让 FunASR 自动下载模型：不采用，违反“尽量不要增加额外下载/成本/膨胀”的边界。
- 使用 legacy/hub cache 判断 ready：不采用，已实测不足以阻止下载。
- 直接切换远程 ASR：不采用作为默认，增加费用和隐私复杂度。

影响范围：

- `tools/funasr_synthetic_smoke_readiness.py`
- `tests/test_funasr_synthetic_smoke_readiness.py`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/project-stage-status-and-next-work-2026-07-02.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

成本/隐私影响：

- 本轮误触发的 runtime cache 已中断并清理。
- 后续默认不下载 FunASR 模型，不调用远程 ASR/LLM。
- 真实用户音频、`configs/local`、API key 和本机绝对 cache 路径不得进入报告。

验证方式：

- TDD 已覆盖 cache preflight passed、missing cache、only legacy cache、forbidden paths、invalid absolute path redaction 五类 readiness 行为。
- 真实 readiness report 当前为 `blocked`，原因是 runtime cache 缺失。

复审条件：

如果用户明确批准模型下载、或者我能在不下载的前提下预置 runtime cache，再重新运行 readiness；只有 cache preflight 通过并补上离线执行 guard 后，才能执行 FunASR synthetic smoke。

## DEC-097：公开音频已完成官方来源复核，但下一轮默认只做具体抽样计划和合成音频模拟

日期：2026-07-03

状态：Accepted

背景：

用户确认：转写验证阶段应由我先去网上寻找合法公开音频并进行模拟；最终真实麦克风会议由用户验证。同时，项目不能继续陷入无限评测，不能把产品做成普通音频转文字工具，也不能增加不必要的额外收费或隐私风险。

2026-07-03 已二次联网复核官方来源：

- AISHELL-4 / OpenSLR SLR111：CC BY-SA 4.0，真实普通话多人会议，`test.tar.gz` 约 5.2G。
- AliMeeting / OpenSLR SLR119：CC BY-SA 4.0，真实多人会议，`Eval_Ali.tar.gz` 约 3.42G。
- AISHELL-1 / OpenSLR SLR33：Apache License v2.0，普通话朗读，`data_aishell.tgz` 约 15G。
- THCHS-30 / OpenSLR SLR18：Apache License v2.0，低优先级中文朗读/噪声补充。

决策：

下一轮执行按以下顺序推进：

```text
FunASR offline guard
  -> synthetic audio smoke batch
  -> public audio bounded sample plan
  -> desktop runtime validation
  -> user real mic meeting validation
```

其中：

- 公开音频来源已经找到，但官方包体量都是 GB 级；本轮默认不下载。
- 公开音频下一步只生成具体 sample extraction plan，必须明确 archive、archive member path、clip start/end、duration cap、byte cap、checksum、target root 和 cleanup policy。
- 合成音频模拟继续使用 committed synthetic meeting scripts 和本机离线 TTS。
- FunASR synthetic smoke 必须先解决 offline guard 或获得明确模型下载批准。
- 用户真实麦克风会议仍是最终产品价值验证，不能被公开音频或合成音频替代。

原因：

- 合法公开会议音频能覆盖真实声学、多人、远场、重叠和切句风险，但不能证明工程建议卡片的产品价值。
- 自建中文技术会议脚本能覆盖技术实体、owner/deadline/rollback/test/metric 等产品语义，但不能替代真实会议声学。
- GB 级公开数据包和 FunASR 模型下载都会带来磁盘膨胀和不可追溯依赖变化，不能默认执行。
- 继续增加 readiness/report-only 文档不能推进真实链路，下一轮必须围绕 ASR 质量、EvidenceSpan、桌面 runtime 和真实麦克风进入条件。

影响范围：

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/asr-zero-cost-and-private-audio-boundary.md`
- `data/asr_eval/public_sample_plan.example.json`
- `docs/requirements-traceability-matrix.md`

成本/隐私影响：

- 默认不下载公开大包。
- 默认不下载 FunASR/ModelScope 模型。
- 默认不调用远程 ASR/LLM/TTS。
- 默认不读取真实用户音频、麦克风、`configs/local/`、`data/asr_eval/local_samples/` 或 `data/local_runtime/`。

验证方式：

- 文档中记录官方来源 URL、授权、包体量和用途边界。
- `data/asr_eval/public_sample_plan.example.json` 增加未来抽样必须填的 schema 字段，但保持 `safe_to_download_now=false`。
- 后续通过 TDD 补 FunASR offline guard 和横向 no-private-audio/no-cost boundary 测试。

复审条件：

如果后续找到小体量、授权清楚、可复现的公开中文会议音频样本，可追加为新的 source id；否则公开音频保持 bounded plan，不再扩大搜索范围。

## DEC-098：FunASR streaming 执行器必须使用显式本地模型目录，缺失即 blocked

日期：2026-07-03

状态：Accepted

背景：

此前 FunASR streaming 文件回放代码在 `stream_events()` 中直接调用 `AutoModel(model="paraformer-zh-streaming", ...)`。实际执行时，ModelScope 在 runtime cache 缺失时会尝试下载约 840MB online streaming 模型。仅靠 readiness preflight 仍不足以防止未来有人绕过 readiness 直接运行执行脚本。

决策：

`code/asr_runtime/scripts/transcribe_funasr.py` 的 streaming 模式必须进入 offline guard：

- CLI `--streaming` 必须显式传入 `--local-model-dir`。
- `local_model_dir` 必须是绝对路径，且包含 `model.pt` 和 `config.yaml`。
- `local_model_dir` 不得位于项目禁区，例如 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`。
- 缺少 `local_model_dir`、目录不存在或必要文件缺失时，`transcribe_streaming()` 返回 `status=blocked`，并保持 `safe_to_download_models=false`。
- blocked 路径不得 import/construct FunASR `AutoModel`。
- 正常执行时传给 FunASR 的 `model` 参数是本地模型目录路径，不再是 alias。
- provider JSON/raw output 不回显本机模型目录绝对路径，只记录 `model_resolution=local_model_dir` 和 `model_download_status`。
- `tools/funasr_synthetic_smoke_readiness.py` 的 command preview 同步加入 `--local-model-dir <modelscope_runtime_models_iic/...>` 占位符，避免误导为可裸跑 alias。

原因：

- 把“不会自动下载模型”从文档/计划边界下沉到实际执行器。
- 避免直接运行脚本时绕过 readiness gate。
- 保持零远程费用、无模型自动下载、无私人路径泄漏的边界。

替代方案：

- 继续允许 `AutoModel(model=<alias>)`：不采用，已证明可能触发下载。
- 只依赖 readiness report：不采用，执行脚本仍可能被直接调用。
- 自动复制 legacy/hub cache 到 runtime cache：不采用，会增加磁盘膨胀且不一定可追溯。

影响范围：

- `code/asr_runtime/scripts/transcribe_funasr.py`
- `code/asr_runtime/tests/test_transcribe_funasr.py`
- `tools/funasr_synthetic_smoke_readiness.py`
- `tests/test_funasr_synthetic_smoke_readiness.py`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-zero-cost-and-private-audio-boundary.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

成本/隐私影响：

- 不下载 FunASR/ModelScope 模型。
- 不调用远程 ASR/LLM。
- 不读取真实用户音频或 `configs/local/`。
- 不在报告中输出本机模型目录绝对路径。

验证方式：

- `code/asr_runtime/tests/test_transcribe_funasr.py` 覆盖：
  - streaming 缺 `local_model_dir` 时 blocked 且不构造 `AutoModel`。
  - local model dir 不完整时拒绝执行。
  - local model dir 位于 forbidden project roots 时拒绝执行。
  - local model dir 完整时 `AutoModel(model=<local path>)`，不使用 alias。
  - CLI 输出不泄漏本机音频路径。
- `tests/test_funasr_synthetic_smoke_readiness.py` 覆盖 command preview 包含 `--local-model-dir` 占位符，且报告仍保持 `safe_to_download_models=false`。

复审条件：

如果未来 FunASR/ModelScope 官方提供明确的 offline-only alias resolution API，可复审是否允许 alias，但必须先证明不会联网下载，并继续保持报告不泄漏本机路径。

## DEC-099：5 脚本 synthetic batch smoke 确认 sherpa 只作为性能基线，不继续作为质量路线打磨

日期：2026-07-03

状态：Accepted

背景：

按下一轮计划完成 5 个中文合成会议脚本的本机 TTS batch 和 sherpa-onnx 本地 ASR baseline。目标是验证文件回放式 ASR 事件链路、速度、技术实体保留和非工程 control，而不是证明真实麦克风会议价值。

决策：

sherpa-onnx 继续保留为本地性能基线和轻量 fallback，不再作为中文技术会议质量主线继续打磨。后续质量工作转向：

```text
FunASR local model/cache approval or offline execution
  + hotword / normalizer / technical entity correction
  + EvidenceSpan-backed state/gap/card gate
```

原因：

- 5 个 synthetic wav 已全部生成，均为 16kHz mono PCM。
- sherpa batch RTF 约 0.029-0.035，说明速度稳定。
- 5 个样本都有 final 和 end_of_stream，事件链路完整。
- normalizer 增量前 4 个工程脚本 normalized technical entity recall 只有 0-0.25；增量后为 0-0.5，仍未达 first-pilot 最低门槛 0.8。
- `payment-gateway`、`request_id`、`40012`、`feature-store`、`recommendation-service`、`redis cluster`、`order-worker`、`error_rate`、`staging` 等核心实体大量缺失。
- 非工程 control recall 为 1.0 是因为无技术实体，不能证明 ASR 质量。

替代方案：

- 继续在 sherpa 上扩大样本调参：不作为主线采用，容易陷入 provider 横评循环。
- 直接进入真实麦克风会议：不采用，技术实体质量门槛未达。
- 只靠 LLM 修正：不采用作为唯一方案，ASR 未保留实体线索时 LLM 容易猜测。

影响范围：

- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `tools/synthetic_audio_batch_smoke.py`
- `tests/test_synthetic_audio_batch_smoke.py`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

成本/隐私影响：

- 本轮只使用 macOS 本机 `say`、`afconvert` 和本地 sherpa 模型。
- 未读取真实用户音频，未读取 `configs/local/`，未调用远程 TTS/ASR/LLM，未下载公开音频或 FunASR 模型。
- 生成音频、events 和 reports 均在 ignored `artifacts/tmp/`。

验证方式：

- `tools/synthetic_audio_batch_smoke.py --execute-local-tts` 输出 `batch_status=generated`。
- `file artifacts/tmp/synthetic_audio/*.wav` 确认 16kHz mono PCM。
- sherpa provider/events/transcript/smoke reports 已生成到 `artifacts/tmp/`。
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md` 记录 5 个脚本的 RTF、latency、event counts 和 entity recall。

复审条件：

如果未来更换 sherpa 模型、加入热词能力或找到明确可行的技术实体保留策略，可以单独复审；但复审必须以 first-pilot entity recall >= 0.8 为硬门槛，而不是只看速度。

## DEC-100：技术实体 normalizer 只恢复已有线索，不从 `<unk>` 猜测实体

日期：2026-07-03

状态：Accepted

背景：

5 脚本 synthetic batch smoke 显示 sherpa 速度达标，但技术实体缺失严重。其中一类实体不是完全丢失，而是以可确定形式出现在文本中，例如 `40012` 被识别成“四万零一十二”，`P99` 被识别成“九九”，未来 provider 也可能输出 `request id`、`error rate`、`payment gateway` 这类别名。

决策：

技术实体 normalizer 可以做确定性恢复，但不得把 `<unk>` 或缺失文本猜成服务名：

- 新增 spoken error code 规则：在 `错误码`、`状态码`、`error code` 或 `code` 附近，将中文数字短语恢复为阿拉伯数字，例如“四万零一十二” -> `40012`。
- committed technical glossary 增加常见中英混合别名，例如 `request id` -> `request_id`、`error rate` -> `error_rate`、`payment gateway` -> `payment-gateway`。
- alias 替换按全局 alias 长度降序执行，并对英文/数字 alias 使用 token boundary，避免 `payment gate` 把 `payment gateway` 误替换为 `payment-gatewayway`。
- 不把 `<unk><unk>` 推断为 `payment-gateway`、`checkout-service` 或其他服务名。

原因：

- 只恢复 ASR 文本中已经存在的数字和别名线索，能提升 EvidenceSpan 可信度。
- 不从 `<unk>` 猜测，避免 LLM 或规则层制造幻觉证据。
- 对真实会议也有价值，因为程序员会议常会把字段名、指标名、服务名说成空格分隔英文。

影响范围：

- `code/asr_runtime/scripts/transcript_normalizer.py`
- `code/asr_runtime/tests/test_transcript_normalizer.py`
- `data/asr_eval/glossaries/technical-terms.zh.json`
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

效果：

- `api-review-001` normalized technical entity recall 从 0.25 提升到 0.5，新增恢复 `40012`。
- 其他 `<unk>` 或没有线索的实体仍保持缺失，不被猜测。
- 仍未达到 first-pilot 最低门槛 0.8，因此不能进入真实麦克风会议验证。

验证方式：

- `code/asr_runtime/tests/test_transcript_normalizer.py` 覆盖 spoken error code、committed glossary alias 和短 alias 边界。
- 重新生成 5 个 synthetic sherpa transcript/smoke reports，确认 `api-review-001` 只新增恢复 `40012`，未猜测 `payment-gateway` 或 `request_id`。

复审条件：

如果后续引入 LLM second-pass 修正，必须继续要求每个恢复实体能追溯到 ASR 文本中的别名、拼音、数字或上下文线索；没有线索的实体只能标为候选，不得写入正式 EvidenceSpan。

## DEC-101：公开音频 planned samples 只做可机检计划校验，AliMeeting 优先但仍不自动下载

日期：2026-07-03

状态：Accepted

背景：

用户要求转写自测阶段由我寻找公开音频并进行模拟，最终真实麦克风会议由用户验证。同时，前序计划审查发现 README、主计划和 next-run 计划之间存在轻微文档漂移：README 的执行顺序表述容易被理解成“先公开音频再合成音频”，next-run 计划也需要更明确地保留 whitelist gate、byte cap、cleanup 和真实麦克风隐私边界。

公开来源复核结论：

- AliMeeting / OpenSLR SLR119：CC BY-SA 4.0，真实多人会议，含 near-field / far-field 对照；`Eval_Ali.tar.gz` 约 3.42G，适合作为会议实时转写模拟主候选，但不适合自动下载。
- AISHELL-4 / OpenSLR SLR111：CC BY-SA 4.0，真实普通话多人会议、远场、多通道和重叠说话；`test.tar.gz` 约 5.2G，适合作为复杂会议声学补充，但不适合自动下载。
- AISHELL-1 / OpenSLR SLR33：Apache License v2.0，普通话朗读；只适合 ASR/runtime sanity check，不证明会议实时转写或产品价值。

决策：

- 公开音频验证优先级调整为 AliMeeting Eval 第一，AISHELL-4 test 第二，AISHELL-1 只做普通话 smoke。
- `tools/public_audio_sample_extraction_plan.py` 支持 `planned_samples` schema 校验，并支持 CLI `--planned-samples-file` 读取 JSON 样本计划。
- planned sample 必须包含 `sample_id`、`archive_name`、`archive_member_path`、`clip_start_seconds`、`clip_end_seconds`、`expected_duration_seconds`、`expected_sha256_after_extract`、`license_citation` 和 `cleanup_required`。2026-07-03 的 DEC-109 已扩展为还必须包含并绑定 selected `source_id/source_url/source_license`。
- 绝对路径、`..` 路径、超出 `max_clip_seconds`、超出 `sample_budget_count`、超出 `sample_budget_minutes`、非 64 位小写 sha256、缺失 license citation 或 `cleanup_required=false` 都会 blocked。
- planned samples 校验通过时只返回 `planned_samples_status=schema_validated_no_download`，仍保持 `safe_to_download_now=false`、`safe_to_extract_now=false`，且 `download_command`、`extract_command`、`transcode_command` 为 `null`。
- README 的“执行顺序”改为“验证结构”，具体执行顺序以 next-run 计划为准：FunASR guard / synthetic batch / public sample plan / desktop runtime / user real mic validation。

原因：

- AliMeeting 的 near-field / far-field 对照更适合先验证会议实时转写事件链和延迟。
- AISHELL-4 更适合补充多人、远场、重叠说话和复杂会议声学风险。
- 两者官方包都是 GB 级，不能默认下载；如果需要真实下载，必须另起人工审批步骤。
- 公开音频只能证明真实声学和切句风险，不能证明工程建议卡片价值；工程语义仍靠合成中文技术会议和最终真实麦克风会议验证。

替代方案：

- 继续泛搜公开网页、视频或播客音频：拒绝，授权风险高且不可追溯。
- 直接下载 OpenSLR Eval/Test 大包：拒绝，体量大且不符合本轮 no-download 边界。
- 只保留文档说明、不做 schema 校验：拒绝，后续容易漏填 license、checksum、cleanup 或误生成执行命令。

影响范围：

- `tools/public_audio_sample_extraction_plan.py`
- `tests/test_public_audio_sample_extraction_plan.py`
- `data/asr_eval/public_sample_plan.example.json`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

成本/隐私影响：

- 不下载公开音频大包。
- 不解压、不转码、不运行 ASR。
- 不读取真实用户音频、`data/asr_eval/local_samples/`、`data/local_runtime/` 或 `configs/local/`。
- 不调用远程 ASR/LLM/TTS。

验证方式：

- `tests/test_public_audio_sample_extraction_plan.py` 覆盖默认 no-download 计划、planned samples schema、unsafe paths、预算超限、checksum、cleanup、license citation 和 CLI `--planned-samples-file`。
- 文档同步记录 AliMeeting 优先、whitelist gate、byte cap、cleanup、真实麦克风隐私边界和 no-download 状态。

复审条件：

如果后续发现小体量、授权清楚、可直接按文件级下载的中文会议音频样本，可以追加 source id；如果只能下载 GB 级包，则仍需人工审批并保留 no-auto-download 默认。

## DEC-102：合成 ASR smoke 必须经过 product value gate，不能只看转写速度和事件完整性

日期：2026-07-03

状态：Accepted

背景：

5 脚本 synthetic batch smoke 已证明 sherpa 本地速度稳定、final / end_of_stream 事件链路完整，但 4 个工程脚本的 normalized technical entity recall 只有 0-0.5。用户反复强调产品不能退化成音频转文字工具，必须验证实时建议卡片和工程缺口价值。因此，仅有 ASR smoke report 不足以决定是否进入桌面 runtime 或真实麦克风 pilot。

决策：

新增 `tools/synthetic_product_value_gate.py` 和 `tests/test_synthetic_product_value_gate.py`。该 gate 把以下内容合并为产品阶段决策：

- synthetic ASR smoke report 的 event counts、normalized technical entity precision/recall、missing entities。
- synthetic script 的 expected gap candidates、expected suggestion cards、expected engineering card count range。
- baseline expectations：transcript-only、summary-only 和 Copilot within-window 期望。
- 非工程 control 是否保持 0 工程卡。

输出决策：

- `needs_asr_quality_work`：工程脚本事件链路可用，但实体召回不足，不能进入真实麦克风 pilot。
- `go_to_desktop_runtime_validation`：工程脚本事件链路和 first-pilot entity threshold 达标，可推进 desktop runtime，但仍不能直接跳到真实麦克风 pilot。
- `negative_control_passed`：非工程 control 的脚本期望保持 0 工程卡，且不推进 desktop / real mic，作为负控保留；实际 downstream card count 会在后续 state/gap/card artifact 接入后纳入 gate。
- `blocked_by_event_contract`：缺 final / end_of_stream 等事件契约问题，需要先修 ASR 事件层。

当前 5 脚本结果：

| 脚本 | 决策 |
| --- | --- |
| `api-review-001` | `needs_asr_quality_work` |
| `architecture-review-001` | `needs_asr_quality_work` |
| `incident-review-001` | `needs_asr_quality_work` |
| `release-review-001` | `needs_asr_quality_work` |
| `non-engineering-control-001` | `negative_control_passed` |

原因：

- 产品价值取决于 EvidenceSpan 和建议卡是否能追溯到保留下来的工程实体，而不是只看 ASR 是否有文字输出。
- 当前事件链路完整，但工程实体不足，LLM 在缺线索时容易猜测。
- 非工程 control 必须继续约束“不是工程会议就不要产生工程卡”。

替代方案：

- 只看 RTF、final、EOS：拒绝，会把速度误当成产品价值。
- 只看 normalized entity recall：拒绝，还需要确保脚本本身有 expected cards/gaps 和非工程负控。
- 直接进真实麦克风会议：拒绝，工程实体 recall 未达 first-pilot 0.8。

影响范围：

- `tools/synthetic_product_value_gate.py`
- `tests/test_synthetic_product_value_gate.py`
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

成本/隐私影响：

- 不读取真实用户音频。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 只读取 allowed synthetic smoke report 和 committed synthetic script JSON。

验证方式：

- `tests/test_synthetic_product_value_gate.py` 覆盖工程脚本低实体召回 blocked、高实体召回进入 desktop runtime、非工程 control 负控、forbidden paths。
- `tests/test_synthetic_product_value_gate.py` 也覆盖 provider error 必须归入 event contract blocker、allowed root symlink 指向仓库外目标必须 blocked、直接 builder 传入未批准绝对路径必须 blocked。
- 使用当前 5 个 `artifacts/tmp/asr_reports/*.sherpa.smoke-report.json` 运行 gate，确认 4 个工程脚本都 blocked 为 `needs_asr_quality_work`，非工程 control 为 `negative_control_passed`。

复审条件：

如果 FunASR、本地 hotword、normalizer 或其他本地 ASR 路线让至少一个工程脚本 normalized entity recall 达到 0.8，再复审是否允许进入 desktop runtime 的真实 ASR worker 集成；真实麦克风 pilot 仍需桌面 runtime 和麦克风 adapter 通过。

## DEC-103：FunASR 模型缺失时先生成手动审批包，不静默下载 ModelScope 模型

日期：2026-07-03

状态：Accepted

背景：

当前 synthetic product value gate 已确认 sherpa 事件链路和速度可用，但中文技术实体质量不足。FunASR/Paraformer 仍是中文质量主候选，不过本地 runtime model dir/cache 缺失；此前显式尝试发现 ModelScope 可能下载约 840MB online streaming 模型。用户要求尽量不要增加额外收费和资源膨胀，同时所有关键决策必须落档，避免后续开发偏离方向。

决策：

新增 `code/asr_runtime/funasr-model-download-approval.policy.json`、`tools/funasr_model_download_approval_packet.py` 和 `tests/test_funasr_model_download_approval_packet.py`，把 FunASR 模型获取固定为 DRV-019 手动审批包：

- model provider：ModelScope。
- namespace：`iic`。
- model id：`speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`。
- expected size note：`about_840mb_observed_online_streaming_model`。
- 必需文件：`model.pt`、`config.yaml`。
- 手动命令文本只作为 inert text 渲染，执行边界为 `manual_user_run_only`。
- post-download 验证顺序：本地模型目录存在、必需文件存在、FunASR readiness gate、`transcribe_funasr.py` offline guard、synthetic product value gate。
- 默认保持 `safe_to_execute_download_now=false`、`safe_to_download_models_now=false`、`safe_to_run_modelscope_now=false`、`safe_to_run_python_download_now=false` 和 `safe_to_run_funasr_smoke_now=false`。

原因：

- 当前主线需要继续验证中文 ASR 质量，但不能通过静默模型下载绕过成本、磁盘和可追溯边界。
- 模型下载不是产品价值本身，只是解除 FunASR 质量验证 blocker 的前置资源动作。
- 把下载说明、approval tokens、清理策略和验证顺序做成 machine-checkable packet，能避免后续把 “Need model approval” 误当成 “可以直接跑模型”。

替代方案：

- 直接运行 `AutoModel(model=<alias>)` 触发 ModelScope 自动下载：拒绝，违反 no-auto-download 边界。
- 改接远程 ASR 作为默认路线：暂不采用，会新增默认费用和隐私/延迟风险；仅可作为后续显式高质量对照。
- 放弃 FunASR 继续打磨 sherpa：暂不采用，sherpa 已确认适合性能基线但技术实体召回不足。

影响范围：

- `code/asr_runtime/funasr-model-download-approval.policy.json`
- `tools/funasr_model_download_approval_packet.py`
- `tests/test_funasr_model_download_approval_packet.py`
- `docs/requirements-traceability-matrix.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `README.md`

成本/隐私影响：

- 不下载 FunASR/ModelScope 模型。
- 不执行 `modelscope` 或 Python 下载命令。
- 不读取真实用户音频、`data/asr_eval/local_samples/`、`data/local_runtime/` 或 `configs/local/`。
- 不调用远程 ASR/LLM。
- 不运行 FunASR synthetic smoke。

验证方式：

- `tests/test_funasr_model_download_approval_packet.py` 覆盖 policy 必填字段、官方来源、手动 inert command、风险/清理说明、所有安全 flag 为 false、custom policy 不能放宽执行状态、forbidden path 和 symlink 阻断、报告不泄漏本机路径/密钥形态、工具源码不包含命令执行入口。

复审条件：

只有用户明确批准模型下载，或用户提供已经就绪且可清理的本地模型目录，才允许进入 post-download verification order；即使模型目录就绪，真实麦克风 pilot 仍需 synthetic product value gate 和 desktop runtime/mic adapter 通过。

## DEC-104：ASR events 必须 replay 到 Live ASR pipeline，且非工程会议不得产生工程候选

日期：2026-07-03

状态：Accepted

背景：

synthetic batch smoke 已证明 sherpa 能输出本地 ASR events，但用户反复强调产品不能停留在“音频转文字”。仅有 ASR smoke report 和 product value gate 仍缺少一个中间证据：ASR event JSON 是否真的能接入 Web Live ASR pipeline，并产生 EvidenceSpan、state/scheduler、suggestion candidate 和 LLM request draft。同时，复测真实 `non-engineering-control-001.sherpa.events.json` 时发现本地规则会把普通会议句子误判为工程候选：`是否方便` 触发 OpenQuestion，`名单整理明天发到群` 触发 ActionItem。

决策：

新增 `tools/asr_live_pipeline_replay.py` 和 `tests/test_asr_live_pipeline_replay.py`，并修正 `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`：

- replay gate 只读取 allowed `artifacts/tmp/asr_events`，禁止 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime`、`outputs` 和 symlink 绕过。
- replay gate 复用 Web Live ASR builder，报告 input event counts、live event counts、EvidenceSpan 数量、state/scheduler/candidate/request draft 数量、正式卡片创建状态和安全 flag。
- replay gate 不跑模型、不读真实音频、不访问麦克风、不调用远程 ASR/LLM、不写 runtime audio。
- OpenQuestion 和 ActionItem 本地抽取必须具备工程上下文，例如 API、接口、服务、错误率、P99、延迟、灰度、回滚、发布、告警、故障、测试、兼容、压测、缓存、数据库、监控、脚本、指标等。

当前真实 artifacts 复测结果：

| 脚本 | EvidenceSpan | state | scheduler | candidate | request draft | formal card | LLM |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `api-review-001` | 1 | 1 | 1 | 1 | 1 | 0 | not_called |
| `release-review-001` | 1 | 1 | 1 | 1 | 1 | 0 | not_called |
| `non-engineering-control-001` | 1 | 0 | 0 | 0 | 0 | 0 | not_called |

原因：

- ASR 质量和实时 Copilot 价值之间必须有可运行的中间 gate，证明 final/revision 不是只落在 transcript，而是真的进入 EvidenceSpan 和建议候选前置链路。
- 非工程会议负控不能只停留在脚本期望层；下游 deterministic state/candidate 也必须约束，否则产品会在普通会议里刷无意义提醒。
- 当前仍保持 LLM disabled/no-cost，只验证本地确定性前置链路。

替代方案：

- 只看 synthetic product value gate：不够，无法发现下游 deterministic 抽取误触发。
- 直接启用 LLM 让模型判断是否工程会议：暂不采用，会引入费用、延迟和幻觉风险；本地规则应先过滤明显非工程场景。
- 完全删除 OpenQuestion/ActionItem：不采用，它们是技术会议实时建议的重要信号，只需加工程上下文边界。

影响范围：

- `tools/asr_live_pipeline_replay.py`
- `tests/test_asr_live_pipeline_replay.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/tests/test_live_events.py`
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

验证方式：

- `tests/test_asr_live_pipeline_replay.py` 覆盖 successful replay、无 final/revision blocked、unknown event contract blocker、forbidden paths、symlink to forbidden root 和源码无远程/进程执行入口。
- `code/web_mvp/backend/tests/test_live_events.py::test_build_asr_live_events_suppresses_non_engineering_open_question_candidate` 覆盖 `是否方便` 与 `名单整理明天发群` 不再生成 state/candidate。
- 使用真实 `api-review-001`、`release-review-001`、`non-engineering-control-001` sherpa events 运行 replay gate，确认工程样本仍产生 candidate，非工程样本为 0 candidate。

复审条件：

当 FunASR 或其他高质量 ASR provider 接入后，必须用同一个 replay gate 复测工程正控和非工程负控；如果高质量 ASR 使普通会议误触发率上升，需要继续收紧 engineering-context gate 或增加 meeting-domain classifier。

## DEC-105：新增本地 ASR event file handoff API，作为桌面 ASR worker 到 Web Live ASR 的受控前置入口

日期：2026-07-03

状态：Accepted

背景：

DRV-020 已证明本地 ASR event JSON 可以 replay 到 Web Live ASR pipeline，但它仍是工具脚本，不是应用运行时入口。后续桌面端 ASR worker 的最小交接面不应该直接访问麦克风、模型、`configs/local` 或任意文件，而应先通过一个受控本地 event file handoff API，把已经生成的 `partial/final/revision/error/end_of_stream` 事件接入现有 Live ASR JSON/SSE 端点。

决策：

新增 `POST /live/asr/local-event-files/sessions`：

- 请求字段：`session_id`、`provider`、`events_path`。
- 只允许读取 `artifacts/tmp/asr_events` 下的 JSON event file。
- 阻止 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 和 symlink 指向 forbidden root。
- 读取后复用 `build_asr_live_events(..., is_mock=False)` 生成 Live ASR events。
- 创建后可通过既有 `/live/asr/sessions/{session_id}/events` 和 `/events.sse` 消费。
- 响应包含 `ingest_mode=local_asr_event_file`、input/live event counts、LLM statuses、formal card creation status 和安全 flags。
- 默认保持 `safe_to_call_llm_now=false`、`safe_to_call_remote_asr_now=false`、`safe_to_read_user_audio_now=false`、`safe_to_read_configs_local_now=false`、`safe_to_capture_microphone_now=false`。

原因：

- 这一步把 ASR worker 输出和 Web Live ASR runtime 真实连接起来，比单独 replay 工具更接近桌面产品主线。
- 当前仍不需要模型下载、麦克风权限、真实音频或远程 provider，能继续在零额外费用和隐私安全边界内推进。
- 明确文件读取白名单和 symlink guard，避免 future desktop worker handoff 变成任意本地文件读取接口。

替代方案：

- 让桌面端直接把 streaming events 作为 JSON body POST 到 `/live/asr/mock/sessions`：暂不采用，会混淆 mock 与 worker handoff 语义。
- 直接启动真实 ASR worker 或麦克风 adapter：暂不采用，FunASR 模型和桌面 runtime 仍未就绪。
- 让 Web backend 读取任意本地路径：拒绝，违反隐私和可追溯边界。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `README.md`

验证方式：

- `test_create_asr_live_session_from_local_event_file_uses_worker_handoff_boundary` 覆盖 happy path：从 allowed events file 创建 session，并通过现有 JSON/SSE 读取。
- `test_create_asr_live_session_from_local_event_file_rejects_forbidden_paths_before_reading` 覆盖 `configs/local` 不可读。
- `test_create_asr_live_session_from_local_event_file_rejects_symlink_to_forbidden_root` 覆盖 allowed path symlink 指向 forbidden root 被拒绝。

复审条件：

当 Tauri no-op shell 和 ASR worker health contract 进入真实运行阶段后，应把该 API 作为 worker output handoff 的第一个集成点；真正麦克风 chunk、worker lifecycle、进程启动和本地 runtime 存储仍需单独 gate。

## DEC-106：计划锁定为公开授权音频模拟 + 合成技术会议 + 用户最终真实麦克风验证

日期：2026-07-03

状态：Accepted

背景：

用户明确提醒：完整计划必须写下来；转写相关验证需要由我们先去网上寻找可用音频并模拟，最终真实麦克风会议再由用户验证。此前已有多份计划和评测文档，但需要一个单页 plan lock 防止后续开发重新滑向“继续评测”“普通转写工具”或“未经审查下载公开视频/大包”。

决策：

新增 `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`，把当前主线锁定为：

- AliMeeting / OpenSLR SLR119 和 AISHELL-4 / OpenSLR SLR111 是公开授权中文会议音频主候选。
- AISHELL-1 只做普通话 ASR/runtime sanity，不证明会议产品价值。
- THCHS-30 只作为观察过的低优先级来源，不进入当前自动执行白名单。
- 公开音频只验证声学、多人、远场、重叠、切句和 ASR event contract；不证明工程建议卡片的最终价值。
- 合成中文技术会议继续验证技术实体、工程缺口、非工程 0 卡和实时触发窗口。
- 用户最终通过真实麦克风会议验证产品价值。
- 默认不下载公开大包、不访问麦克风、不读取真实用户音频、不调用远程 ASR/LLM、不自动下载模型。

原因：

- 官方可复用会议语料存在，但 AliMeeting Eval 和 AISHELL-4 test 都是 GB 级，不能变成默认下载动作。
- 公开会议语料不是软件工程会议，只能覆盖 ASR/声学风险；产品价值仍必须通过自建技术会议脚本和用户真实会议验证。
- 当前最有价值的下一步不是继续扩大评测，而是把已有 ASR event 链路推进到 desktop/runtime/worker handoff。

影响范围：

- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

验证方式：

- 文档列明官方来源、授权、当前使用边界和执行白名单。
- 文档列明 no-download/no-mic/no-private-audio/no-remote-ASR/LLM/no-model-autodownload 边界。
- RTM 新增 `DRV-022`，把该计划锁定为后续开发可追踪需求。

复审条件：

当用户明确批准一次公开语料下载或提供已就绪的小样本文件时，必须新增对应 sample manifest、checksum、存储路径和清理策略；否则公开音频阶段仍保持 no-download plan，不阻塞 desktop ASR worker handoff 主线。

下一步开发顺序：

1. 先加固 Web local ASR event file handoff API，补重复 session、坏 JSON、未知事件、非法时间戳/置信度、repo 外绝对路径和持久化读回等测试。
2. 再定义 desktop-side ASR worker handoff preflight，只做 descriptor/schema/chunk lifecycle/forbidden root，不启动 worker、不读麦克风。
3. 最后受控推进 desktop no-op runtime，只有 Rust/Cargo/Tauri 条件被明确确认时才运行，并把 target 写到 `artifacts/tmp/desktop_tauri_target`。

不再把新增宽泛 ASR readiness、公开音频来源搜索或 provider 横评作为下一轮主线。

## DEC-107：加固本地 ASR event file handoff API，禁止坏事件和重复 session 污染 Live ASR runtime

日期：2026-07-03

状态：Accepted

背景：

DEC-106 把下一步主线锁定为 Web local ASR event file handoff API 加固、desktop-side ASR worker handoff preflight 和受控 desktop no-op runtime。`POST /live/asr/local-event-files/sessions` 已经能把 `artifacts/tmp/asr_events` 下的 ASR event JSON 导入 Live ASR session，但仍缺少 future worker runtime 必须具备的幂等和坏输入边界：坏 JSON 会回显底层 parser 错误，未知事件会被归为 invalid file，重复 `session_id` 会覆盖已有 ASR live record。

决策：

加固 `/live/asr/local-event-files/sessions`：

- 文件形状错误进入 `blocked_by_invalid_events_file`：坏 JSON、JSON 非 list、list item 非 object、缺失文件。
- 事件契约错误进入 `blocked_by_event_contract`：未知 `event_type`、缺 `segment_id`、空 final/revision、非法时间戳、非法 confidence、revision 缺 `revision_of`。
- 重复 `session_id` 进入 `blocked_by_duplicate_session`，不得覆盖已有 Live ASR record。
- repo 内 `artifacts/tmp/asr_events` 的绝对路径允许读取并显示为 repo-relative path；repo 外绝对路径必须 `blocked_by_path_validation` 且 `events_path=<redacted_invalid_path>`。
- 成功和失败响应继续返回安全 flags：`safe_to_call_llm_now=false`、`safe_to_call_remote_asr_now=false`、`safe_to_read_user_audio_now=false`、`safe_to_read_configs_local_now=false`、`safe_to_capture_microphone_now=false`、`safe_to_download_models_now=false`。
- `data_dir` 模式下通过 local event file 创建的 Live ASR session 必须能跨 app 实例读取 JSON/SSE events。

原因：

- 这是 future desktop ASR worker output 到 Web Live ASR runtime 的第一条真实应用入口；它必须在 worker/mic 还没接入前先具备可审计的坏输入和幂等边界。
- 把 file shape、event contract 和 duplicate session 分成不同 ingest status，能让后续 worker preflight 快速定位是文件输出、事件 schema 还是 session lifecycle 问题。
- 重复 session 覆盖会破坏事件审计链，必须在接入真实桌面 worker 前禁止。

替代方案：

- 继续依赖 `tools/asr_live_pipeline_replay.py`：不采用，replay 工具不能替代运行时 API 的幂等和错误响应边界。
- 让 builder 自然抛错：不采用，会造成 500 或不稳定错误文本，也不利于未来 desktop worker 诊断。
- 允许重复 session 覆盖：拒绝，违反可追溯和不可篡改的会议事件链要求。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_repository.py`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

验证方式：

- TDD 红灯：新增 handoff hardening tests 初次运行 3 failed / 1 passed，覆盖 bad JSON sanitization、event contract status 和 duplicate session overwrite。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_create_asr_live_session_from_local_event_file_rejects_invalid_json_shapes code/web_mvp/backend/tests/test_app.py::test_create_asr_live_session_from_local_event_file_rejects_event_contract_errors code/web_mvp/backend/tests/test_app.py::test_create_asr_live_session_from_local_event_file_handles_absolute_and_missing_paths code/web_mvp/backend/tests/test_app.py::test_create_asr_live_session_from_local_event_file_rejects_duplicate_session_without_mutation code/web_mvp/backend/tests/test_app.py::test_create_asr_live_session_from_local_event_file_persists_across_app_instances -q -p no:cacheprovider`
  - Result: 5 passed.
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py -q -p no:cacheprovider`
  - Result: 267 passed.
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_live_events.py tests/test_asr_live_pipeline_replay.py -q -p no:cacheprovider`
  - Result: 45 passed.

复审条件：

下一步 desktop-side ASR worker handoff preflight 必须把 worker descriptor 输出对齐到这个已加固 API；不得绕过该 API 直接读取任意本地文件、启动 worker、访问麦克风、读取真实音频或调用远程 provider。

## DEC-108：新增 desktop-side ASR worker handoff preflight，只定义 descriptor/schema，不启动 worker

日期：2026-07-03

状态：Accepted

背景：

DEC-106 的第二个开发动作是 desktop-side ASR worker handoff preflight。DEC-107 已把 Web `/live/asr/local-event-files/sessions` 入口加固为可审计的 worker event file handoff API。下一步需要让 desktop/Tauri 侧先明确未来 ASR worker 要交什么 descriptor、event file path 和 chunk lifecycle，但仍不能启动 worker、访问麦克风、读写 event file、写 runtime audio 或调用 Web API。

决策：

新增 `PCWEB-095`：

- `code/desktop_tauri/asr-worker-handoff-preflight.policy.json`
- `tools/desktop_asr_worker_handoff_preflight.py`
- `tests/test_desktop_asr_worker_handoff_preflight.py`

该 preflight 只接受 caller-provided descriptor，校验：

- `descriptor_version=desktop_asr_worker_handoff_preflight.v1`
- `session_id`
- `provider`
- `event_file_path`
- `source_kind`
- `chunk_lifecycle`

当前只允许 `source_kind=preflight_only|synthetic`；`mic|file` 必须后续审批。`event_file_path` 只允许 `artifacts/tmp/asr_events`，拒绝 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 和 symlink 绕过。校验通过时只生成 future request preview：

```json
{
  "session_id": "...",
  "provider": "...",
  "events_path": "artifacts/tmp/asr_events/..."
}
```

原因：

- 桌面 worker 和 Web handoff API 之间需要先有稳定、可测试、无副作用的交接合同。
- 先固定 descriptor/chunk lifecycle 可以避免后续真实麦克风 adapter 或 ASR worker 直接写入任意路径、绕过 Web API 或混入真实用户音频。
- 当前本机仍未运行 Tauri/Cargo，也未获得麦克风/worker 启动授权；因此该阶段必须保持 schema-only。

替代方案：

- 直接在 Tauri command 中启动 ASR worker：拒绝，违反 no mic/no worker/no runtime write 边界。
- 让 worker 自由输出任意 JSON 路径：拒绝，会绕过 DRV-023 的 path guard 和 event contract。
- 直接接 `data/local_runtime` 作为 worker 输出根：暂不采用，真实 runtime 存储和删除策略还未进入实现阶段。

影响范围：

- `code/desktop_tauri/asr-worker-handoff-preflight.policy.json`
- `tools/desktop_asr_worker_handoff_preflight.py`
- `tests/test_desktop_asr_worker_handoff_preflight.py`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `README.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`

验证方式：

- TDD 红灯：新增测试初次运行 8 failed，因为 policy/tool 尚不存在。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_preflight.py -q -p no:cacheprovider`
  - Result: 8 passed.

复审条件：

只有在 Tauri no-op shell 受控运行和 IPC 结果记录完成后，才允许考虑把该 descriptor preflight 暴露到 desktop no-op command 或 UI；即使暴露，也仍不得启动真实 ASR worker、访问麦克风、读取真实用户音频、写 runtime audio 或调用远程 provider。

## DEC-109：主线收束为 Copilot 产品价值 tri-lane gate，并加固公开音频计划工具的预读边界

日期：2026-07-03

状态：Accepted

背景：

用户追问“完整计划是否写下、转写是否要网上寻找音频和模拟、最终再由用户做真实麦克风会议验证”。只读对抗审查确认：现有计划保留了 `ASR -> EvidenceSpan -> state/gap -> suggestion card -> feedback` 的产品价值方向，但近期工作仍有 ASR 评测惯性；另一个审查发现公开音频工具虽然不会下载，却允许 `--source-path` 和 `--planned-samples-file` 在路径边界校验前读取调用方传入的 JSON 路径，和不读 `configs/local` / private runtime 的边界不一致。

决策：

- 新增 `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`，把下一阶段主线固定为：
  - perfect transcript / mock ASR / real ASR 三路产品价值 gate。
  - 公开音频只做合法授权小样本治理，不下载 GB 级大包。
  - 桌面 no-op runtime / IPC。
  - ASR worker handoff integration。
  - 用户最终真实麦克风 20-30 分钟 shadow test。
- README 的“下一步”不再使用旧的 ASR bake-off 顺序，不再建议默认准备 5-10 段真实用户音频。
- 加固 `tools/public_audio_source_whitelist.py`：
  - 在读取 `--source-path` 前拒绝 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外绝对路径和 symlink 逃逸。
- 加固 `tools/public_audio_sample_extraction_plan.py`：
  - 在读取 `--planned-samples-file` 前拒绝同样的 forbidden/repo-outside/symlink 路径。
  - 没有具体 planned samples 时返回 `blocked_no_planned_samples`，不再显示为可下载评审 ready。
  - planned samples 必须绑定 selected `source_id/source_url/source_license`。
- 更新 `data/asr_eval/public_sample_plan.example.json` 的 required fields。

原因：

- 产品价值验证不能等 ASR 完美后才开始；perfect transcript lane 可以把产品逻辑问题和 ASR 质量问题拆开。
- 公开音频官方来源已经明确，但 AliMeeting/AISHELL-4/AISHELL-1 默认包都是 GB 级；没有 3-5 个 clip 的具体 manifest 时，状态必须明确 blocked，而不是“看起来可以下载评审”。
- CLI override 文件属于本地输入边界，必须先拒绝敏感根和 symlink 逃逸，再读取内容。

替代方案：

- 继续以 FunASR/sherpa/SenseVoice 横评作为下一主线：拒绝，容易把产品做成转写工具。
- 允许空 planned samples 进入 manual download review：拒绝，会误导人工流程。
- 只在文档里提醒不要读 forbidden path，不改工具：拒绝，边界必须由测试和代码执行。

影响范围：

- `tools/public_audio_source_whitelist.py`
- `tools/public_audio_sample_extraction_plan.py`
- `tests/test_public_audio_source_whitelist.py`
- `tests/test_public_audio_sample_extraction_plan.py`
- `data/asr_eval/public_sample_plan.example.json`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`
- `docs/decision-log.md`

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py -q -p no:cacheprovider`
  - Result: 6 failed, 13 passed。失败覆盖 forbidden/repo-outside/symlink 预读、零样本状态和 attribution 未绑定。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py -q -p no:cacheprovider`
  - Result: 19 passed, 1 warning。
- CLI smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
  - Result: `source_validation_status=passed`，`safe_to_download_now=false`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio --source-split eval --sample-budget-count 3 --sample-budget-minutes 9 --max-clip-seconds 180`
  - Result: `plan_status=blocked_no_planned_samples`，`download_command=null`，`safe_to_download_now=false`。

复审条件：

下一轮必须优先实现 Copilot product value tri-lane gate。任何继续新增 provider/readiness 文档的动作，都必须说明它如何直接改善“能否在会议中及时发现工程缺口并形成可追溯建议”。

## DEC-110：实现 Copilot product value tri-lane gate，先拆分产品逻辑和 ASR 质量瓶颈

日期：2026-07-03

状态：Accepted

背景：

DEC-109 把下一阶段主线收束为 perfect transcript / mock ASR / real ASR 三路产品价值 gate。问题是：如果只看真实 ASR smoke 的实体召回和 candidate 数量，无法判断失败来自 Copilot gap/candidate 逻辑、streaming event contract，还是 ASR 技术实体质量。用户明确担心项目陷入“永无止境的评测”并忘记产品初心，因此需要一个能先回答产品价值问题的本地 gate。

决策：

新增 `DRV-025`：

- `tools/copilot_product_value_tri_lane_gate.py`
- `tests/test_copilot_product_value_tri_lane_gate.py`
- `docs/copilot-product-value-gate.md`

该 gate 对同一 synthetic scenario 生成三路报告：

- `perfect_transcript`：从 synthetic script turns 生成 perfect final/eos ASR events。
- `mock_asr`：读取 mock/fixture ASR event JSON。
- `real_asr`：读取 real ASR event JSON 和对应 smoke report。

三路都复用 Web Live ASR builder，输出 EvidenceSpan、state_event、scheduler_event、suggestion_candidate_event 和 llm_request_draft_event。顶层按以下优先级给出 `overall_decision`：

1. perfect transcript lane 失败：`blocked_by_product_logic`
2. mock ASR lane 失败：`blocked_by_stream_contract`
3. real ASR lane 技术实体召回不足：`blocked_by_asr_quality`
4. 三路通过：`product_logic_ready`

该工具始终保持：

- `safe_to_call_llm_now=false`
- `safe_to_call_remote_asr_now=false`
- `safe_to_capture_microphone_now=false`
- `safe_to_read_user_audio_now=false`
- `safe_to_read_configs_local_now=false`
- `safe_to_download_models_now=false`
- `safe_to_write_runtime_audio_now=false`

原因：

- perfect transcript lane 可以在 ASR 不完美时提前验证 Copilot brain 本身是否值得继续。
- mock ASR lane 单独验证 streaming event contract 和增量链路。
- real ASR lane 单独承接技术实体 recall、final/eos、provider error 等输入质量风险。
- 这个拆分能避免“ASR 质量差”掩盖“产品逻辑也不行”，也能避免“产品逻辑可用”被 real ASR 早期质量拖慢所有开发。

替代方案：

- 继续只用 `synthetic_product_value_gate`：不采用，它无法把 perfect/mock/real 三类原因拆开。
- 直接接 LLM 中转站评估建议质量：不采用，当前 no-LLM candidate 和 EvidenceSpan 链路还需要先证明，且会引入费用。
- 只写文档不做工具：不采用，用户要求自测和实现，必须有可重复 gate。

影响范围：

- `tools/copilot_product_value_tri_lane_gate.py`
- `tests/test_copilot_product_value_tri_lane_gate.py`
- `docs/copilot-product-value-gate.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`
- `docs/decision-log.md`

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_tri_lane_gate.py -q -p no:cacheprovider`
  - Result: 6 failed，因为工具尚不存在。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_tri_lane_gate.py -q -p no:cacheprovider`
  - Result: 6 passed, 1 warning。
- CLI smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_tri_lane_gate.py --script-json data/asr_eval/synthetic_meetings/scripts/api-review.json --mock-events artifacts/tmp/asr_events/api-review-001.sherpa.events.json --real-events artifacts/tmp/asr_events/api-review-001.sherpa.events.json --real-smoke-report artifacts/tmp/asr_reports/api-review-001.sherpa.smoke-report.json --real-provider sherpa_onnx_streaming`
  - Result: `overall_decision=blocked_by_asr_quality`；perfect transcript lane 和 mock lane 为 `product_logic_ready`，real ASR lane 因 normalized technical entity recall 0.5 阻断。

复审条件：

下一步应把 DRV-025 扩成 5 synthetic scripts 的 batch summary。若 perfect transcript lane 在更多工程脚本上失败，应先修 gap/candidate 逻辑；若 perfect/mock 通过但 real lane 失败，才继续 FunASR/hotword/normalizer 或模型审批。

## DEC-111：把 product value tri-lane gate 扩展为五场景 batch summary，避免把所有失败误判为 ASR 问题

日期：2026-07-03

状态：Accepted

背景：

DEC-110 的单场景 `api-review-001` tri-lane gate 显示 perfect/mock lane 可用、real ASR lane 因技术实体 recall 0.5 被阻断。但用户要求的产品不是单一 API review 场景，也不能因为一个场景通过就继续投入 ASR 横评。需要把同样拆因方法扩展到 5 个 synthetic scripts，形成当前产品价值矩阵。

决策：

新增 `DRV-026`：

- `tools/copilot_product_value_batch_gate.py`
- `tests/test_copilot_product_value_batch_gate.py`
- `docs/copilot-product-value-batch-result-2026-07-03.md`

该 batch gate：

- 自动读取 `data/asr_eval/synthetic_meetings/scripts/*.json`。
- 对每个 script 用 `{script_id}` 套用 mock/real ASR events 和 real smoke report path。
- 调用 DRV-025 的 tri-lane gate，不复制产品逻辑。
- 输出 scenario_count、decision_counts、perfect_lane_ready_count、mock_lane_ready_count、real_asr_blocked_count、non_engineering_candidate_count、scenario summaries 和 batch-level `overall_decision`。
- 任一 scenario 输入 missing、forbidden root、仓库外路径或 symlink 逃逸时，整体 blocked。
- 保持 no mic/no real audio/no configs/no remote ASR/no LLM/no model download/no runtime audio write。

原因：

- 单场景结果不足以指导下一阶段资源分配。
- batch summary 能区分：
  - perfect lane 失败：产品逻辑覆盖问题。
  - mock lane 失败：streaming/event 输入或场景规则问题。
  - real lane 失败：ASR 质量问题。
  - non-engineering control：误报安全边界。
- 当前五场景结果显示不是只有 ASR 问题，继续只做 ASR 横评会偏离产品初心。

当前 batch smoke 结论：

```json
{
  "overall_decision": "blocked_by_product_logic",
  "scenario_count": 5,
  "engineering_scenario_count": 4,
  "negative_control_count": 1,
  "decision_counts": {
    "blocked_by_asr_quality": 2,
    "blocked_by_product_logic": 1,
    "blocked_by_stream_contract": 1,
    "product_logic_ready": 1
  },
  "perfect_lane_ready_count": 4,
  "mock_lane_ready_count": 3,
  "real_asr_blocked_count": 4,
  "non_engineering_candidate_count": 0
}
```

场景级结论：

- `api-review-001`：perfect/mock ready，real ASR blocked by recall 0.5。
- `release-review-001`：perfect/mock ready，real ASR blocked by recall 0.25。
- `architecture-review-001`：perfect lane 失败，先补产品逻辑覆盖。
- `incident-review-001`：perfect lane ready，但 mock/real lane 失败，需检查事件输入和规则覆盖。
- `non-engineering-control-001`：三路 ready，candidate 为 0，负控通过。

替代方案：

- 继续只看 `synthetic_product_value_gate` 的 ASR recall：拒绝，它无法把 architecture perfect lane 失败暴露出来。
- 继续只调 FunASR/normalizer：暂缓，当前至少有一个 perfect lane 产品逻辑问题。
- 直接进入真实麦克风：拒绝，4 个工程场景 real ASR 仍被阻断，产品逻辑也未覆盖完整。

影响范围：

- `tools/copilot_product_value_batch_gate.py`
- `tests/test_copilot_product_value_batch_gate.py`
- `docs/copilot-product-value-batch-result-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`
- `docs/decision-log.md`

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_batch_gate.py -q -p no:cacheprovider`
  - Result: 4 failed，因为工具尚不存在。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_batch_gate.py -q -p no:cacheprovider`
  - Result: 4 passed, 1 warning。
- CLI smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_batch_gate.py --scripts-root data/asr_eval/synthetic_meetings/scripts --mock-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' --real-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' --real-smoke-report-pattern 'artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json' --real-provider sherpa_onnx_streaming`
  - Result: `overall_decision=blocked_by_product_logic`，非工程 candidate=0。

复审条件：

下一步优先补 `architecture-review-001` perfect transcript lane 的产品逻辑候选；同时检查 `incident-review-001` mock/real event 文本和规则覆盖。只有 perfect/mock lane 扩展稳定后，才继续投入 FunASR/hotword/normalizer 或模型审批。

## DEC-112：完整计划已锁定，下一轮先修产品逻辑再回到 ASR 和公开音频执行

日期：2026-07-03

状态：Accepted；next-action superseded by DEC-113 after DRV-027 completion

背景：

用户再次确认：完整计划是否已经写下；转写类验证需要由我先联网寻找合法公开音频并做模拟，最终真实麦克风会议由用户再验证。此前 DRV-024/025/026 已经把公开音频边界、tri-lane product value gate 和 5 场景 batch gate 实现出来，但部分文档仍把已完成动作写成“下一步”，容易让后续开发误判为继续评测或重复建设。

决策：

更新计划锁定口径：

- 完整计划以 `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`、`docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md` 和 `docs/project-current-status-and-forward-plan-2026-07-03.md` 为准。
- 公开音频来源仍只允许 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111 和 AISHELL-1 / OpenSLR SLR33 这类官方授权清楚来源；Bilibili、YouTube、播客、直播回放、公开课和大会录播不进入自动评测。
- 公开音频当前状态是 no-download sample plan：没有具体 3-5 个 clip manifest、archive member path、clip start/end、sha256 和 license citation 前，不下载、不解压、不转码、不喂 ASR。
- 当前 5 场景 batch gate 的主结论是 `overall_decision=blocked_by_product_logic`，所以下一轮主线不是继续扩 ASR/provider 横评，而是：
  1. 补 `architecture-review-001` perfect transcript lane 产品逻辑覆盖。
  2. 查 `incident-review-001` mock/real event lane 失败原因。
  3. 复跑 5 场景 batch gate，保持非工程 control 0 candidate。
  4. 只有 perfect/mock lane 更稳定后，再回到 FunASR/hotword/normalizer/公开音频小样本。
  5. 用户最终执行真实麦克风 20-30 分钟中文技术会议 shadow test。

原因：

- 公开音频只能补会议声学、多人、远场、重叠说话和切句风险，不能证明工程建议卡片的价值。
- `architecture-review-001` 在 perfect transcript lane 失败，说明即使 ASR 完美，产品 brain 也有规则覆盖缺口；继续堆 ASR 样本不能解决该问题。
- `incident-review-001` perfect lane 通过但 mock/real lane 失败，更像事件输入、streaming contract 或事故规则覆盖问题，需要先拆因。
- 非工程负控当前保持 candidate=0，这是必须保护的安全锚点。

替代方案：

- 继续找更多公开视频或播客做测试：拒绝，授权链不稳，且不能证明产品价值。
- 继续横评更多 ASR provider：暂缓，perfect/mock lane 尚未稳定。
- 直接进入真实麦克风会议：拒绝，桌面 runtime、麦克风 adapter、ASR worker 和 real ASR 质量仍未达进入条件。
- 降级成普通转写工具：拒绝，违背 DEC-001 产品定位。

影响范围：

- `docs/current-mainline-index.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`
- `docs/decision-log.md`

成本/隐私影响：

- 默认仍不增加远程 ASR 费用。
- 默认不读取真实用户音频、不访问麦克风、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载 FunASR/ModelScope 模型。
- 公开音频下载只有在 sample manifest 和人工审批边界明确后才允许。

验证方式：

- 计划文档检查：确认下一轮不再把 DRV-023/024/025/026 当作未来任务，而是明确 architecture/incident 产品逻辑优先。
- Focused tests：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_copilot_product_value_tri_lane_gate.py tests/test_copilot_product_value_batch_gate.py -q -p no:cacheprovider`
- Batch gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_batch_gate.py --scripts-root data/asr_eval/synthetic_meetings/scripts --mock-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' --real-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' --real-smoke-report-pattern 'artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json' --real-provider sherpa_onnx_streaming`
- 完整本地门禁：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser`

复审条件：

当 `architecture-review-001` perfect transcript lane 变为 `product_logic_ready`，且 `incident-review-001` mock lane 拆因完成、非工程 control 仍为 0 candidate 后，再复审是否进入 ASR 质量、FunASR 模型审批、公开音频小样本执行或 desktop runtime 阶段。

## DEC-113：完成 DRV-027 架构评审产品逻辑修复，主阻塞从产品逻辑推进到 ASR 质量

日期：2026-07-03

状态：Accepted

背景：

DEC-112 要求下一轮先补 `architecture-review-001` perfect transcript lane 产品逻辑覆盖，并检查 `incident-review-001` mock/real event lane。原因是 DRV-026 batch gate 显示 `overall_decision=blocked_by_product_logic`，不能继续用 ASR/provider 横评掩盖产品 brain 的缺口。

决策：

新增 DRV-027：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/tests/test_live_events.py::test_build_asr_live_events_extracts_architecture_review_gap_candidates`
- `tests/test_copilot_product_value_tri_lane_gate.py::test_tri_lane_gate_architecture_review_perfect_lane_detects_evidence_backed_gap`

实现范围：

- 扩展工程语境 marker：`QPS`、`mysql`、`redis`、`cluster`、`feature-store`、`recommendation-service` 等。
- 扩展未闭环问题 marker：`还没`、`还没有`、`没定`、`未定`、`未安排`、`待定`。
- 增加架构风险启发：在工程语境中识别 `缓存穿透`、`打到 mysql/数据库`、`timeout/超时`、`堆积`、`告警延迟`，且需要 `可能/如果/会/增多/峰值` 这类不确定或容量触发词。
- 仍保持 no LLM、no remote ASR、no mic、no private audio、no `configs/local`、no model download。

结果：

真实 batch gate 从：

```json
{
  "overall_decision": "blocked_by_product_logic",
  "decision_counts": {
    "blocked_by_asr_quality": 2,
    "blocked_by_product_logic": 1,
    "blocked_by_stream_contract": 1,
    "product_logic_ready": 1
  },
  "perfect_lane_ready_count": 4,
  "mock_lane_ready_count": 3,
  "real_asr_blocked_count": 4,
  "non_engineering_candidate_count": 0
}
```

推进为：

```json
{
  "overall_decision": "blocked_by_asr_quality",
  "decision_counts": {
    "blocked_by_asr_quality": 4,
    "product_logic_ready": 1
  },
  "perfect_lane_ready_count": 5,
  "mock_lane_ready_count": 5,
  "real_asr_blocked_count": 4,
  "non_engineering_candidate_count": 0
}
```

原因：

- `architecture-review-001` 现在能从 `缓存穿透时可能会打到 mysql` 生成 Risk candidate，并从 `压测 owner 还没安排` 生成 OpenQuestion candidate。
- `incident-review-001` mock lane 也不再因本地规则触发失败而阻塞。
- 非工程 control 仍保持 0 candidate，说明新增 marker 没有把普通行政/团建场景误判为工程会议。
- 这证明当前 product brain 的 perfect/mock gate 已基本过线，下一步资源应转向 real ASR 中文技术实体质量，而不是继续扩大产品逻辑 gate。

替代方案：

- 继续只做 ASR/provider 横评：拒绝，DRV-026 已证明当时还存在 product logic 缺口。
- 大幅放宽工程语境或问题 marker：拒绝，会提高非工程误报风险。
- 用 LLM 直接补架构/事故判断：暂不采用，当前阶段要求 no-LLM deterministic skeleton 先过线。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py`
- `code/web_mvp/backend/tests/test_live_events.py`
- `tests/test_copilot_product_value_tri_lane_gate.py`
- `docs/copilot-product-value-batch-result-2026-07-03.md`
- `docs/current-mainline-index.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`
- `docs/decision-log.md`

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_tri_lane_gate.py::test_tri_lane_gate_architecture_review_perfect_lane_detects_evidence_backed_gap -q -p no:cacheprovider`
  - Result: failed，`overall_decision` 仍为 `blocked_by_product_logic`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_live_events.py::test_build_asr_live_events_extracts_architecture_review_gap_candidates -q -p no:cacheprovider`
  - Result: failed，state_events 为空。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_tri_lane_gate.py::test_tri_lane_gate_architecture_review_perfect_lane_detects_evidence_backed_gap -q -p no:cacheprovider`
  - Result: 1 passed, 1 warning。
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_live_events.py::test_build_asr_live_events_extracts_architecture_review_gap_candidates -q -p no:cacheprovider`
  - Result: 1 passed, 1 warning。
- Focused regression:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_tri_lane_gate.py tests/test_copilot_product_value_batch_gate.py code/web_mvp/backend/tests/test_live_events.py -q -p no:cacheprovider`
  - Result: 51 passed, 1 warning。
- Batch gate:
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_batch_gate.py --scripts-root data/asr_eval/synthetic_meetings/scripts --mock-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' --real-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' --real-smoke-report-pattern 'artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json' --real-provider sherpa_onnx_streaming`
  - Result: `overall_decision=blocked_by_asr_quality`，perfect lane ready 5/5，mock lane ready 5/5，non-engineering candidate=0。

复审条件：

下一步进入 ASR 质量受控路径。若用户提供完整本地 FunASR model dir 或明确批准 DRV-019 模型下载审批包，运行 FunASR synthetic smoke；否则只能继续 bounded normalizer/hotword、公开音频 no-download sample manifest 或 desktop no-op runtime，不得静默下载模型或引入默认远程 ASR 费用。

## DEC-114：DRV-028 bounded normalizer 到达安全边界，剩余 ASR 质量不能靠规则猜实体

日期：2026-07-03

状态：Accepted

背景：

DRV-027 后，Copilot product value batch gate 已从 `blocked_by_product_logic` 推进到 `blocked_by_asr_quality`：5 个 synthetic 场景 perfect/mock lane 均 ready，非工程 control 保持 0 candidate，但 real ASR lane 因 sherpa 中文技术实体 recall 不达标仍阻塞。

决策：

新增 DRV-028 bounded normalizer：

- 只恢复 ASR 文本中已有的显式线索。
- 不从 `<unk>`、完全缺失文本或脚本答案中猜实体。
- raw recall 和 normalized recall 必须分开记录。
- normalizer 只能作为 ASR 文本后处理，不得伪装成 ASR 模型质量提升。

当前允许的恢复：

- `消费堆积 ... 最高到了` 这类上下文可恢复 `lag`。
- 架构/容量上下文中出现 `峰值按...`，并伴随 `缓存穿透`、`压测`、`扩容` 或 `降级`，可恢复 `QPS`。

当前禁止的恢复：

- 不从事故脚本中的 `<unk>` 猜 `order-worker`、`timeout` 或 `监控阈值`。
- 不从架构脚本中的 `<unk>` 猜 `feature-store`、`mysql`、`recommendation-service` 或 `redis cluster`。
- 不用脚本 expected entities 反向补全 ASR 输出。

结果：

- `incident-review-001` normalized recall 从 0.0 提升到 0.25，匹配 `lag`。
- `architecture-review-001` normalized recall 从 0.0 提升到 0.2，匹配 `QPS`。
- 当前 batch 仍为 `overall_decision=blocked_by_asr_quality`，说明剩余质量问题必须靠更好的 ASR provider、热词能力、本地 FunASR 模型目录/审批，或最终产品降级策略解决。

验证方式：

- `cd code/asr_runtime && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_transcript_normalizer.py tests/test_transcript_report.py -q -p no:cacheprovider`
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_asr_smoke_report.py -q -p no:cacheprovider`
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_copilot_product_value_tri_lane_gate.py tests/test_copilot_product_value_batch_gate.py code/web_mvp/backend/tests/test_live_events.py -q -p no:cacheprovider`

复审条件：

如果后续真实 ASR 文本出现新的显式技术线索，可以继续做 bounded normalizer；否则不得继续硬编码缺失实体。下一步优先 FunASR 本地模型目录/DRV-019 模型审批，或推进 desktop no-op runtime/IPC。

## DEC-115：公开视频检索已收口，MagicHub Web Meeting 只作为观察候选不进入自动评测

日期：2026-07-03

状态：Accepted

背景：

用户要求转写验证由我寻找网上音频和模拟，最终由用户进行真实麦克风会议验证。此前已锁定 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111 和 AISHELL-1/OpenSLR SLR33 作为公开授权来源候选，但 OpenSLR 默认包均为 GB 级。

决策：

- 继续保留 AliMeeting 和 AISHELL-4 作为公开会议声学主候选，但没有 3-5 个 clip 的 sample manifest 前不下载。
- AISHELL-1 只作为普通话 ASR sanity check，不证明会议价值。
- 2026-07-03 追加检索到 MagicHub / Mandarin Chinese Conversational Speech Corpus - Web Meeting，页面标注 CC BY-NC-ND 4.0，约 5.2 小时。
- MagicHub Web Meeting 因 `NC/ND` 对商业化验证、切分/转码/衍生处理边界更敏感，且需要人工复核使用条件，当前只记录为 `observed_but_not_whitelisted_sources`。

执行边界：

- 不自动下载 MagicHub。
- 不抽样、不转码、不喂给 ASR。
- 不进入产品价值 gate。
- 不把它当作绕过 OpenSLR GB 级包的替代白名单。
- 不抓 Bilibili、YouTube、播客、公开课、直播回放或版权链不清来源。

影响范围：

- `data/asr_eval/public_sources.json`
- `tests/test_public_audio_source_whitelist.py`
- `docs/current-mainline-index.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

复审条件：

只有在完成单独授权和用途复核、并且形成可审查 sample manifest 后，才允许把某个新来源从 observed candidate 提升到 whitelist。默认下一步不是继续找更多网站，而是推进合成中文技术会议、ASR 质量受控路径、desktop runtime 和最终用户真实麦克风验收准备。

## DEC-116：新增 PCWEB-096 desktop ASR worker handoff local dry-run bridge，推进桌面 worker 到 Web Live ASR 主线

日期：2026-07-03

状态：Accepted

背景：

DEC-108/PCWEB-095 已定义 desktop-side ASR worker handoff preflight：future worker descriptor 可以生成 `/live/asr/local-event-files/sessions` request preview，但默认不启动 worker、不读写 event file、不调用 Web API。当前 ASR normalizer 已到 DRV-028 安全边界，继续补规则会变成从 `<unk>` 猜实体。因此下一步应推进桌面 handoff 主线，而不是继续扩大 ASR 后处理。

决策：

新增 PCWEB-096：

- `code/desktop_tauri/asr-worker-handoff-local-dry-run.policy.json`
- `tools/desktop_asr_worker_handoff_local_dry_run.py`
- `tests/test_desktop_asr_worker_handoff_local_dry_run.py`
- `docs/pcweb-096-desktop-asr-worker-handoff-local-dry-run-plan.md`

功能：

- 默认 `mode=preview_only`：复用 PCWEB-095 descriptor preflight，只返回 `future_web_handoff_request_preview`，不读 event file、不调用 Web API、不写 session。
- 显式 `mode=synthetic_local_test`：只允许 `source_kind=preflight_only|synthetic`，只允许 event file 位于 `artifacts/tmp/asr_events`，只允许 data dir 位于 `artifacts/tmp/desktop_handoff_dry_run`，用 FastAPI `TestClient` 调用现有 Web handoff API。
- 成功时只返回 Web handoff summary：session id、ingest mode、provider、`transcript_final` count、`suggestion_card` count 和 LLM status。
- synthetic local test 会临时替换 Web app `REPO_ROOT`，并在 finally 中恢复，避免污染后续测试。
- Web handoff 422 和 custom policy drift 都必须保持 blocked，且不能把 `safe_to_read_approved_asr_event_file_now` 或 `safe_to_mutate_temp_web_session_now` 误置为 true。

安全边界：

- 不启动 worker。
- 不访问麦克风或系统音频。
- 不请求权限。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local`、provider config、API key、环境密钥或 keychain。
- 不调用远程 ASR、LLM 或中转站。
- 不下载 FunASR/ModelScope 模型。
- 不写 runtime audio。
- 不运行 Tauri、Cargo、package manager 或 shell command。

原因：

- 这一步把已完成的 Web local ASR event file handoff API 和 desktop descriptor preflight 串起来，证明 future worker 输出能进入同一条 Live ASR session 链路。
- 它推进桌面 PC 产品主线，同时不越过模型下载、麦克风、远程调用或工具链安装边界。
- ASR 质量仍由 FunASR 本地模型目录/审批或后续 provider 输出解决，PCWEB-096 不假装提升 ASR 召回。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py -q -p no:cacheprovider`
  - Result: 6 failed，policy/tool 不存在。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py -q -p no:cacheprovider`
  - Result: 6 passed, 2 warnings。
- 全局状态恢复红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_calls_web_handoff_api_in_temp_data_dir -q -p no:cacheprovider`
  - Result: failed，Web app `REPO_ROOT` 未恢复。
- 修复后：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py -q -p no:cacheprovider`
  - Result: 6 passed, 2 warnings。
- 补充护栏：
  - Web handoff 422 不误标成功 flags；custom policy 不能启用 worker/audio/remote false flags；blocked CLI status 返回非 0。
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py -q -p no:cacheprovider`
  - Result: 9 passed, 2 warnings。
- 相邻回归：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_preflight.py tests/test_desktop_asr_worker_handoff_local_dry_run.py -q -p no:cacheprovider`
  - Result: 17 passed, 2 warnings。

复审条件：

下一步可把 PCWEB-096 dry-run 状态接入 Web/Tauri no-op UI，或在获得 FunASR 本地模型目录/审批后用真实本地 provider event file 替换 synthetic event file。不得把 synthetic local test 扩展成真实麦克风采集或 worker 启动，除非新决策明确授权。

## DEC-117：锁定完整未来计划，公开音频只做授权样本计划，真实麦克风由用户最终验证

日期：2026-07-03

状态：Accepted

背景：

当前计划分散在 `current-mainline-index`、public audio master plan、mainline execution plan、project status 和 PCWEB 单项计划中。用户明确要求确认完整计划是否已经写下，并说明转写验证应先由我通过网上公开音频和模拟自测完成，最终真实麦克风会议由用户验证。为避免后续再次陷入“评测/ready 文档循环”，需要把主线收束到一个总控入口。

决策：

新增 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 作为完整未来计划总控入口，并在 README 与 `docs/current-mainline-index.md` 中加入指针。

总控结论：

- 主线固定为 `自建中文技术会议脚本/合成音频 + 公开授权中文会议音频 sample manifest -> 本地 ASR partial/final/revision -> EvidenceSpan -> engineering gap candidate -> suggestion candidate/card -> desktop runtime / ASR worker handoff -> 用户真实麦克风 shadow test`。
- 公开音频只用于声学、多人、远场、重叠说话、切句和 ASR event contract；不用于证明工程建议卡片价值。
- 默认白名单仍是 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111、AISHELL-1 / OpenSLR SLR33；MagicHub Web Meeting 只保留为 observed-but-not-whitelisted。
- 不抓取 Bilibili、YouTube、播客、直播回放、公开课、技术大会录播或授权链不清音频。
- 没有 3-5 个可复核 clip 的 sample manifest 时，公开音频阶段保持 `blocked_no_planned_samples`，不下载、不抽取、不转码。
- sherpa-onnx 继续作为本地性能基线；FunASR/Paraformer 仍是中文质量主候选，但必须有本地模型目录或用户明确批准 DRV-019 模型下载审批包。
- 真实麦克风会议必须等 desktop runtime、IPC、mic adapter、ASR worker handoff、stop/delete 和隐私边界完成后由用户执行 shadow test。
- 文档权威顺序固定为 2026-07-03 P0/P1 文档优先，`docs/superpowers/plans/**` 和 2026-07-02 阶段报告只作历史执行记录。
- 当前唯一推荐下一张桌面票是 PCWEB-097：把 PCWEB-096 desktop ASR worker handoff dry-run readiness/status 展示到 Web/Tauri no-op UI/API。
- 真实麦克风 first pilot 必须经过同意、start/pause/resume/stop/delete、ignored audio chunk root、默认不上传、默认不远程 ASR、导出 metrics/timeline/feedback 和 Go/Pivot/Stop checklist。

原因：

- 当前 product value perfect/mock lane 已经 5/5 ready，继续宽泛评测不能增加产品确定性。
- real ASR 仍阻塞在中文技术实体质量，必须进入 FunASR/模型审批/降级决策，而不是继续横向堆 provider 或 readiness。
- 公开音频官方来源包体量为 GB 级，默认下载会带来成本、磁盘和清理风险；必须先把样本计划具体化。
- 真实麦克风验证涉及用户隐私和本机权限，必须由用户显式启动并最终验收。

验证方式：

- 文档落地：
  - `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
  - `docs/current-mainline-index.md`
  - `README.md`
  - `docs/decision-log.md`
- 本决策不运行麦克风、Tauri、Cargo、远程 ASR/LLM、模型下载或公开音频下载。

复审条件：

后续每轮主线进展必须回答：

- 技术实体 normalized recall 是否接近 first-pilot 门槛，或为什么仍 blocked。
- final/revision 是否能在 10-30 秒窗口形成 EvidenceSpan 和工程 gap candidate。
- 非工程 control 是否仍为 0 工程 state/candidate/card。

如果三项没有推进，应转入模型审批、远程 ASR 可选模式或 MVP 降级决策，而不是继续增加评测层。

## DEC-118：实现 PCWEB-097，将 PCWEB-096 dry-run readiness 接入 Web/Tauri no-op UI/API

日期：2026-07-03

状态：Accepted

背景：

DEC-116/PCWEB-096 已把 desktop ASR worker descriptor preflight 与 Web local ASR event file handoff API 串成受控 dry-run，但状态只存在于工具/CLI 侧。DEC-117 已把当前唯一推荐下一张桌面票锁定为 PCWEB-097，要求把 PCWEB-096 dry-run readiness/status 展示到 Web/Tauri no-op UI/API，避免继续停留在评估循环。

决策：

新增 PCWEB-097：

- `GET /desktop/asr-worker-handoff-dry-run-readiness`
- `desktop-asr-handoff-dry-run-panel`
- `loadDesktopAsrHandoffDryRunReadiness`
- `renderDesktopAsrHandoffDryRunReadiness`
- `docs/pcweb-097-desktop-asr-worker-handoff-dry-run-readiness-ui-plan.md`

响应和 UI 展示：

- `desktop_asr_worker_handoff_dry_run_mode=readiness_only`
- `desktop_asr_worker_handoff_dry_run_status=preview_only_ready`
- `pcweb_096_default_dry_run_status=preview_ready_no_web_mutation`
- `synthetic_local_test_status=explicit_mode_only`
- `worker_execution_status=not_started`
- `event_file_read_status=not_read`
- `web_handoff_mutation_status=not_mutated`
- allowed roots：`artifacts/tmp/asr_events` 和 `artifacts/tmp/desktop_handoff_dry_run`
- 6 个 readiness phases、blockers、next decisions 和 scoped false safety flags

安全边界：

- 不启动 worker。
- 不读取 event file。
- 不调用 Web handoff mutation API。
- 不访问麦克风或系统音频。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local`、provider config、API key、环境密钥或 keychain。
- 不调用远程 ASR、LLM 或中转站。
- 不下载 FunASR/ModelScope 模型。
- 不运行 Tauri、Cargo、package manager 或 shell command。
- 被动加载该 panel 不创建 session JSON、Live ASR session JSON 或 desktop handoff temp dir。

原因：

- 这一步把 PCWEB-096 从 CLI dry-run 推进到桌面工作台可见状态，是 desktop runtime/ASR worker handoff 主线的实际增量。
- 它仍保持 no-action/no-audio/no-remote/no-model/no-Tauri 边界，不越过用户授权和工具链审批。
- 后续真实 worker、mic adapter 或 FunASR 质量验证可以基于同一 handoff readiness 继续推进，而不是重新设计入口。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_does_not_probe_audio_or_read_secrets code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_with_data_dir_does_not_create_local_storage code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: 5 failed，缺少 endpoint、panel 和 frontend loader。
- 绿灯：
  - 同一 focused command
  - Result: 5 passed, 2 warnings。

复审条件：

下一步可继续真实 Tauri no-op run、ASR worker process contract 或 mic adapter contract，但必须另起明确审批/计划；不得把 PCWEB-097 解释成已启动 worker、已验证真实 ASR、已访问麦克风或已具备真实会议能力。

## DEC-119：确认完整计划、责任边界和下一步 PCWEB-098，停止继续扩散评测

日期：2026-07-03

状态：Accepted

背景：

用户明确询问“完整计划写下来了吗”，并要求转写验证由我先通过网上公开音频和模拟完成，最终再由用户做真实麦克风会议验证。当前文档已经覆盖完整主线，但入口较多，且执行容易被 ASR/provider 横评、公开音频检索和 readiness 文档带成循环。需要把责任边界和下一步收敛动作落档。

决策：

新增 `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`，并同步更新 README、`docs/current-mainline-index.md`、`docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`、`docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md` 和 `docs/project-current-status-and-forward-plan-2026-07-03.md`。

确认事项：

- 完整计划已经写下，当前不是缺计划，而是缺可用中文技术实体 ASR 质量和可运行桌面端闭环。
- 我负责公开授权音频来源审查、no-download sample manifest、合成音频模拟、本地 ASR 自测、指标报告和 ASR event/worker handoff 前置链路。
- 用户最终负责真实麦克风会议 shadow test。
- 当前自动公开音频白名单只包含 AliMeeting、AISHELL-4 和 AISHELL-1；THCHS-30 只作为观察/低优先级备选，不进入当前自动白名单。
- 下一张桌面主线票锁定为 PCWEB-098：desktop ASR worker process contract。
- PCWEB-098 实施时必须先修正 PCWEB-097 readiness endpoint/UI 中仍指向 `next_pcweb_id=PCWEB-097` 的旧提示，改为 PCWEB-098。

停止事项：

- 不继续泛化 ASR/provider 横评。
- 不继续搜版权不清音频。
- 不把公开音频当产品价值证明。
- 不把更多 readiness/report-only 文档当主线进展。
- 不从 `<unk>` 或缺失 ASR 文本猜实体。
- 不访问麦克风或真实录音。

下一步：

- PCWEB-098 只定义 future worker lifecycle、command catalog、resource limits、event output contract、approved roots 和 no-spawn/no-audio/no-remote safety flags。
- 公开音频只做 AliMeeting Eval 或 AISHELL-4 test 的 3-5 个 planned samples no-download manifest；做不到就 blocked。
- FunASR 本地模型目录就绪则跑一次 synthetic smoke；否则停在 DRV-019 Need model approval。

验证方式：

- 本次为文档决策，不运行麦克风、Tauri、Cargo、远程 ASR/LLM、模型下载或公开音频下载。
- 文档一致性通过 `rg` 检查 PCWEB-097/098、THCHS、real mic boundary 和 plan confirmation 指针。

复审条件：

如果后续真实麦克风前置条件、公开音频下载审批、FunASR 模型目录或远程 ASR 策略发生变化，必须新增决策，不得只在聊天记录里改变边界。

## DEC-120：实现 PCWEB-098 desktop ASR worker process contract

日期：2026-07-03

状态：Accepted

背景：

DEC-119 已把下一张桌面主线票锁定为 PCWEB-098。PCWEB-097 只是把 PCWEB-096 dry-run readiness 展示到 Web/Tauri no-op UI/API，仍缺少 future ASR worker sidecar 的进程合同。没有该合同，后续容易直接跳到启动 worker、麦克风或 Tauri/Cargo，越过安全边界。

决策：

新增 PCWEB-098：

- `code/desktop_tauri/asr-worker-process-contract.policy.json`
- `tools/desktop_asr_worker_process_contract.py`
- `tests/test_desktop_asr_worker_process_contract.py`
- `docs/pcweb-098-desktop-asr-worker-process-contract-plan.md`

同时修正 PCWEB-097 readiness endpoint：

- `next_pcweb_id=PCWEB-098`
- `desktop_asr_handoff_next_decisions` 包含 `define_desktop_asr_worker_process_contract`

PCWEB-098 合同定义：

- worker lifecycle：`specified_not_started`
- worker process：`not_spawned`
- worker health：`not_checked`
- command transport：`not_bound`
- event output contract：`partial/final/revision/error/end_of_stream`
- command catalog：`worker.prepare`、`worker.start`、`worker.stop`、`worker.health`、`worker.collect_events`、`worker.cleanup`
- approved event root：`artifacts/tmp/asr_events`
- approved runtime root：`artifacts/tmp/desktop_asr_worker_runtime`
- handoff endpoint：`/live/asr/local-event-files/sessions`

安全边界：

- 不启动 worker。
- 不运行 subprocess。
- 不访问麦克风或系统音频。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不写 runtime audio。
- 不写或读 event file。
- 不 mutate Web session。
- 不运行 Tauri/Cargo。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_process_contract.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 9 failed。缺 policy/tool，readiness 仍返回 PCWEB-097。
- 绿灯：
  - 同一 focused command。
  - Result: 10 passed, 2 warnings。后续补充 `main(argv=None)` CLI 回归，确认真实命令行参数不会被忽略。

复审条件：

下一步如果要真正启动 worker、运行 Tauri/Cargo、访问麦克风、读取真实音频、下载模型或调用远程 ASR/LLM，必须另起决策和测试，不得把 PCWEB-098 解释成已经授权真实执行。

## DEC-121：同步完整计划状态，PCWEB-098 已完成，下一步锁定 PCWEB-099

日期：2026-07-03

状态：Accepted

背景：

用户再次确认“完整计划写下来了吗”，并要求转写验证先由我通过网上公开授权音频和模拟完成，最终再由用户做真实麦克风会议验证。文档中已经有完整计划和责任边界，但 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 与 `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md` 仍残留 PCWEB-098 是“下一步”的旧状态。DEC-120 已经接受 PCWEB-098 完成，因此必须同步计划入口，避免后续执行回到已完成工作。

决策：

- 完整计划维持不变：公开授权音频来源审查和 no-download sample manifest、合成中文技术会议模拟、本地 ASR event 自测、EvidenceSpan/gap/card pipeline、desktop runtime/worker/mic adapter、用户最终真实麦克风 shadow test。
- PCWEB-098 状态更新为已完成。
- 当前下一张桌面主线票锁定为 PCWEB-099：desktop ASR worker command protocol / lifecycle envelope。
- PCWEB-099 只定义 `worker.prepare/start/stop/health/collect_events/cleanup` 的 request/response envelope、lifecycle transition preview、blocked reasons、allowed roots 和 safety flags。
- 公开音频仍只允许 AliMeeting Eval 或 AISHELL-4 test 的 3-5 个 planned samples no-download manifest；没有具体 manifest 就保持 `blocked_no_planned_samples`。
- FunASR 仍需要已验证本地模型目录或明确模型下载审批；不静默下载 ModelScope 模型。

安全边界：

- 不下载公开音频大包。
- 不抽取或转码公开音频。
- 不访问麦克风。
- 不读取真实用户录音。
- 不读取 `configs/local`、`data/asr_eval/local_samples` 或 `data/local_runtime`。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不运行 Cargo/Tauri。

验证方式：

- 文档一致性检查：搜索 P0 文档、README、current mainline 和 RTM 中的 PCWEB-098/PCWEB-099 指针。
- 公开来源复核继续引用官方或原始网页：OpenSLR SLR119、OpenSLR SLR111 和 MagicHub Web Meeting 页面。

复审条件：

如果后续批准真实下载公开音频、运行 FunASR 模型下载、运行 Tauri/Cargo、访问麦克风或启用远程 ASR/LLM，必须新增独立决策和测试，不得从本决策推导执行授权。

## DEC-122：实现 PCWEB-099 desktop ASR worker command protocol

日期：2026-07-03

状态：Accepted

背景：

DEC-121 已把 PCWEB-098 完成后的下一张桌面主线票锁定为 PCWEB-099。PCWEB-098 只定义 worker process contract，仍缺少 future desktop sidecar 的 command request/response envelope 和 lifecycle transition preview。没有该协议层，后续实现 worker、Tauri IPC 或 mic adapter 时容易把“命令形状验证”与“真实执行”混在一起。

决策：

新增 PCWEB-099：

- `code/desktop_tauri/asr-worker-command-protocol.policy.json`
- `tools/desktop_asr_worker_command_protocol.py`
- `tests/test_desktop_asr_worker_command_protocol.py`
- `docs/pcweb-099-desktop-asr-worker-command-protocol-plan.md`

同时修正 PCWEB-097 readiness endpoint：

- `next_pcweb_id=PCWEB-099`
- `desktop_asr_handoff_next_decisions` 包含 `define_desktop_asr_worker_command_protocol`

PCWEB-099 command protocol 定义：

- protocol mode：`command_envelope_contract_only`
- protocol version：`desktop_asr_worker_command_protocol.v1`
- worker command execution status：`not_executed`
- transition preview status：`specified_not_executed`
- command catalog：`worker.prepare`、`worker.start`、`worker.stop`、`worker.health`、`worker.collect_events`、`worker.cleanup`
- lifecycle preview：
  - `not_prepared -> prepared`
  - `prepared -> running`
  - `running -> stopped`
  - health / collect_events 为 unchanged preview
  - `stopped -> cleaned`
- approved event root：`artifacts/tmp/asr_events`
- approved runtime root：`artifacts/tmp/desktop_asr_worker_runtime`

合法 command request 只会返回 response preview：

- `accepted=false`
- `status=validated_not_executed`
- `worker_lifecycle_status=unchanged_not_executed`

安全边界：

- 不接受或执行 command。
- 不启动 worker。
- 不运行 subprocess。
- 不访问麦克风或系统音频。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不写 runtime audio。
- 不写或读 event file。
- 不 mutate Web session。
- 不运行 Tauri/Cargo。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_command_protocol.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 10 failed。缺 policy/tool，readiness 仍返回 PCWEB-098。
- 绿灯：
  - 同一 focused command。
  - Result: 10 passed, 2 warnings。

复审条件：

下一步如果要真正启动 worker、运行 Tauri/Cargo、访问麦克风、读取或写入 event file、读取真实音频、下载模型或调用远程 ASR/LLM，必须另起决策和测试，不得把 PCWEB-099 解释成已经授权真实执行。

## DEC-123：实现 PCWEB-100 desktop ASR worker synthetic lifecycle harness

日期：2026-07-03

状态：Accepted

背景：

PCWEB-099 已定义 command request/response envelope，但仍停留在静态协议校验。为了继续向真实 worker 主线推进，同时不越过麦克风、真实音频、模型下载和 Tauri/Cargo 边界，需要一个 bounded synthetic lifecycle harness：只在测试里应用 command sequence，并在 `collect_events` 阶段复用 PCWEB-096 的临时 Web handoff。

决策：

新增 PCWEB-100：

- `code/desktop_tauri/asr-worker-synthetic-lifecycle.policy.json`
- `tools/desktop_asr_worker_synthetic_lifecycle.py`
- `tests/test_desktop_asr_worker_synthetic_lifecycle.py`
- `docs/pcweb-100-desktop-asr-worker-synthetic-lifecycle-plan.md`

PCWEB-100 lifecycle 固定为：

```text
worker.prepare
  -> worker.start
  -> worker.collect_events
  -> worker.stop
  -> worker.cleanup
```

`collect_events` 阶段只允许 PCWEB-096 `synthetic_local_test`：

- 读取 approved synthetic event file：`artifacts/tmp/asr_events`
- 写入临时 Web data dir：`artifacts/tmp/desktop_handoff_dry_run`
- 返回 Web handoff summary：transcript final count、suggestion card count、LLM statuses

安全边界：

- 不启动真实 worker。
- 不访问麦克风或系统音频。
- 不请求音频权限。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不写 runtime audio。
- 不写 event file。
- 不 mutate production Web session。
- 不运行 Tauri/Cargo。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_synthetic_lifecycle.py -q -p no:cacheprovider`
  - Result: 8 failed。缺 policy/tool。
- 绿灯：
  - 同一 focused command。
  - Result: 8 passed, 2 warnings。

复审条件：

下一步如果要从 synthetic lifecycle harness 进入真实 worker implementation、Tauri IPC、真实麦克风 adapter、FunASR 模型运行、公开音频下载或远程 ASR/LLM，必须另起决策和测试，不得把 PCWEB-100 解释成真实 worker 已完成。

## DEC-124：公开音频二次检索和计划入口去歧义

日期：2026-07-03

状态：Accepted

背景：

用户要求确认完整计划是否已经写下，并强调转写类验证需要由我先联网寻找合法公开音频和模拟完成，最终真实麦克风会议由用户验证。多 Agent 只读审查结论是：计划已经覆盖主线和边界，但少数状态指针、公开音频候选和入口说明需要同步，避免后续 agent 被旧 PCWEB-098 指针或小体量公开数据候选带偏。

决策：

公开音频 registry 和文档同步为四类：

- 自动白名单仍只包含 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111、AISHELL-1 / OpenSLR SLR33。
- THCHS-30 / OpenSLR SLR18 继续是低优先级观察，不进入当前自动工具白名单。
- MagicHub Web Meeting、MagicData-RAMC / OpenSLR SLR123 和 Mozilla Common Voice zh-CN 只记录为 `observed_but_not_whitelisted`：
  - MagicHub/MagicData 受 CC BY-NC-ND 4.0 约束，不自动下载、抽样、转码或进入产品价值 gate。
  - Common Voice zh-CN 虽为 CC0 baseline 候选，但它是短句朗读、非会议且包体大，不自动下载，也不能证明会议实时 Copilot 价值。
- WenetSpeech / OpenSLR SLR121 明确排除，因为其音频依赖 YouTube/podcast 等平台来源，和本项目不抓平台音频、播客或版权链不清录音的边界冲突。

同时同步入口文档：

- `README.md` 增加当前公开音频二次检索结论，并说明 PCWEB-098/099/100 段落是历史增量，当前下一步以 `docs/current-mainline-index.md` 为准。
- `docs/current-mainline-index.md` 增加 observed/excluded 来源口径和每个下一步仍需 SDD/TDD 的执行钩子。
- `docs/project-current-status-and-forward-plan-2026-07-03.md` 更新 Phase 3：PCWEB-098/099/100 已完成，下一步只在明确审批边界下选择 ASR worker implementation design、Tauri no-op run、公开音频 planned samples 或 mic adapter contract。
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md` 和 `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md` 同步二次检索结果。
- `docs/requirements-traceability-matrix.md` 增加最近更新日期，并同步 DRV-002/DRV-029 的当前状态。

TDD / 验证：

- 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py -q -p no:cacheprovider`
  - Result: 2 failed, 4 passed, 1 warning。失败原因为 registry 只记录 MagicHub Web Meeting，缺 MagicData-RAMC、Common Voice zh-CN 和 WenetSpeech 排除项。
- 绿灯：
  - 同一 focused command。
  - Result: 6 passed, 1 warning。

安全边界：

- 不下载公开音频。
- 不抽样、不解压、不转码。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不访问麦克风。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载 FunASR/ModelScope 模型。
- 不运行 Cargo/Tauri。

下一步：

继续按主线推进，而不是继续泛化评测。默认候选为：ASR worker implementation design、公开音频 planned samples 的 no-download manifest、真实 Tauri no-op run 审批边界或 mic adapter contract。真实麦克风会议仍由用户最终启动和验证。

## DEC-125：实现 PCWEB-101 desktop ASR worker implementation approval packet

日期：2026-07-03

状态：Accepted

背景：

PCWEB-100 已经把 synthetic command lifecycle 跑通到临时 Web handoff，但仍没有真实 worker implementation 的审批边界。如果直接进入 worker 代码，很容易越过麦克风、真实音频、event file 写入、模型下载、Cargo/Tauri 或远程 provider 边界。因此下一步先把 implementation approval packet 做成可测试合同。

决策：

新增 PCWEB-101：

- `code/desktop_tauri/asr-worker-implementation-approval.policy.json`
- `tools/desktop_asr_worker_implementation_approval.py`
- `tests/test_desktop_asr_worker_implementation_approval.py`
- `docs/pcweb-101-desktop-asr-worker-implementation-approval-plan.md`

PCWEB-101 定义：

- required previous contracts：`PCWEB-098`、`PCWEB-099`、`PCWEB-100`
- future worker entrypoint：只允许批准代码根下的路径
- future command runner：只允许批准代码根下的路径
- approved event root：`artifacts/tmp/asr_events`
- approved runtime root：`artifacts/tmp/desktop_asr_worker_runtime`
- 当前可 preview provider：`mock_streaming`、`sherpa_onnx_streaming`
- 后续需审批 provider：`funasr_streaming`
- forbidden provider：`remote_asr`、`remote_llm_asr`
- 当前可 preview source：`synthetic`
- 后续需审批 source：`mic`、`file`、`system_audio`
- required approval tokens：`pcweb_101_worker_implementation_design_reviewed`、`no_mic_no_real_audio_ack`、`no_remote_asr_no_llm_ack`、`no_model_download_ack`、`no_tauri_cargo_run_ack`

合法 approval packet 只会返回：

- `implementation_approval_status=ready_for_manual_review_not_executable`
- `implementation_packet_status=preview_ready`
- `approved_to_implement_now=false`
- `approved_to_execute_now=false`

Web readiness endpoint 同步为：

- `next_pcweb_id=PCWEB-101`
- `desktop_asr_handoff_next_decisions` 包含 `define_desktop_asr_worker_implementation_approval_packet`

安全边界：

- 不实现真实 worker。
- 不启动真实 worker。
- 不访问麦克风或系统音频。
- 不请求音频权限。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或运行 ModelScope。
- 不写 runtime audio。
- 不读写 event file。
- 不 mutate Web session。
- 不运行 Tauri/Cargo。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_implementation_approval.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 10 failed, 2 warnings。缺 policy/tool，readiness 仍返回 PCWEB-099。
- 绿灯：
  - 同一 focused command。
  - Result: 10 passed, 2 warnings。

复审条件：

下一步如果要创建 worker skeleton、绑定 Tauri IPC、启动 worker、访问麦克风、读写真实音频、下载模型、调用远程 ASR/LLM 或运行 Cargo/Tauri，必须另起决策和 TDD，不得把 PCWEB-101 解释成已经批准真实 implementation 或 execution。

## DEC-126：再次确认公网音频模拟转写和真实麦克风验收分工

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：“完整计划写下来了吗，转写之类的需要你去网上寻找音频和模拟，我最终再来真实麦克风会议验证”。这要求把当前计划从“是否还要继续评测”收束为可执行分工：我先完成公网公开音频来源复核、合成音频/模拟转写、本地 ASR event 自测和指标报告；用户最后在真实麦克风会议中验证产品价值。

决策：

- 完整计划已经写下，权威入口仍是 `docs/current-mainline-index.md` 和 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`。
- 不再新增散点计划文档；后续只更新主入口、RTM 和 decision-log。
- 公网音频继续只走官方来源复核和 no-download sample manifest：
  - AliMeeting / OpenSLR SLR119：会议声学主候选。
  - AISHELL-4 / OpenSLR SLR111：多人会议、远场、多通道和重叠补充。
  - AISHELL-1 / OpenSLR SLR33：普通话 ASR/runtime sanity check，不证明会议价值。
- MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN：observed-but-not-whitelisted。
- WenetSpeech：因 YouTube/podcast 平台音频来源明确排除。
- 没有 3-5 个可复核 `planned_samples` 时，公开音频阶段保持 `blocked_no_planned_samples`，不下载、不抽样、不转码。
- `data/asr_eval/public_sample_plan.example.json` 使用 AISHELL-4 只是 schema example，执行优先级仍是 AliMeeting Eval 第一、AISHELL-4 test 第二。
- `expected_sha256_after_extract` 暂不改工具协议；它作为人工复核 manifest 字段保留。若未来批准真实抽取，必须在 post-extraction run report 记录 observed clip sha256 后才允许进入 ASR。
- 合成音频和 mock streaming 继续承担产品逻辑、event contract、EvidenceSpan、gap candidate 和非工程 0 candidate 回归。
- 真实麦克风会议只在 desktop runtime、ASR worker handoff、mic adapter start/pause/resume/stop/delete 和导出反馈链路具备后，由用户显式启动。

安全边界：

- 不下载公开音频大包。
- 不抽样、不解压、不转码公开音频。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不访问麦克风。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载 FunASR/ModelScope 模型。
- 不运行 Cargo/Tauri。

验证方式：

- 文档同步：
  - `docs/current-mainline-index.md`
  - `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`
  - `docs/requirements-traceability-matrix.md`
  - `docs/decision-log.md`
- Focused public-audio guard：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py -q -p no:cacheprovider`

下一步：

执行优先级不变：PCWEB-102 no-execution worker skeleton、公开音频 planned sample manifest 条件审查、FunASR 本地模型目录/DRV-019 或 Tauri/mic adapter 边界择一推进；FunASR 只有在用户提供本地模型目录或明确批准 DRV-019 后再跑一次 synthetic smoke；真实麦克风会议仍由用户最终验证。2026-07-03 后续已通过 DEC-127 完成 PCWEB-102。

## DEC-127：实现 PCWEB-102 desktop ASR worker no-execution skeleton

日期：2026-07-03

状态：Accepted

背景：

PCWEB-101 已经定义了 future worker implementation approval packet，但还没有真实 worker 代码落点。用户要求按已收敛计划继续实现和自测，且不能继续停留在评测循环。下一步因此选择 PCWEB-102：建立可导入 sidecar module boundary，但仍不允许执行 worker、访问音频、读写事件文件、下载模型或运行 Tauri/Cargo。

决策：

新增 PCWEB-102：

- `code/asr_runtime/scripts/asr_worker_sidecar.py`
- `code/desktop_tauri/asr-worker-no-execution-skeleton.policy.json`
- `tools/desktop_asr_worker_no_execution_skeleton.py`
- `tests/test_desktop_asr_worker_no_execution_skeleton.py`
- `docs/pcweb-102-desktop-asr-worker-no-execution-skeleton-plan.md`

PCWEB-102 定义：

- required previous contracts：`PCWEB-101`
- skeleton mode：`module_boundary_only`
- execution mode：`no_execution`
- worker skeleton status：`specified_not_executable`
- sidecar module path：`code/asr_runtime/scripts/asr_worker_sidecar.py`
- approved event output root：`artifacts/tmp/asr_events`
- approved runtime root：`artifacts/tmp/desktop_asr_worker_runtime`
- provider preview allowed：`mock_streaming`、`sherpa_onnx_streaming`
- provider requiring later approval：`funasr_streaming`
- provider forbidden：`remote_asr`、`remote_llm_asr`
- source preview allowed：`synthetic`
- source requiring later approval：`mic`、`file`、`system_audio`
- module boundaries：worker identity、command envelope intake、lifecycle state、event writer、provider adapter、health/status、cleanup plan

Web readiness endpoint 同步为：

- `next_pcweb_id=PCWEB-102`
- next decision 包含 `define_desktop_asr_worker_no_execution_skeleton`

安全边界：

- 不启动进程。
- 不启动 worker。
- 不访问麦克风或系统音频。
- 不请求音频权限。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不导入或执行 provider/model。
- 不调用远程 ASR/LLM。
- 不下载模型或运行 ModelScope。
- 不写 runtime audio。
- 不读写 event file。
- 不 mutate Web session。
- 不运行 Tauri/Cargo。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_no_execution_skeleton.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 10 failed, 2 warnings。缺 policy/tool/sidecar，readiness 仍返回 PCWEB-101。
- 绿灯：
  - 同一 focused command。
  - Result: 10 passed, 2 warnings。

复审条件：

下一步如果要把 skeleton 绑定到 desktop command runner、运行 Tauri no-op、启动真实 worker、访问麦克风、读写真实音频、下载模型、调用远程 ASR/LLM 或运行 Cargo/Tauri，必须另起决策和 TDD，不得把 PCWEB-102 解释成真实 worker implementation 或 execution 已获批。

## DEC-128：实现 PCWEB-103 desktop ASR worker command runner binding preview

日期：2026-07-03

状态：Accepted

背景：

PCWEB-102 已经建立可导入 sidecar module boundary，但还没有定义 future desktop command runner 如何与 sidecar 对接的静态边界。用户要求继续推进 PC 端主线，同时避免继续停留在泛评估。下一步因此选择 PCWEB-103：建立 command runner binding preview，但不实现、不绑定、不执行 runner。

决策：

新增 PCWEB-103：

- `code/desktop_tauri/asr-worker-command-runner-binding.policy.json`
- `tools/desktop_asr_worker_command_runner_binding.py`
- `tests/test_desktop_asr_worker_command_runner_binding.py`
- `docs/pcweb-103-desktop-asr-worker-command-runner-binding-plan.md`

PCWEB-103 定义：

- required previous contracts：`PCWEB-102`
- binding mode：`command_runner_binding_preview_only`
- execution mode：`no_execution`
- sidecar module path：`code/asr_runtime/scripts/asr_worker_sidecar.py`
- future native command runner path：`code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- command transport preview：`stdio_jsonl`
- command catalog：`worker.prepare`、`worker.start`、`worker.health`、`worker.collect_events`、`worker.stop`、`worker.cleanup`
- approved event output root：`artifacts/tmp/asr_events`
- approved runtime root：`artifacts/tmp/desktop_asr_worker_runtime`
- provider preview allowed：`mock_streaming`、`sherpa_onnx_streaming`
- provider requiring later approval：`funasr_streaming`
- provider forbidden：`remote_asr`、`remote_llm_asr`
- source preview allowed：`synthetic`
- source requiring later approval：`mic`、`file`、`system_audio`

合法 binding request 也只返回：

- `command_runner_binding_status=ready_for_no_execution_binding_review`
- `future_native_command_preview.binding_status=validated_not_bound`
- `command_dispatch_status=not_dispatched`
- `tauri_ipc_status=not_invoked`
- `process_spawn_status=not_spawned`
- `health_probe_status=not_executed`
- `event_collection_status=not_executed`
- `worker_execution_status=not_executed`

Web readiness endpoint 同步为：

- `next_pcweb_id=PCWEB-103`
- next decision 包含 `define_desktop_asr_worker_command_runner_binding`
- blocker 新增 `command_runner_binding_not_approved`
- 新增 command runner false flags：bind、dispatch、subprocess、Tauri IPC 全部 false

安全边界：

- 不实现 Rust command runner。
- 不绑定 Tauri command。
- 不 invoke Tauri IPC。
- 不 spawn subprocess 或 Python worker。
- 不 dispatch worker command。
- 不执行 health probe。
- 不 collect event file。
- 不启动 worker。
- 不访问麦克风或系统音频。
- 不请求音频权限。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不导入或执行 provider/model。
- 不调用远程 ASR/LLM。
- 不下载模型或运行 ModelScope。
- 不写 runtime audio。
- 不读写 event file。
- 不 mutate Web session。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯 1：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_command_runner_binding.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 9 failed, 2 warnings。缺 policy/tool，readiness 仍返回 PCWEB-102。
- TDD 红灯 2：
  - 同一 focused command。
  - Result: 4 failed, 5 passed, 2 warnings。新增 runner/dispatch/Tauri IPC/subprocess/health/collect events safety fields 尚未实现。
- 绿灯：
  - 同一 focused command。
  - Result: 12 passed, 2 warnings。后续按多 Agent 复审补充 `main(argv=None)` CLI 参数读取、no-request validation status、reserved Tauri command preview 字段，以及 `--policy` / `--binding-request` 输入文件读取前 forbidden path guard。

复审条件：

下一步如果要实现 command runner skeleton、绑定真实 Tauri command、invoke IPC、spawn subprocess、dispatch worker command、health probe、collect/read/write event file、运行 Tauri no-op、访问麦克风、读写真实音频、下载模型、调用远程 ASR/LLM 或运行 Cargo/Tauri，必须另起决策和 TDD，不得把 PCWEB-103 解释成真实 runner binding 或 worker execution 已获批。

## DEC-129：实现 public audio / synthetic ASR guard hardening

日期：2026-07-03

状态：Accepted

背景：

用户确认转写验证应由我先通过网上公开授权音频来源审查和模拟完成，最终真实麦克风会议再由用户验证。多 Agent 只读复核后确认主计划已写下，但发现三个会影响后续自测可信度的边界细节：`synthetic_asr_smoke_report` 只校验 relative path 字符串，allowed root 内 symlink 可能指向 `configs/local` 等 forbidden roots；`public_audio_sample_extraction_plan` 在 planned samples file path 校验通过后仍用 caller 当前 cwd 打开原始 path；`copilot_product_value_batch_gate` CLI 默认 mock lane 和 real lane 都指向 sherpa events，容易误导三车道隔离解释。

决策：

- `tools/synthetic_asr_smoke_report.py` 新增 resolved path / symlink guard；在读取 provider、transcript report、events 和 script JSON 前，必须验证 resolved path 仍在 repo 内、仍在对应 allowed root 下，且没有指向 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime` 或 `outputs`。
- `tools/public_audio_sample_extraction_plan.py` 新增 `resolve_input_file_path_after_validation`；planned samples file 通过读前校验后，统一打开 repo-root 解析后的绝对路径，不依赖当前 shell cwd。
- `tools/copilot_product_value_batch_gate.py` 默认 `--mock-events-pattern` 改为 `artifacts/tmp/asr_events/{script_id}.mock.events.json`，默认 `--real-events-pattern` 继续为 sherpa events，确保 mock ASR lane 和 real ASR lane 默认隔离。
- `docs/current-mainline-index.md` 的 batch gate 当前命令同步改为 `.mock.events.json`。
- `docs/requirements-traceability-matrix.md` 新增 `DRV-030`，追踪 public audio / synthetic ASR guard hardening。

安全边界：

- 不下载公开音频。
- 不抽取、不解压、不转码公开音频。
- 不访问麦克风。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或运行 ModelScope。
- 不运行 Cargo/Tauri。
- 不新增任何 process/network/audio 执行入口。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_asr_smoke_report.py::test_synthetic_asr_smoke_report_rejects_allowed_symlink_to_forbidden_root_before_read tests/test_public_audio_sample_extraction_plan.py::test_sample_plan_loads_relative_planned_samples_file_from_repo_root tests/test_copilot_product_value_batch_gate.py::test_batch_gate_cli_defaults_keep_mock_and_real_asr_event_lanes_separate -q -p no:cacheprovider`
  - Result: 3 failed, 1 warning。旧实现会把 symlink report 跑成 completed、relative planned samples file 按 cwd 打开失败、batch 默认 mock events 仍是 sherpa。
- 绿灯：
  - 同一 focused command。
  - Result: 3 passed, 1 warning。

复审条件：

DEC-129 当时尚未完成 PCWEB-104，因此候选后续包含 planned public sample manifest 草案和 command runner implementation skeleton/no-dispatch。2026-07-03 后续已通过 DEC-130 完成 PCWEB-104，因此后续不再把 PCWEB-104 当未来任务；仍不得下载公开音频、访问麦克风、读取真实录音、读取 `configs/local`、调用远程 ASR/LLM、下载模型或运行 Cargo/Tauri，除非另有明确决策和 TDD。

## DEC-130：实现 PCWEB-104 desktop ASR worker command runner implementation skeleton / no-dispatch

日期：2026-07-03

状态：Accepted

背景：

PCWEB-103 已经定义 future desktop command runner binding preview，但还没有实际 Rust source 形状。用户要求停止在泛评估和 readiness 循环里空转，同时保持“不访问麦克风、不运行 worker、不下载模型、不运行 Cargo/Tauri”的安全边界。本决策因此实现 PCWEB-104：创建 inert Rust command runner implementation skeleton，并通过静态 policy/report 工具验证它仍未绑定、未 dispatch、未执行。

决策：

新增 PCWEB-104：

- `code/desktop_tauri/asr-worker-command-runner-implementation-skeleton.policy.json`
- `tools/desktop_asr_worker_command_runner_implementation_skeleton.py`
- `tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py`
- `code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- `docs/pcweb-104-desktop-asr-worker-command-runner-implementation-skeleton-plan.md`

PCWEB-104 定义：

- required previous contracts：`PCWEB-103`
- skeleton mode：`command_runner_implementation_skeleton_only`
- execution mode：`no_dispatch_no_execution`
- skeleton version：`desktop_asr_worker_command_runner_implementation_skeleton.v1`
- runner implementation status：`skeleton_not_bound`
- native command runner status：`skeleton_file_not_bound`
- native command runner path：`code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`
- sidecar module path：`code/asr_runtime/scripts/asr_worker_sidecar.py`
- command transport preview：`stdio_jsonl`
- command catalog：`worker.prepare`、`worker.start`、`worker.health`、`worker.collect_events`、`worker.stop`、`worker.cleanup`
- approved event output root：`artifacts/tmp/asr_events`
- approved runtime root：`artifacts/tmp/desktop_asr_worker_runtime`
- provider preview allowed：`mock_streaming`、`sherpa_onnx_streaming`
- provider requiring later approval：`funasr_streaming`
- provider forbidden：`remote_asr`、`remote_llm_asr`
- source preview allowed：`synthetic`
- source requiring later approval：`mic`、`file`、`system_audio`

Rust skeleton 只定义：

- `COMMAND_CATALOG`
- `AsrWorkerCommandRunnerPreview`
- `BlockedCommandRunnerResponse`
- `command_catalog_preview()`
- `implementation_preview()`
- `preview_blocked_response(command_id: &str)`

合法 skeleton request 也只返回：

- `runner_implementation_status=ready_for_no_dispatch_skeleton_review`
- `ready_for_no_dispatch_skeleton_review=true`
- `future_native_command_runner_skeleton.implementation_status=skeleton_source_validated_not_bound`
- `future_native_command_runner_skeleton.binding_status=not_bound`
- `future_native_command_runner_skeleton.command_dispatch_status=not_dispatched`
- `future_native_command_runner_skeleton.tauri_ipc_status=not_invoked`
- `future_native_command_runner_skeleton.process_spawn_status=not_spawned`
- `future_native_command_runner_skeleton.worker_execution_status=not_executed`
- `blocked_command_preview.accepted=false`
- `blocked_command_preview.safe_to_execute_now=false`

Web readiness endpoint 同步为：

- `next_pcweb_id=PCWEB-104`
- `command_runner_implementation_skeleton_status=not_bound_no_dispatch`
- `desktop_asr_handoff_safe_to_accept_worker_command=false`
- blocker 新增 `command_runner_binding_not_approved` 和 `command_runner_implementation_skeleton_not_approved`
- next decision 包含 `define_desktop_asr_worker_command_runner_implementation_skeleton`

安全边界：

- 不在 `lib.rs` 中 `mod asr_worker_command_runner`。
- 不在 `generate_handler!` 中绑定 command runner。
- 不使用 `#[tauri::command]`。
- 不 accept 或 dispatch worker command。
- 不 invoke Tauri IPC。
- 不 spawn subprocess 或 Python worker。
- 不执行 health probe。
- 不 collect/read/write event file。
- 不启动 worker。
- 不访问麦克风或系统音频。
- 不请求音频权限。
- 不读取真实用户音频。
- 不读取 `configs/local` 或 secret。
- 不导入或执行 provider/model。
- 不调用远程 ASR/LLM。
- 不下载模型或运行 ModelScope。
- 不写 runtime audio。
- 不 mutate Web session。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_command_runner_implementation_skeleton.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 8 failed, 2 warnings。缺 policy/tool/Rust skeleton，readiness 仍返回 PCWEB-103。
- 绿灯：
  - 同一 focused command。
  - Result: 8 passed, 2 warnings。

复审条件：

PCWEB-104 完成后，下一步不再是 command runner implementation skeleton/no-dispatch。后续只能在明确计划和 TDD 下选择以下方向之一：

- public planned samples no-download manifest。
- ASR quality decision，包括 FunASR 本地模型目录或 DRV-019 模型下载审批。
- real Tauri no-op run，且需要显式 Cargo/Tauri 执行审批。
- mic adapter contract，仍不得访问麦克风，直到用户显式 start 语义、runtime ignored root、删除语义和隐私边界全部实现。

不得把 PCWEB-104 解释成真实 command runner binding、worker execution、event file IO、音频采集、模型下载、远程调用或 Cargo/Tauri 执行已获批。

## DEC-131：实现 DRV-031 public audio planned sample manifest decision

日期：2026-07-03

状态：Accepted

背景：

用户要求转写验证由我先通过网上公开音频和模拟完成，最终真实麦克风会议再由用户验证。此前已确认 AliMeeting / OpenSLR SLR119 和 AISHELL-4 / OpenSLR SLR111 是授权链相对清楚的中文会议声学候选，但公开包是 GB 级，并且当前没有 3-5 个可复核 clip 的 `archive_member_path`、`expected_sha256_after_extract` 和人工下载审批。为避免继续泛搜版权不清来源，或用 placeholder 伪造 sample manifest，本决策新增机器可测的 planned sample manifest decision。

决策：

新增 DRV-031：

- `tools/public_audio_planned_sample_manifest_decision.py`
- `tests/test_public_audio_planned_sample_manifest_decision.py`
- `docs/public-audio-planned-sample-manifest-decision-2026-07-03.md`

默认候选顺序固定为：

1. `alimeeting_openslr_slr119`
   - `source_url=https://www.openslr.org/119/`
   - `source_license=CC BY-SA 4.0`
   - `source_split=eval`
   - `archive_name=Eval_Ali.tar.gz`
   - archive size note：约 `3.42G`
2. `aishell4_openslr_slr111`
   - `source_url=https://www.openslr.org/111/`
   - `source_license=CC BY-SA 4.0`
   - `source_split=test`
   - `archive_name=test.tar.gz`
   - archive size note：约 `5.2G`

默认输出：

- `decision_status=blocked_no_verified_public_sample_manifest`
- `public_audio_stage_status=blocked_no_planned_samples`
- `blocked_reasons=[no_verified_archive_member_path,no_expected_clip_sha256_after_extract,no_user_approval_for_gb_archive_download]`
- `planned_sample_count=0`
- `download_command=null`
- `extract_command=null`
- `transcode_command=null`
- `safe_to_download_now=false`
- `safe_to_extract_now=false`
- `safe_to_transcode_now=false`
- `safe_to_call_asr_now=false`

如果未来提供合法 planned samples 文件，工具只允许返回：

- `decision_status=schema_validated_no_download`
- `public_audio_stage_status=ready_for_manual_download_review`

即使 schema valid，也仍不生成下载、解压、转码或 ASR 命令。

安全边界：

- 不下载公开音频。
- 不解压、不抽取、不转码公开音频。
- 不访问麦克风。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或运行 ModelScope。
- 不运行 Cargo/Tauri。
- 不把 MagicHub/MagicData/Common Voice 等 observed-but-not-whitelisted 候选提升为可执行白名单。
- 不使用 placeholder 伪造 `archive_member_path` 或 sha256。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result: 4 failed, 1 warning。缺 `tools/public_audio_planned_sample_manifest_decision.py`。
- 绿灯：
  - 同一 focused command。
  - Result: 4 passed, 1 warning。
- 默认 CLI：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
  - Result: exit 1 with `decision_status=blocked_no_verified_public_sample_manifest` and all download/extract/transcode/ASR flags false。
- AliMeeting sample extraction plan cross-check：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
  - Result: exit 1 with `plan_status=blocked_no_planned_samples` and `download_command=null`。

复审条件：

公开音频阶段当前不再作为下一主线继续扩展。后续只有在用户提供合法 planned samples 文件，或用户明确批准一次 GB 级公开包下载并接受 ignored 存储、checksum、post-extraction observed sha256 和清理策略后，才允许重新进入公开音频小样本执行。否则下一主线应转向 ASR quality decision / FunASR 本地模型目录或 DRV-019 审批、真实 Tauri no-op run、或 mic adapter contract。

## DEC-132：实现 DRV-032 ASR quality decision gate

日期：2026-07-03

状态：Accepted

背景：

用户要求按计划继续实现和自测，同时反复强调不要陷入无限评测，必须回到产品主线。此前 DRV-026/027 已证明 perfect/mock lane 5/5 ready、非工程 control candidate=0，但 real sherpa ASR 仍被中文技术实体质量阻塞；DRV-031 已把公开音频阶段收束为 no-download blocked；DRV-019 已生成 FunASR 模型下载审批包但未批准下载。为避免后续继续泛化 provider 横评，需要一个机器可测 gate 明确当前 ASR 质量下一步。

决策：

新增 DRV-032：

- `tools/asr_quality_decision_gate.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/asr-quality-decision-gate-2026-07-03.md`

DRV-032 只组合已有 gate/report：

- `tools/copilot_product_value_batch_gate.py`
- `tools/funasr_synthetic_smoke_readiness.py`
- `tools/funasr_model_download_approval_packet.py`
- `tools/public_audio_planned_sample_manifest_decision.py`

默认 CLI：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py
```

当前输出：

- exit code：`1`
- `decision_status=requires_funasr_model_dir_or_drv019_approval`
- `product_value_batch_overall_decision=blocked_by_asr_quality`
- `perfect_lane_ready_count=5`
- `mock_lane_ready_count=5`
- `real_asr_blocked_count=4`
- `non_engineering_candidate_count=0`
- `funasr_readiness_status=blocked`
- `funasr_required_cached_models_status=missing`
- `funasr_approval_packet_status=generated_for_manual_review`
- `public_audio_decision_status=blocked_no_verified_public_sample_manifest`

支持的决策状态：

- `fix_product_logic_first`
- `fix_stream_contract_first`
- `requires_funasr_model_dir_or_drv019_approval`
- `funasr_cache_preflight_ready_requires_execution_approval`
- `asr_quality_current_gate_not_blocking`

安全边界：

- 不运行 ASR。
- 不下载 FunASR/ModelScope 模型。
- 不下载公开音频。
- 不解压、不抽取、不转码公开音频。
- 不访问麦克风。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不运行 Cargo/Tauri。
- 不把 public audio schema valid 误写成 download-ready；`schema_validated_no_download` 仍是 `manual_evidence_pending_no_download`。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result: 5 failed, 1 warning。缺 `tools/asr_quality_decision_gate.py`。
- 绿灯：
  - 同一 focused command。
  - Result: 5 passed, 1 warning。

复审条件：

DRV-032 完成后，下一步不再继续宽泛 ASR/provider 横评。只有以下路径允许继续：

- 用户提供已验证 FunASR 本地模型目录，然后申请一次 synthetic smoke 并复跑 batch gate。
- 用户明确批准 DRV-019 manual-user-run-only 模型下载审批包，然后按 post-download verification order 执行。
- 在 ASR 质量仍 blocked 时，继续推进 desktop no-op runtime / IPC / mic adapter contract，但不得宣称真实实时建议链路质量已达标。

## DEC-133：实现 PCWEB-105 desktop microphone adapter contract

日期：2026-07-03

状态：Accepted

背景：

DRV-032 已把 ASR 质量结论收束为 `requires_funasr_model_dir_or_drv019_approval`，但用户要求继续按计划推进主线，不能卡在评测循环。真实麦克风会议仍必须由用户最终显式启动，因此下一步应先定义麦克风 adapter 合同、start/pause/resume/stop/delete 语义、ignored runtime audio root 和删除边界，而不是直接访问麦克风。

决策：

新增 PCWEB-105：

- `code/desktop_tauri/mic-adapter-contract.policy.json`
- `tools/desktop_mic_adapter_contract.py`
- `tests/test_desktop_mic_adapter_contract.py`
- `docs/pcweb-105-desktop-mic-adapter-contract-plan.md`

更新：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `README.md`
- `code/desktop_tauri/README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

PCWEB-105 定义：

- `contract_version=desktop_mic_adapter_contract.v1`
- `adapter_execution_status=not_bound_not_executed`
- `permission_request_status=not_requested`
- `user_start_boundary=explicit_user_start_required_before_capture`
- `approved_runtime_audio_root=artifacts/tmp/desktop_mic_adapter_runtime`
- `approved_audio_chunk_root=artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`
- `delete_semantics=delete_audio_chunks_before_session_discard`

命令目录：

- `mic_adapter.prepare`
- `mic_adapter.status`
- `mic_adapter.start`
- `mic_adapter.pause`
- `mic_adapter.resume`
- `mic_adapter.stop`
- `mic_adapter.delete_audio_chunks`

Web readiness endpoint 同步为：

- `next_pcweb_id=PCWEB-105`
- `mic_adapter_contract_status=not_defined`
- blocker 新增 `mic_adapter_contract_not_approved`
- next decision 新增 `define_desktop_mic_adapter_contract`
- `desktop_asr_handoff_safe_to_request_audio_permission=false`

安全边界：

- 不绑定 mic adapter。
- 不接受或执行真实 mic command。
- 不枚举 input device。
- 不访问麦克风。
- 不请求系统音频权限。
- 不写 audio chunk。
- 不读 audio chunk。
- 不删除真实 audio chunk。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不 mutate Web session。
- 不运行 Cargo/Tauri。
- `file` 和 `system_audio` source kind 必须另起审批，当前 blocked。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_mic_adapter_contract.py code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary -q -p no:cacheprovider`
  - Result: 8 failed, 2 warnings。缺 policy/tool，且 Web readiness 仍返回 `next_pcweb_id=PCWEB-104`。
- 绿灯：
  - 同一 focused command。
  - Result: 8 passed, 2 warnings。
- 默认 CLI：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_mic_adapter_contract.py`
  - Result: exit 0 with `mic_adapter_contract_status=specified_not_executable`、`permission_request_status=not_requested`、`audio_capture_status=not_started`、`audio_chunk_write_status=not_written`、`audio_chunk_delete_status=not_executed`。

复审条件：

PCWEB-105 不授权真实麦克风会议。后续若要进入真实采集，必须另起决策和 TDD，至少覆盖 Tauri/no-op IPC 运行、adapter UI、用户显式 start、pause/resume/stop/delete、本地 ignored audio root、ASR worker handoff、导出和反馈闭环。否则只能继续 desktop no-op runtime / IPC 或 FunASR 本地模型审批路径。

## DEC-134：实现 PCWEB-106 desktop mic adapter contract readiness UI/API

日期：2026-07-03

状态：Accepted

背景：

PCWEB-105 已经把麦克风 adapter 合同、start/pause/resume/stop/delete 语义、显式用户 start 边界、ignored runtime audio root、audio chunk root 和删除语义固定为静态 report。但 Web 工作台仍只能通过 ASR handoff readiness 看到旧的 `mic_adapter_contract_status=not_defined` 口径，容易让后续开发误以为合同尚未定义，或者继续在 report-only 评估中打转。

决策：

新增 PCWEB-106：

- `GET /desktop/mic-adapter-contract-readiness`
- `desktop-mic-adapter-contract-panel`
- `docs/pcweb-106-desktop-mic-adapter-contract-readiness-ui-plan.md`

更新：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `README.md`
- `code/desktop_tauri/README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

PCWEB-106 endpoint 复用 PCWEB-105 静态 contract report，并返回：

- `pcweb_id=PCWEB-106`
- `source_pcweb_id=PCWEB-105`
- `readiness_mode=readiness_only_no_mic_permission`
- `mic_adapter_ui_status=ready_noop_contract_visible`
- `mic_adapter_contract_status=specified_not_executable`
- `contract_version=desktop_mic_adapter_contract.v1`
- `adapter_execution_status=not_bound_not_executed`
- `permission_request_status=not_requested`
- `audio_capture_status=not_started`
- `audio_chunk_write_status=not_written`
- `audio_chunk_delete_status=not_executed`
- `approved_runtime_audio_root=artifacts/tmp/desktop_mic_adapter_runtime`
- `approved_audio_chunk_root=artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`
- `delete_semantics=delete_audio_chunks_before_session_discard`
- 7 个 mic adapter command catalog
- all-false safety flags

ASR handoff readiness endpoint 同步更新：

- `next_pcweb_id=PCWEB-106`
- `mic_adapter_contract_status=specified_not_executable`
- blocker 改为 `mic_adapter_not_bound_to_desktop_runtime`
- next decision 改为 `surface_mic_adapter_contract_readiness_ui`

安全边界：

- 不绑定 native mic adapter。
- 不接受或执行真实 mic command。
- 不请求麦克风权限。
- 不枚举 input device。
- 不访问麦克风。
- 不写 audio chunk。
- 不读 audio chunk。
- 不删除真实 audio chunk。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不 mutate Web session。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_reports_contract_without_audio_access code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_does_not_probe_audio_or_read_secrets code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_with_data_dir_does_not_create_local_storage code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: 6 failed, 2 warnings。缺新 endpoint/panel/loader，且 ASR handoff readiness 仍指向 PCWEB-105。
- 绿灯：
  - 同一 focused command。
  - Result: 6 passed, 2 warnings。

审查跟进：

- 只读审查指出初版 UI 只渲染了 PCWEB-105 safety flags 的子集，而 endpoint 已返回完整 `FALSE_SAFETY_FLAGS`。
- 已补充 `desktop-mic-adapter-contract-panel`，展示完整 21 个 all-false safety flags。
- 已补充 `test_workbench_static_assets_are_served`，逐项检查完整 flag 名称存在于 `app.js`。
- 已补充 `browser_smoke.mjs`，逐项检查浏览器中渲染出的完整 `flag=false`。
- 追加 TDD 红灯：`test_workbench_static_assets_are_served` 失败于缺少 `safe_to_accept_mic_command_now`。
- 追加绿灯：`test_workbench_static_assets_are_served` 1 passed, 2 warnings；`node e2e/browser_smoke.mjs` status ok。

复审条件：

PCWEB-106 不授权真实麦克风会议。下一步只能在明确审批边界下推进真实 Tauri no-op run、mic adapter no-op IPC binding、worker/mic 连接设计、FunASR synthetic smoke 审批路径或公开音频 planned sample manifest。真实麦克风 shadow test 仍必须等 desktop runtime、adapter start/pause/resume/stop/delete、本地 ignored audio root、ASR worker handoff、导出和反馈链路齐备后，由用户显式启动。

## DEC-135：实现 PCWEB-107 desktop mic adapter no-op Tauri IPC binding

日期：2026-07-03

状态：Accepted

背景：

PCWEB-105 已定义麦克风 adapter 合同，PCWEB-106 已把合同展示到 Web/Tauri no-op readiness UI/API。但 Tauri scaffold 的 native no-op command catalog 仍停在 PCWEB-082 的 3 个 runtime command，未来 UI 调用 mic adapter no-op IPC 时容易出现 command name、bridge id 或 safety envelope 漂移。

决策：

新增 PCWEB-107：

- `docs/pcweb-107-desktop-mic-adapter-noop-tauri-ipc-binding-plan.md`

更新：

- `code/desktop_tauri/src-tauri/src/lib.rs`
- `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`
- `tools/desktop_tauri_noop_shell_run_smoke.py`
- `tests/test_desktop_tauri_scaffold.py`
- `tests/test_desktop_tauri_noop_shell_run_smoke.py`
- `README.md`
- `code/desktop_tauri/README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

PCWEB-107 把 Tauri no-op command catalog 扩展为 10 个：

- `runtime_get_status -> runtime.get_status`
- `session_prepare -> session.prepare`
- `asr_worker_health -> asr_worker.health`
- `mic_adapter_prepare -> mic_adapter.prepare`
- `mic_adapter_status -> mic_adapter.status`
- `mic_adapter_start -> mic_adapter.start`
- `mic_adapter_pause -> mic_adapter.pause`
- `mic_adapter_resume -> mic_adapter.resume`
- `mic_adapter_stop -> mic_adapter.stop`
- `mic_adapter_delete_audio_chunks -> mic_adapter.delete_audio_chunks`

`NoopBridgeResponse` 继续保持：

- `safe_to_invoke_noop=true`
- `safe_to_execute_real_action=false`
- `captures_audio=false`
- `spawns_process=false`
- `calls_remote_provider=false`
- `writes_local_files=false`

安全边界：

- 不运行 Cargo/Tauri。
- 不请求麦克风权限。
- 不枚举输入设备。
- 不访问麦克风。
- 不采集音频。
- 不写 audio chunk。
- 不删除真实 audio chunk。
- 不启动 ASR worker。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不 mutate Web session。

验证方式：

- 初始 TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_desktop_tauri_noop_shell_run_smoke.py -q -p no:cacheprovider`
  - Result: `5 failed, 15 passed, 1 warning`。缺 7 个 mic adapter no-op command，policy/tool 仍停在 3 command catalog。
- 中间红灯：
  - 同一 focused command。
  - Result: `10 failed, 10 passed, 1 warning`。Rust scaffold/policy 已更新，但 static smoke tool 仍按旧 PCWEB-082 catalog 校验。
- 绿灯：
  - 同一 focused command。
  - Result: `20 passed, 1 warning`。

复审条件：

PCWEB-107 只证明 mic adapter no-op IPC 静态绑定和 smoke drift guard，不授权真实麦克风会议。下一步不能继续新增横向 readiness/report-only 作为主线；只能收敛到 ASR 质量决策、真实 Tauri no-op run、worker handoff 闭环、mic adapter no-op UI invocation、短时本地模拟输入或真实麦克风 shadow-test report schema。

## DEC-136：锁定公开音频/合成音频/真实麦克风的 6 个后续里程碑

日期：2026-07-03

状态：Accepted

背景：

用户要求确认完整计划是否已经写下，并强调转写验证由我先通过网上公开音频和模拟完成，真实麦克风会议最终由用户验证。两个只读审查 Agent 分别从产品主线和 ASR/公开音频边界做了对抗审查，结论一致：当前不是缺计划，而是需要避免继续用 readiness/report-only、ASR 指标或公开音频 blocked 状态制造“看起来在推进”的工作。

决策：

完整计划继续以以下文档为权威入口：

- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-mainline-index.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`

后续主线压成 6 个里程碑：

1. ASR 质量路径一次性决策：FunASR 本地模型目录、DRV-019 审批、可选远程 ASR 对照或降级取舍必须不再悬空。
2. Real Tauri no-op run：在明确审批下运行 Tauri WebView，并验证 runtime/session/asr_worker/mic adapter no-op IPC。
3. Worker output -> Web Live ASR session：approved synthetic event output 能创建 Web Live ASR session，并生成 EvidenceSpan/state/gap。
4. Mic adapter no-op UI invocation：UI 能调用 7 个 mic adapter no-op IPC，`start` 仍只能 validated-not-executed。
5. Short local simulated input：用合成/模拟输入验证 EvidenceSpan -> gap/state -> candidate/card，非工程 control 保持 0 candidate。
6. Real mic shadow test report schema：先固定真实验收报告结构，再由用户最终执行真实麦克风会议。

公开音频执行锁：

- 公开音频只验证声学、多人、远场、重叠说话、切句和 ASR event contract。
- 公开音频不证明工程建议卡片价值。
- 默认自动白名单仍只保留 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111 和 AISHELL-1 / OpenSLR SLR33。
- MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 只保留 observed-but-not-whitelisted。
- WenetSpeech、Bilibili、YouTube、播客、公开课、直播回放、技术大会录播和版权链不清数据继续 excluded。
- 没有 concrete 3-5 个 clip manifest 时，公开音频必须保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`。
- 不下载 GB 级公开包、不抽取、不转码、不喂 ASR，除非用户明确批准并记录 archive、license、checksum、ignored root 和 cleanup policy。

合成音频执行锁：

- 合成音频用于可重复验证工程语义、技术实体、expected gaps/cards、10-30 秒窗口、非工程负控 0 candidate 和 feedback rubric。
- 合成音频不能替代真实语音体验或真实麦克风会议。
- perfect transcript、mock ASR 和 real ASR 三路必须显式区分，不能把 mock/perfect 成功当作真实 ASR 质量。

真实音频边界：

- 继续禁止读取真实用户录音、访问麦克风、枚举设备、写真实 audio chunk、上传原始音频或调用远程 ASR，直到用户明确进入真实麦克风 shadow test。
- 当前 pre-pilot mic adapter 合同使用 `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks/` 作为 ignored audio chunk root；`docs/asr-zero-cost-and-private-audio-boundary.md` 已同步该口径。未来生产用户数据目录必须另起决策。

治理规则：

任何新 PCWEB/DRV 主线项必须标注它直接推进的链路节点：`ASR quality`、`EvidenceSpan/state/gap`、`candidate/card/feedback`、`desktop runtime`、`worker handoff`、`mic adapter` 或 `pilot`。如果不能直接推进这些节点，只能作为辅助记录，不算主线进展。

复审条件：

下一轮优先考虑 `DRV-033 real mic shadow test report schema`，因为它能把最终真实验收从口头判断变成可审计结构：transcript、ASR metrics、EvidenceSpan/state/candidate/card timeline、feedback labels、privacy/cost flags 和 Go/Pivot/Stop。

## DEC-137：实现 DRV-033 real mic shadow test report schema

日期：2026-07-03

状态：Accepted

背景：

DEC-136 已把后续主线压成 6 个里程碑，其中真实麦克风 shadow test 之前必须先固定报告 schema。否则真实会议可能只留下 transcript 或主观反馈，无法证明产品是否真的在会议中及时发现工程缺口，也无法形成 Go/Pivot/Stop 决策。

决策：

新增 DRV-033：

- `tools/real_mic_shadow_test_report_schema.py`
- `tests/test_real_mic_shadow_test_report_schema.py`
- `docs/drv-033-real-mic-shadow-test-report-schema-plan.md`

Schema 必须覆盖：

- transcript
- ASR metrics
- EvidenceSpan timeline
- state timeline
- candidate/card timeline
- feedback labels
- final Go/Pivot/Stop decision
- privacy/cost flags
- audio retention/delete status
- known limitations

反馈标签固定为：

- `useful`
- `would_have_asked`
- `wrong`
- `too_late`
- `too_intrusive`
- `dismissed`

最终决策固定为：

- `go`
- `pivot`
- `stop`
- `inconclusive_requires_more_shadow_tests`

当前 approved candidate report root：

- `artifacts/tmp/real_mic_shadow_reports`

当前 pre-pilot audio chunk root：

- `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`

安全边界：

- 不访问麦克风。
- 不枚举设备。
- 不请求音频权限。
- 不读取真实用户录音。
- 不写 audio chunk。
- 不删除真实 audio chunk。
- 不读取 `configs/local`。
- 不启动 ASR worker。
- 不调用远程 ASR/LLM。
- 不运行 Cargo/Tauri。
- 不 mutate Web session。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_report_schema.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`。失败原因是 schema 工具不存在。
- 绿灯：
  - 同一 focused command。
  - Result: `7 passed, 1 warning`。

复审加固：

- 只读审查指出初版 schema 仍允许 `evidence_span_timeline=[{}]`、`state_timeline=[{}]`、`candidate_card_timeline=[{}]` 通过，且没有 transcript/evidence/card cross-reference、feedback aggregate、audio retention enum 和 full forbidden-root test。
- 追加红灯：
  - 同一 focused command。
  - Result: `6 failed, 7 passed, 1 warning`。
- 加固内容：
  - EvidenceSpan timeline 必须包含 `evidence_id`、`segment_id`、`start_ms`、`end_ms`、`text`、`supports_candidate_id`。
  - State timeline 必须包含 `state_id`、`state_type`、`at_ms`、`evidence_id`。
  - Candidate/card timeline 必须包含 `candidate_id`、`card_type`、`created_at_ms`、`latency_ms`、`evidence_ids`、`text`。
  - EvidenceSpan `segment_id` 必须引用 transcript segment；state/card 必须引用 EvidenceSpan；EvidenceSpan `supports_candidate_id` 必须引用 candidate/card。
  - feedback aggregate 必须等于 fixed labels 汇总。
  - `go` 决策必须满足 useful/would_have_asked >= 2 且 negative feedback <= 1。
  - audio retention/delete status 改为封闭枚举，拒绝 `not_deleted` 或 `keep_forever`。
  - candidate report path 输出 repo-relative sanitized path。
  - `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 和 `outputs` 都有预读阻断回归测试。
- 加固绿灯：
  - 同一 focused command。
  - Result: `13 passed, 1 warning`。

复审条件：

DRV-033 只固定真实麦克风 shadow-test 报告结构，不代表真实麦克风会议已经开始。真实会议仍必须等 desktop runtime、mic adapter start/pause/resume/stop/delete、本地 ignored audio root、ASR worker handoff、导出和反馈链路具备后，由用户显式启动。

## DEC-138：确认完整计划已落档，旧 next pointer 由 6 个里程碑取代

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：“完整计划写下来了吗，转写之类的需要你去网上寻找音频和模拟，我最终再来真实麦克风会议验证”。为避免继续陷入“再写一份计划/再做一轮宽泛评测”的循环，两个只读审查 Agent 分别复核了主线文档和公开音频边界。

复核结论：

- 完整计划已经写下，权威入口仍是 `docs/current-mainline-index.md` 和 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`。
- 公开音频/公开视频路线已经收口：自动白名单只保留 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111 和 AISHELL-1 / OpenSLR SLR33；MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 只做 observed-but-not-whitelisted；WenetSpeech、Bilibili、YouTube、播客、公开课、技术大会录播和版权链不清来源继续 excluded。
- 公开音频当前只能执行官方来源复核、白名单校验和 no-download planned sample manifest 决策；没有真实 `archive_member_path`、`expected_sha256_after_extract` 和 GB 级公开包下载审批时，必须保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`。
- 合成中文技术会议、mock streaming events 和本地 ASR event 自测由我完成；真实麦克风会议只能在 desktop runtime、ASR worker handoff、mic adapter start/pause/resume/stop/delete、导出和反馈链路具备后，由用户最终显式启动。
- DRV-032 当前 ASR 质量结论仍是 `requires_funasr_model_dir_or_drv019_approval`，不能再通过新增 provider 横评、sherpa 猜测型 normalizer 或 report-only readiness 来绕过。

决策：

旧决策日志中的历史 next pointer 只保留为当时上下文，不再作为当前主线：

- DEC-117 中的 `PCWEB-097` next pointer 已由后续 PCWEB-097 到 PCWEB-107、DEC-136 和 DRV-033 supersede。
- DEC-119/DEC-121 附近的 `PCWEB-098` 或 worker contract next pointer 已由后续 PCWEB-098 到 PCWEB-107、DEC-136 和 DRV-033 supersede。
- 后续执行只以 `docs/current-mainline-index.md` 第 4 节和 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 第 8 节的 6 个里程碑为准：
  1. ASR 质量路径一次性决策。
  2. Real Tauri no-op run。
  3. Worker output -> Web Live ASR session。
  4. Mic adapter no-op UI invocation。
  5. Short local simulated input。
  6. Real mic shadow test report schema / 用户最终真实会议验收。

当前最优下一步：

优先推进真实链路而不是继续扩大评测：在不访问麦克风、不启动 worker、不读取 secret、不调用远程 ASR/LLM 的边界下，下一张主线票应优先选择 Real Tauri no-op run、worker handoff 闭环或 mic adapter no-op UI invocation。公开音频阶段保持 blocked，除非未来提供可信 planned samples manifest 或明确批准公开包下载；ASR 质量阶段保持 DRV-032 闸门，除非提供已验证 FunASR 本地模型目录、明确批准 DRV-019、选择可选远程 ASR 对照或明确降级。

## DEC-139：实现 PCWEB-108 worker output to Web Live ASR session closure gate

日期：2026-07-03

状态：Accepted

背景：

DEC-136/138 将后续主线压成 6 个里程碑，其中 M3 是 `Worker output -> Web Live ASR session`。PCWEB-096/100 已经能用 approved synthetic event file 调用 `/live/asr/local-event-files/sessions` 并写入临时 Web data dir，但原报告主要证明 Web handoff API 接收成功，只汇总 transcript final 和 formal card count。用户反复强调产品不能退化成普通音频转文字工具，因此 M3 必须证明 worker-like event output 进入 Web session 后真的形成 EvidenceSpan、state/gap candidate 和 LLM request draft。

决策：

实现 PCWEB-108：

- 更新 `tools/desktop_asr_worker_handoff_local_dry_run.py`。
- 更新 `tests/test_desktop_asr_worker_handoff_local_dry_run.py`。
- 更新 `tests/test_desktop_asr_worker_synthetic_lifecycle.py`。
- 新增 `docs/pcweb-108-worker-output-web-live-asr-session-closure-plan.md`。
- 同步 `docs/current-mainline-index.md`、`docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/requirements-traceability-matrix.md` 和 `README.md`。

PCWEB-108 的 `web_handoff_response_summary` 必须包含：

- `transcript_final_count`
- `evidence_span_count`
- `state_event_count`
- `scheduler_event_count`
- `suggestion_candidate_count`
- `llm_request_draft_count`
- `suggestion_card_count`
- `all_llm_statuses`
- `worker_to_web_live_session_closure_status`

通过条件：

- 技术会议 synthetic input 进入临时 Web Live ASR session 后，必须返回 `worker_to_web_live_session_closure_status=closed_to_evidence_state_gap`。
- 非工程 input 即使能产生 transcript final 和 EvidenceSpan，只要没有 state/gap candidate，必须返回 `dry_run_status=blocked_by_live_session_closure` 和 `blocked_no_state_or_gap_candidate`。
- PCWEB-100 synthetic lifecycle harness 必须继承同一 closure summary。

安全边界：

- 不启动真实 worker。
- 不访问麦克风。
- 不枚举设备。
- 不请求音频权限。
- 不读取真实用户音频。
- 不读取 `configs/local`。
- 不读取 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不写 runtime audio。
- 不写 event file。
- 不 mutate production Web session。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_calls_web_handoff_api_in_temp_data_dir tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_blocks_when_handoff_does_not_create_evidence_state_gap -q -p no:cacheprovider`
  - Result: `2 failed, 2 warnings`。失败原因是 summary 缺 EvidenceSpan/state/gap closure 字段，非工程 input 仍返回 `synthetic_web_handoff_passed`。
- 初步绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_calls_web_handoff_api_in_temp_data_dir tests/test_desktop_asr_worker_handoff_local_dry_run.py::test_synthetic_local_test_blocks_when_handoff_does_not_create_evidence_state_gap tests/test_desktop_asr_worker_synthetic_lifecycle.py::test_synthetic_lifecycle_runs_command_sequence_and_temp_web_handoff -q -p no:cacheprovider`
  - Result: `3 passed, 2 warnings`。
- 相关门禁：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_handoff_local_dry_run.py tests/test_desktop_asr_worker_synthetic_lifecycle.py tests/test_asr_live_pipeline_replay.py -q -p no:cacheprovider`
  - Result: `24 passed, 2 warnings`。
- 复审加固：
  - 只读代码审查指出初版 closure status 没有把 `scheduler_event_count` 和 `llm_request_draft_count` 纳入 closed 判定。
  - 追加红灯 `test_closure_summary_blocks_when_scheduler_or_llm_request_draft_is_missing`，Result: `1 failed, 1 warning`，缺 scheduler 时仍误判为 `closed_to_evidence_state_gap`。
  - 修正后同一测试 Result: `1 passed, 1 warning`。
  - 修正后相关门禁 Result: `25 passed, 2 warnings`。
- 相邻保护网：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_report_schema.py tests/test_desktop_tauri_noop_shell_run_smoke.py tests/test_desktop_tauri_scaffold.py -q -p no:cacheprovider`
  - Result: `38 passed, 1 warning`。
- 总门禁：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile pc-web`
  - Initial Result: root-pytest `285 passed, 2 warnings`；core `34 passed, 1 warning`；Web backend `316 passed, 2 warnings`；browser smoke `status=ok`；quality gate profile `pc-web passed`。
  - After review hardening Result: root-pytest `286 passed, 2 warnings`；core `34 passed, 1 warning`；Web backend `316 passed, 2 warnings`；browser smoke `status=ok`；quality gate profile `pc-web passed`。

复审条件：

PCWEB-108 完成的是 synthetic temp Web session closure gate，不代表真实 worker、真实 Tauri runtime 或真实麦克风采集已可用。后续若继续 M3，只能把同一 closure summary 接入 UI/报告；若进入 M2/M4/M5，仍需各自决策和 TDD。

## DEC-140：实现 PCWEB-109 mic adapter no-op UI invocation，并收紧计划执行口径

日期：2026-07-03

状态：Accepted

背景：

用户要求确认“完整计划是否已经写下”，并明确转写验证由我继续网上寻找公开音频和模拟，最终由用户真实麦克风会议验证。多 Agent 只读审查确认现有 README、总控计划、主线索引、RTM 和 decision-log 已经形成完整计划，但指出几个执行口径需要更硬：

- README 顶部仍容易让新读者误以为主线还停在 Local Web MVP。
- 公开音频不应再作为主动扩源/下载主线；没有可信 planned samples 或用户批准 GB 级公开包下载时必须保持 blocked。
- `Short local simulated input` 不能被理解为读取本地私有短音频、`.m4a` 或用户录音；只能使用合成生成音频、mock events 或 approved synthetic event file。
- 真实麦克风会议仍必须等 desktop runtime、worker handoff、mic adapter start/pause/resume/stop/delete、导出和反馈链路具备后由用户显式启动。

同时，DEC-136/138 的 6 个里程碑中 M4 是 `Mic adapter no-op UI invocation`。PCWEB-105 已定义合同，PCWEB-106 已展示 readiness，PCWEB-107 已静态绑定 Tauri no-op IPC，但 UI 还没有展示 7 个 mic adapter command 的 invocation status。

决策：

实现 PCWEB-109：

- 更新 `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`。
- 更新 `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`。
- 更新 `code/web_mvp/backend/tests/test_app.py`。
- 更新 `code/web_mvp/e2e/browser_smoke.mjs`。
- 新增 `docs/pcweb-109-mic-adapter-noop-ui-invocation-plan.md`。
- 同步 `README.md`、`code/desktop_tauri/README.md`、`docs/current-mainline-index.md`、`docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 和 `docs/requirements-traceability-matrix.md`。

PCWEB-109 行为：

- Web 工作台在 `desktop-mic-adapter-contract-panel` 中同时展示 PCWEB-105/106 合同 readiness 和 PCWEB-107 七个 no-op command 的 invocation status。
- 普通浏览器中没有 Tauri IPC，因此必须显示 `mic_adapter_browser_fallback` 和 7 个 `not_invoked` row。
- 未来 Tauri WebView 中才允许通过 `window.__TAURI__.core.invoke` 或 `window.__TAURI__.tauri.invoke` 调用：
  - `mic_adapter_prepare`
  - `mic_adapter_status`
  - `mic_adapter_start`
  - `mic_adapter_pause`
  - `mic_adapter_resume`
  - `mic_adapter_stop`
  - `mic_adapter_delete_audio_chunks`
- 所有 no-op invocation safety flags 继续保持 `safe_to_request_audio_permission_now=false`、`safe_to_capture_audio_now=false`、`safe_to_write_audio_chunk_now=false`、`safe_to_delete_audio_chunks_now=false`、`safe_to_call_remote_asr_now=false`、`safe_to_call_llm_now=false` 和 `safe_to_run_tauri_or_cargo_now=false`。

安全边界：

- 不访问麦克风。
- 不请求音频权限。
- 不枚举设备。
- 不采集或写入 audio chunk。
- 不删除真实 audio chunk。
- 不启动 ASR worker。
- 不读取真实用户音频或 `.m4a`。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: `1 failed, 2 warnings`。失败原因是静态 JS 尚未包含 `loadDesktopMicAdapterNoopInvocation`。
- 绿灯：
  - 同一 focused command。
  - Result: `1 passed, 2 warnings`。
- 总门禁：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile pc-web`
  - Result: root-pytest `286 passed, 2 warnings`；core `34 passed, 1 warning`；Web backend `316 passed, 2 warnings`；browser smoke `status=ok` 且 checked list 包含 `desktop mic adapter no-op invocation browser fallback`；quality gate profile `pc-web passed`。

文档治理：

- README 顶部阶段改为 `PC/desktop mainline`，明确本地 Web MVP 只是 core/API/UI/event 的验证切片。
- `docs/current-mainline-index.md` 和总控计划记录 PCWEB-109 已推进 M4。
- M5 `Short local simulated input` 明确限定为合成生成音频、mock events 或 `artifacts/tmp/asr_events` 下 approved synthetic event file；不得读取 `.m4a`、本地私有短音频、`data/asr_eval/local_samples` 或任意用户录音。
- 公开音频保持 no-download blocked，除非后续提供可信 planned samples manifest 或明确批准公开包下载。

复审条件：

PCWEB-109 只证明 no-op invocation surface，不代表真实麦克风、真实录音、真实 audio chunk lifecycle、真实 worker 或真实 Tauri run 已经可用。真实麦克风 shadow test 仍待后续 desktop runtime、worker handoff、mic adapter lifecycle、导出和反馈链路具备后，由用户显式启动。

## DEC-141：实现 PCWEB-110 short local simulated input timeline report

日期：2026-07-03

状态：Accepted

背景：

DEC-136/138 将后续主线压成 6 个里程碑，其中 M5 是 `Short local simulated input`。PCWEB-108 已证明 approved synthetic worker-like event output 可以进入 Web Live ASR session 并形成 transcript、EvidenceSpan、state/gap candidate 和 LLM request draft；PCWEB-109 已把 mic adapter no-op invocation surface 展示到 Web 工作台。用户再次强调转写验证需要由我先用网上公开音频来源和模拟推进，最终真实麦克风会议由用户验证。为了避免 M5 被误解成读取本地私有短音频、`.m4a` 或直接开麦克风，M5 必须收束为只读 approved synthetic/mock event file 的 timeline report。

决策：

实现 PCWEB-110：

- 更新 `tools/asr_live_pipeline_replay.py`。
- 更新 `tests/test_asr_live_pipeline_replay.py`。
- 新增 `docs/pcweb-110-short-local-simulated-input-timeline-report-plan.md`。
- 同步 `docs/current-mainline-index.md`、`docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/requirements-traceability-matrix.md` 和 `README.md`。

PCWEB-110 的 replay report 必须新增：

- `short_local_simulated_input_status`
- `input_source_kind=approved_synthetic_event_file`
- `timeline_window_ms`
- `asr_metrics`
- `evidence_span_timeline`
- `state_timeline`
- `candidate_card_timeline`

通过条件：

- 工程 synthetic/mock event file 必须能形成 EvidenceSpan、state event 和 suggestion candidate，并返回 `short_local_simulated_input_status=closed_to_candidate_timeline`。
- candidate/card timeline 中必须明确 `llm_call_status=not_called` 和 `card_status=not_created`，即本阶段不调用 LLM、不创建正式建议卡。
- 非工程 control 可以形成 transcript/EvidenceSpan，但必须返回 `short_local_simulated_input_status=no_engineering_candidate_detected`、`state_event_count=0`、`suggestion_candidate_count=0` 和 `candidate_card_timeline=[]`。

安全边界：

- 不访问麦克风。
- 不请求音频权限。
- 不读取真实用户录音。
- 不读取 `.m4a`。
- 不读取 `data/asr_eval/local_samples`。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不下载公开音频。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_live_pipeline_replay.py::test_replay_report_converts_asr_events_to_live_pipeline_without_llm_calls tests/test_asr_live_pipeline_replay.py::test_replay_report_keeps_non_engineering_control_at_zero_candidates -q -p no:cacheprovider`
  - Result: `2 failed, 1 warning`。失败原因是 report 中缺少 `short_local_simulated_input_status` 和 timeline 字段。
- 绿灯：
  - 同一 focused command。
  - Result: `2 passed, 1 warning`。
- 完整 replay gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_live_pipeline_replay.py -q -p no:cacheprovider`
  - Result: `7 passed, 1 warning`。
- 本地样本自测：
  - 工程样本 `artifacts/tmp/asr_events/api-review-001.mock.events.json` 返回 `closed_to_candidate_timeline`、`evidence_span_count=3`、`state_event_count=1`、`suggestion_candidate_count=1`、`llm_request_draft_count=1`、candidate/card timeline `not_called/not_created`。
  - 非工程 control `artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json` 返回 `no_engineering_candidate_detected`、`evidence_span_count=3`、`state_event_count=0`、`suggestion_candidate_count=0`、`candidate_card_timeline=[]`。

联网/公开音频口径：

公开音频继续只作为声学和 ASR event contract 验证来源，不作为产品价值证明。AliMeeting / OpenSLR SLR119 和 AISHELL-4 / OpenSLR SLR111 仍是会议声学主候选，但都是 GB 级官方包；AISHELL-1 / OpenSLR SLR33 只做普通话 sanity check；MagicHub Web Meeting 贴近线上会议但 CC BY-NC-ND 4.0 且需登录/人工复核，只能 observed，不进入自动下载、抽样、转码或产品价值 gate。没有 verified `archive_member_path`、clip sha256 和用户批准公开包下载时，公开音频阶段保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`。

复审条件：

PCWEB-110 不代表正式 suggestion card、真实用户 feedback、export、真实 ASR、真实 worker、真实 Tauri runtime 或真实麦克风采集已完成。它只完成 M5 的受限本地模拟 timeline report。下一步应转向真实 Tauri no-op run、worker/mic connector、export/feedback report adapter、ASR quality decision，或在用户最终验证阶段 ingestion 真实 shadow-test report；不得把 PCWEB-110 解读成可以读取真实音频或直接开麦克风。

## DEC-142：实现 PCWEB-111 ASR event provenance manifest

日期：2026-07-03

状态：Accepted

背景：

DEC-141/PCWEB-110 完成了 M5 的受限本地模拟 timeline report，但 `input_source_kind` 固定为 `approved_synthetic_event_file`。只要后续要接入 `synthetic_audio`、`mock_streaming` 或未来人工证据校验后的 `public_audio_sample`，replay report 就必须能记录事件来源，否则无法审计“这个 ASR event JSON 到底来自哪里”。同时，用户要求继续自测但不要增加不必要收费或触碰真实音频，因此 provenance 只能是只读 manifest，不得变成公开音频下载器、ASR 执行器或麦克风采集入口。

决策：

实现 PCWEB-111：

- 更新 `tools/asr_live_pipeline_replay.py`。
- 更新 `tests/test_asr_live_pipeline_replay.py`。
- 新增 `docs/pcweb-111-asr-event-provenance-manifest-plan.md`。
- 同步 `docs/current-mainline-index.md`、`docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/requirements-traceability-matrix.md` 和 `README.md`。

Manifest 合同：

- `manifest_version=asr_event_provenance.v1`
- `events_path` 必须匹配 replay 的 repo-relative events path。
- `input_source_kind` 只允许 `approved_synthetic_event_file`、`synthetic_audio`、`mock_streaming` 或 `public_audio_sample`。
- `provider_candidate`、`event_contract_version` 和 `generated_by` 必须为非空字符串。
- `source_id`、`script_id` 和 `sample_id` 是可选 provenance 字段。
- `safe_to_call_llm=false`
- `safe_to_call_remote_asr=false`
- `safe_to_capture_microphone=false`
- `safe_to_read_user_audio=false`
- `safe_to_download_public_audio=false`

行为：

- 未提供 manifest 时，保持 PCWEB-110 兼容行为：`input_source_kind=approved_synthetic_event_file`，`event_manifest_status=not_provided`。
- 提供合法 manifest 时，report 输出 `event_manifest_status=loaded`、repo-relative `event_manifest_path`、manifest 中的 `input_source_kind` 和 sanitized `event_provenance`。
- Manifest path 位于 forbidden root 或不在 `artifacts/tmp/asr_events` 时，返回 `blocked_by_event_manifest_path_validation`。
- Manifest schema 不合法或任一 side-effect flag 不为 false 时，返回 `blocked_by_event_manifest_validation`。
- Manifest 的 `source_id/script_id/sample_id/provider_candidate/event_contract_version/generated_by` 不得包含本机路径文本，例如 `/Users/...`。
- Manifest 的 provenance id fields 不得包含相对 forbidden-root 文本或音频路径文本，例如 `configs/local/...`、`data/asr_eval/local_samples/...` 或 `.m4a`。
- Manifest blocked 时不读取 events，不生成 EvidenceSpan/state/candidate timeline。

安全边界：

- 不访问麦克风。
- 不请求音频权限。
- 不读取真实用户录音。
- 不读取 `.m4a`。
- 不读取 `data/asr_eval/local_samples`。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不下载公开音频。
- 不抽取或转码公开音频。
- 不运行 ASR provider。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_live_pipeline_replay.py::test_replay_report_uses_event_manifest_provenance_for_public_audio_sample tests/test_asr_live_pipeline_replay.py::test_replay_report_blocks_event_manifest_with_side_effect_flags tests/test_asr_live_pipeline_replay.py::test_replay_report_rejects_forbidden_event_manifest_paths_before_reading -q -p no:cacheprovider`
  - Result: `3 failed, 1 warning`。失败原因是 `build_asr_live_pipeline_replay_report()` 尚不支持 `event_manifest_path`。
- 绿灯：
  - 同一 focused command。
  - Result: `3 passed, 1 warning`。
- 完整 replay gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_live_pipeline_replay.py -q -p no:cacheprovider`
  - Initial Result: `10 passed, 1 warning`。
- 路径泄漏加固：
  - 追加红灯 `test_replay_report_blocks_event_manifest_with_local_path_provenance`。
  - Result: `1 failed, 1 warning`。失败原因是 `sample_id=/Users/...` 仍可进入成功 report。
  - 加固后同一测试 Result: `1 passed, 1 warning`。
  - 加固后完整 replay gate Result: `11 passed, 1 warning`。
- 复审补充加固：
  - 追加红灯 `test_replay_report_blocks_event_manifest_with_forbidden_relative_path_provenance`。
  - Result: `1 failed, 1 warning`。失败原因是 `source_id=configs/local/...` 和 `sample_id=data/asr_eval/local_samples/...m4a` 仍可进入成功 report。
  - 加固后同一测试 Result: `1 passed, 1 warning`。
  - 加固后完整 replay gate Result: `12 passed, 1 warning`。
- 复审补充反斜杠回归：
  - 追加 `test_replay_report_blocks_event_manifest_with_backslash_path_provenance`。
  - 临时去掉 `\` 到 `/` 的归一化后 Result: `1 failed, 1 warning`，证明 `sample_id=data\asr_eval\local_samples\private.wav` 会被错误放行。
  - 恢复归一化后同一测试 Result: `1 passed, 1 warning`。
  - 最终完整 replay gate Result: `13 passed, 1 warning`。

复审条件：

PCWEB-111 只完成 event provenance，不代表公开音频已经可用、ASR event 已由真实音频生成、真实 ASR 质量达标、正式 suggestion card/feedback/export 已完成，或真实麦克风链路已完成。下一步最小闭环应转向公开音频 post-extraction evidence schema、replay -> DRV-033 shadow report draft adapter、真实 Tauri no-op run / worker-mic connector，或用户最终真实 shadow-test report ingestion。

## DEC-143：实现 DRV-034 public audio post-extraction evidence schema

日期：2026-07-03

状态：Accepted

背景：

DEC-142/PCWEB-111 已完成 ASR event provenance manifest，但未来如果要把 AliMeeting/AISHELL-4 等公开授权音频小样本接入 replay，不能直接跳到 ASR event 或下载器。DRV-031 只证明下载前 planned sample manifest；还需要一个抽样后的 evidence gate，记录人工批准抽样后的 observed sha256、duration、采样率、声道、license citation 和 cleanup 状态，同时继续保持不读取音频、不下载、不抽取、不转码、不运行 ASR、不调用远程服务。

决策：

新增 DRV-034：

- `tools/public_audio_post_extraction_evidence_schema.py`
- `tests/test_public_audio_post_extraction_evidence_schema.py`
- `docs/drv-034-public-audio-post-extraction-evidence-schema-plan.md`

同步：

- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

Schema 合同：

- `manifest_version=public_audio_post_extraction_evidence.v1`
- `planned_sample_id`
- `source_id/source_url/source_license/source_snapshot_date`
- `archive_name/archive_member_path`
- `clip_start_seconds/clip_end_seconds/expected_duration_seconds`
- `expected_sha256_after_extract`
- `observed_sha256`
- `observed_duration_seconds`
- `sample_rate_hz`
- `channel_count`
- `container_format`
- `codec`
- `license_citation`
- `cleanup_status`
- `derived_artifact_root`
- no-download/no-extract/no-transcode/no-audio-read/no-ASR/no-remote/no-LLM flags 全 false

行为：

- 默认 CLI 只输出 schema contract：`schema_status=specified_not_executable`、`evidence_report_status=not_provided`。
- 合法 evidence report 返回 `evidence_report_status=schema_validated_no_audio_access`。
- side-effect flag 为 true、checksum mismatch、source attribution 不匹配、unsafe archive path 或 local-path text 均返回 `blocked_by_schema_validation`。
- evidence report path 必须在 `artifacts/tmp/public_audio` 下；`configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime` 和 `outputs` 在读取前阻断。

安全边界：

- 不下载公开音频。
- 不解压或抽取 archive。
- 不裁剪或转码音频。
- 不读取音频文件或 `.m4a`。
- 不运行外部命令。
- 不运行 ASR provider。
- 不访问麦克风。
- 不读取真实用户音频。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_post_extraction_evidence_schema.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`。失败原因是 `tools/public_audio_post_extraction_evidence_schema.py` 不存在。
- 绿灯：
  - 同一 focused command。
  - Result: `7 passed, 1 warning`。
- 复审补充覆盖后：
  - 增加 forbidden report path、repo-outside path、allowed-root symlink escape、non-JSON suffix、source attribution mismatch、unsafe archive/member path、clip/duration mismatch、sample rate/channel count、cleanup status 和 derived artifact root 的负例覆盖。
  - 同一 focused command。
  - Result: `10 passed, 1 warning`。

复审条件：

DRV-034 只完成 post-extraction evidence schema，不代表公开音频已经下载、已经抽样、已经转写、真实 ASR 质量已经达标，或真实麦克风链路已经完成。下一步最小闭环应转向 `replay -> DRV-033 shadow report draft adapter`。

## DEC-144：实现 DRV-035 replay shadow report draft adapter

日期：2026-07-03

状态：Accepted

背景：

PCWEB-110 已把 approved synthetic/mock event file replay 成 timeline report，PCWEB-111 已补齐 ASR event provenance，DRV-034 已补齐公开音频 post-extraction evidence schema。用户再次确认完整计划需要写下，转写验证由我先通过网上公开来源复核和模拟推进，最终真实麦克风会议由用户验证。两个只读审查 Agent 均确认当前计划已经完整，公开音频、合成/Mock 转写和真实麦克风验证已经分开；下一步应继续 `replay -> DRV-033 shadow report draft adapter`，而不是继续泛搜音频或重复评测。

决策：

新增 DRV-035：

- `tools/replay_shadow_report_draft_adapter.py`
- `tests/test_replay_shadow_report_draft_adapter.py`
- `docs/drv-035-replay-shadow-report-draft-adapter-plan.md`

同步：

- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

Adapter 合同：

- 输入为内存 replay report 或 `artifacts/tmp/asr_reports` 下的 replay report JSON。
- 成功时输出 `adapter_status=shadow_report_draft_created`。
- candidate report 使用 DRV-033 `real_mic_shadow_test_report.v1`。
- transcript segments、EvidenceSpan timeline、state timeline、candidate-card timeline 和 ASR metrics 来自 replay report。
- feedback summary 固定全 0。
- final decision 固定 `inconclusive_requires_more_shadow_tests`。
- `privacy_cost_flags` 全 false。
- audio retention 固定 `audio_chunk_write_status=not_written` 和 `audio_delete_status=not_applicable_no_audio_written`。
- 非 candidate replay 返回 `blocked_by_replay_not_candidate_ready`，不伪造产品价值。
- 输入 replay 的 `validation_errors` 必须为空。
- 输入 replay 的 `safe_to_call_llm_now`、`safe_to_call_remote_asr_now`、`safe_to_read_user_audio_now`、`safe_to_read_configs_local_now`、`safe_to_capture_microphone_now` 必须全部明确为 false；否则 blocked，避免把上游危险 replay 洗成 all-false shadow draft。

路径和安全边界：

- `replay_report_path` 只允许 `artifacts/tmp/asr_reports`。
- 读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`。
- 读取前阻断仓库外路径和非 JSON 文件。
- 不访问麦克风。
- 不请求音频权限。
- 不枚举设备。
- 不读取真实用户音频或 `.m4a`。
- 不写 audio chunk。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行外部命令、Cargo 或 Tauri。

网上音频复核结论：

- AliMeeting / OpenSLR SLR119 官方页面显示为 Mandarin multi-channel meeting corpus，License 为 CC BY-SA 4.0，Eval 包约 3.42G；只作为 no-download 会议声学候选。
- AISHELL-4 / OpenSLR SLR111 官方页面显示为 Mandarin multi-channel meeting speech corpus，License 为 CC BY-SA 4.0，test 包约 5.2G；只作为 no-download 会议声学补充候选。
- AISHELL-1 / OpenSLR SLR33 官方页面显示 License 为 Apache License v2.0，但它是普通话语音 corpus，不是会议场景，只能做 ASR sanity check。
- FunASR 仍是中文质量主候选，但当前需要本地模型目录或 DRV-019 明确审批，不自动下载模型。

验证方式：

- TDD 初始红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_replay_shadow_report_draft_adapter.py -q -p no:cacheprovider`
  - Result: `4 failed, 1 warning`，原因是 adapter 工具不存在。
- 实现后首次 focused run：
  - Result: `1 failed, 3 passed, 1 warning`，原因是未直接绑定候选的 EvidenceSpan 输出 `draft_candidate_pending`，无法交叉引用 candidate timeline。
- 修复：
  - 补充测试断言未直接绑定的 EvidenceSpan 也引用现有 draft candidate。
  - Adapter fallback 到第一张 replay candidate id，不再输出无法交叉引用的占位 candidate id。
- 最终 focused 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_replay_shadow_report_draft_adapter.py -q -p no:cacheprovider`
  - Result: `4 passed, 1 warning`。
- 复审加固红灯：
  - 只读复审指出旧实现只校验 `safe_to_call_llm_now` 和 `safe_to_capture_microphone_now`，可能接受 `safe_to_call_remote_asr_now=true`、`safe_to_read_user_audio_now=true` 或 `validation_errors` 非空的 replay，并输出 all-false privacy/cost draft。
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_replay_shadow_report_draft_adapter.py::test_replay_shadow_report_draft_blocks_replay_with_side_effect_flags tests/test_replay_shadow_report_draft_adapter.py::test_replay_shadow_report_draft_blocks_replay_with_validation_errors -q -p no:cacheprovider`
  - Result: `2 failed, 1 warning`，证明旧实现会生成 `shadow_report_draft_created`。
- 复审加固绿灯：
  - 同一 selection Result: `2 passed, 1 warning`。
  - 完整 DRV-035 focused gate Result: `6 passed, 1 warning`。

复审条件：

DRV-035 只证明 replay timeline 可以对齐未来真实 shadow-test report schema。它不代表真实麦克风会议已经发生，不代表公开音频已下载/抽样/转写，不代表 ASR 中文技术实体质量已经达标，也不代表真实用户 feedback 或 Go/Pivot/Stop 结论已经可用。下一步不应继续评测循环，应转向真实 Tauri no-op run、worker/mic connector、shadow report ingestion/export/feedback，或 ASR quality decision 的退出动作。

## DEC-145：实现 DRV-036 shadow report ingestion/export/feedback readiness

日期：2026-07-03

状态：Accepted

背景：

DRV-033 已固定真实麦克风 shadow-test report schema，DRV-035 已把 PCWEB-110/111 replay timeline 映射为 DRV-033 candidate report draft。用户再次确认完整计划已经要按“网上公开音频来源复核 + 合成/Mock 转写自测 + 用户最终真实麦克风会议验证”执行，且不能继续陷入无限评测循环。因此下一步必须把 shadow report draft 接入导出、反馈和 Go/Pivot/Stop readiness，而不是继续泛搜音频或重复做 ASR/provider 横评。

决策：

新增 DRV-036：

- `tools/shadow_report_ingestion_export_feedback.py`
- `tests/test_shadow_report_ingestion_export_feedback.py`
- `docs/drv-036-shadow-report-ingestion-export-feedback-plan.md`

同步：

- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

工具合同：

- 允许直接 ingest DRV-033 `real_mic_shadow_test_report.v1` candidate report。
- 允许读取 `artifacts/tmp/real_mic_shadow_reports` 下的 candidate report JSON。
- 允许读取 `artifacts/tmp/asr_reports` 下的 DRV-035 adapter report JSON，并抽取 `candidate_report`。
- 必须调用 DRV-033 schema validation。
- 必须输出 `timeline_counts`、`feedback_analysis`、`feedback_collection_status`、`final_decision_readiness_status`、`export_readiness_status`、`json_export_preview` 和 `markdown_export_preview`。
- DRV-035 replay draft 只能输出 `draft_export_preview_only` / `feedback_required_before_decision`，不能作为 Go 证据。
- 有真实反馈且 schema 支持的 report 才能进入 `ready_for_shadow_test_export`。
- DRV-036 只输出 export preview，不写导出文件。

路径和安全边界：

- `candidate_report_path` 只允许 `artifacts/tmp/real_mic_shadow_reports` 或 `artifacts/tmp/asr_reports`。
- 读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和非 JSON 文件。
- 不访问麦克风。
- 不请求音频权限。
- 不枚举设备。
- 不读取真实用户音频或 `.m4a`。
- 不写或删除 audio chunk。
- 不写真实导出文件。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行 ASR provider、外部命令、Cargo 或 Tauri。

公开音频和真实麦克风边界复述：

- AliMeeting / OpenSLR SLR119 和 AISHELL-4 / OpenSLR SLR111 继续只作为 no-download 会议声学候选。
- AISHELL-1 / OpenSLR SLR33 只做普通话 sanity。
- 没有 verified clip manifest 或用户明确批准 GB 级公开包下载前，不下载公开音频、不抽样、不转码。
- 真实麦克风会议仍由用户最终执行；DRV-036 不代表真实会议已经发生。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_ingestion_export_feedback.py -q -p no:cacheprovider`
  - Result: `6 failed, 1 warning`，原因是 `tools/shadow_report_ingestion_export_feedback.py` 不存在。
- 中间红灯：
  - 同一 focused command。
  - Result: `1 failed, 5 passed, 1 warning`，原因是一张卡同时标记 `useful` 和 `would_have_asked` 时 usefulness ratio 可能超过 1.0。
- 修复：
  - `usefulness_ratio` 和 `negative_ratio` capped at 1.0。
- 最终 focused 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_ingestion_export_feedback.py -q -p no:cacheprovider`
  - Result: `6 passed, 1 warning`。

复审条件：

DRV-036 只完成 shadow report ingestion/export/feedback readiness。它不代表真实麦克风会议已经发生，不代表真实 audio chunk 已采集或删除，不代表 replay draft 已有真实用户反馈，不代表 ASR 中文技术实体质量已经达标，也不代表真实导出文件已经写入磁盘。下一步不得继续做泛评测，默认应转向 `shadow report export file writer`、`feedback ingestion API/UI`、`Real Tauri no-op run`、`worker/mic connector` 或 ASR quality decision 的退出动作。

## DEC-146：实现 DRV-037 shadow report export file writer

日期：2026-07-03

状态：Accepted

背景：

DRV-036 已把 DRV-033/035 report 接入 feedback analysis、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview，但它故意不写文件。用户要求继续按文档计划实现和自测完成，且不要继续陷入泛评测循环。因此下一步把 DRV-036 preview 写入 ignored artifact root，形成可复查的本地导出物，同时继续避免麦克风、真实音频、远程调用和仓库可提交运行产物风险。

决策：

新增 DRV-037：

- `tools/shadow_report_export_file_writer.py`
- `tests/test_shadow_report_export_file_writer.py`
- `docs/drv-037-shadow-report-export-file-writer-plan.md`

同步：

- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

工具合同：

- 调用 DRV-036 `shadow_report_ingestion_export_feedback`，不重复实现 report schema。
- 默认输出根为 ignored `artifacts/tmp/shadow_report_exports`。
- 写入 `<session_id>.shadow-report.json` 和 `<session_id>.shadow-report.md`。
- 输出 `written_files`，记录 kind、repo-relative path、sha256 和 byte count。
- `session_id` 只能包含 ASCII 字母、数字、点、下划线和短横线，防止路径穿越。
- existing files 内容一致时返回 `idempotent_existing_files_match`。
- existing files 内容不一致时返回 `blocked_by_existing_export_conflict`，不覆盖。
- unsafe output root 在调用 DRV-036 读取 candidate report 前阻断。
- replay draft 可以写预览，但 `go_evidence_status=not_go_evidence_replay_or_feedback_missing`。
- 真实反馈 report 且 DRV-036 readiness 支持时，才输出 `go_evidence_status=go_evidence_supported_by_real_feedback_report`。

路径和安全边界：

- 只写 `artifacts/tmp/shadow_report_exports` 下的 JSON/Markdown 文件。
- 写入前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和非 approved root。
- 不访问麦克风。
- 不请求音频权限。
- 不枚举设备。
- 不读取真实用户音频或 `.m4a`。
- 不写或删除 audio chunk。
- 不写仓库可提交导出文件。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行 ASR provider、外部命令、Cargo 或 Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_export_file_writer.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`，原因是 `tools/shadow_report_export_file_writer.py` 不存在。
- 绿灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_export_file_writer.py -q -p no:cacheprovider`
  - Result: `7 passed, 1 warning`。

复审条件：

DRV-037 只完成 ignored artifact export file writer。它不代表真实麦克风会议已经发生，不代表真实 audio chunk 已采集或删除，不代表 replay draft 已有真实用户反馈，不代表 ASR 中文技术实体质量已经达标，不代表正式产品导出目录已经确定，也不代表 feedback ingestion API/UI 已完成。下一步不得继续做泛评测，默认应转向 `feedback ingestion API/UI`、`Real Tauri no-op run`、`worker/mic connector` 或 ASR quality decision 的退出动作。

## DEC-147：锁定公开音频模拟和真实麦克风验收责任边界

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：完整计划必须写下来；转写类验证由我先通过网上公开音频来源和模拟推进，最终真实麦克风会议由用户验证。此前多 Agent 审查和总控计划已经确认，项目价值不在普通音频转文字，而在 `ASR final/revision -> EvidenceSpan -> meeting state / engineering gap -> suggestion card -> feedback/export` 的实时 Copilot 链路。因此本轮需要把“公开音频不等于真实 pilot、模拟不等于 Go 证据、真实麦克风由用户最终验收”的边界再次落档，并用工具结果证明当前不是继续无限评测。

决策：

- 不新增散点计划文档；继续以 `docs/current-mainline-index.md` 和 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 为 P0 入口。
- 公开音频只保留官方白名单来源：AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111、AISHELL-1 / OpenSLR SLR33。
- AliMeeting 和 AISHELL-4 只用于会议声学、多人/远场/重叠说话、切句和 ASR event contract；不用于产品价值 Go 证据。
- AISHELL-1 只用于普通话 ASR/runtime sanity；不用于会议声学或工程建议价值判断。
- MagicHub Web Meeting、MagicData-RAMC、Common Voice zh-CN 继续保持 observed-but-not-whitelisted；WenetSpeech、Bilibili、YouTube、播客、公开课、直播回放和版权链不清音频继续排除。
- 没有 verified bounded sample manifest 和 GB 级公开包下载审批前，不下载、不抽取、不转码、不喂 ASR。
- 转写模拟继续走自建中文技术会议脚本、合成音频、mock streaming events、本地 ASR event replay 和 shadow report draft/ingestion/export 链路。
- 真实麦克风会议仍由用户在 desktop runtime、worker/mic adapter、start/pause/resume/stop/delete、导出和反馈链路具备后显式启动。

本轮联网复核结论：

- OpenSLR SLR119 官方页显示 AliMeeting 为 Mandarin multi-channel meeting speech corpus，license 为 CC BY-SA 4.0。
- OpenSLR SLR111 官方页显示 AISHELL-4 是 conference scenario Mandarin corpus，license 为 CC BY-SA 4.0，`test.tar.gz` 约 5.2G。
- OpenSLR SLR33 官方页显示 AISHELL-1 license 为 Apache License v2.0，`data_aishell.tgz` 约 15G，适合普通话 sanity，不适合会议声学或产品价值证明。

工具复核：

- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
  - Result: `source_validation_status=passed`，`source_count=3`，`safe_to_download_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
  - Result: exit code 1 as expected，`plan_status=blocked_no_planned_samples`，`download_command=null`，`extract_command=null`，`transcode_command=null`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
  - Result: exit code 1 as expected，`decision_status=blocked_no_verified_public_sample_manifest`，blocked reasons 为 `no_verified_archive_member_path`、`no_expected_clip_sha256_after_extract`、`no_user_approval_for_gb_archive_download`。
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result: `25 passed, 1 warning`。

复审条件：

DEC-147 不授权任何新 side effect。它不授权下载公开音频包，不授权读取 `.m4a` 或真实用户录音，不授权访问麦克风，不授权读取 `configs/local`，不授权自动下载 FunASR/ModelScope 模型，不授权调用远程 ASR/LLM，也不把公开音频或 replay draft 视为产品价值 Go 证据。下一步主线仍应转向 `feedback ingestion API/UI`、`Real Tauri no-op run`、`worker/mic connector` 或 ASR quality decision 的退出动作。

## DEC-148：实现 DRV-038 shadow report feedback ingestion API/UI

日期：2026-07-03

状态：Accepted

背景：

DRV-033 已固定真实 shadow-test report schema，DRV-036/037 已完成 report ingestion、Go/Pivot/Stop readiness、导出预览和 ignored artifact export file writer。但真实会议后的产品价值闭环还缺一段：用户需要把每张建议卡的反馈标签接回 report，否则系统只能导出已有报告，不能把 `useful/would_have_asked/wrong/too_late/too_intrusive/dismissed` 转化为 Go/Pivot/Stop 证据。用户要求继续按主线实现和自测，且不再陷入公开音频/ASR 泛评测循环。

决策：

新增 DRV-038：

- `tools/shadow_report_feedback_ingestion.py`
- `tests/test_shadow_report_feedback_ingestion.py`
- `docs/drv-038-shadow-report-feedback-ingestion-api-ui-plan.md`

修改 Web MVP：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`

工具/API 合同：

- 支持直接传入 DRV-033 candidate report。
- 支持读取 `artifacts/tmp/real_mic_shadow_reports` 下的 candidate report JSON。
- 支持读取 `artifacts/tmp/asr_reports` 下的 DRV-035 adapter report，并抽取 `candidate_report`。
- `feedback_entries` 必须非空，且每项为 `{candidate_id,label}`。
- `candidate_id` 必须引用 `candidate_card_timeline`。
- 每个 candidate 最多一个标签。
- 标签只允许 `useful`、`would_have_asked`、`wrong`、`too_late`、`too_intrusive`、`dismissed`。
- 工具把反馈写入 card 的 `feedback_label`，重算 `feedback_summary`，并更新 `final_decision` preview。
- 真实 audio-written report 在 `useful + would_have_asked >= 2` 且 `wrong + too_late + too_intrusive <= 1` 时更新为 `go`。
- Replay draft 或 `audio_chunk_write_status=not_written` 的 report 即使有正反馈也保持 `inconclusive_requires_more_shadow_tests`，并输出 `not_go_evidence_replay_or_feedback_missing`。
- Web backend 暴露 `POST /shadow-reports/feedback-ingestions`。
- Web 工作台新增 `shadow-report-feedback-panel`，手动提交 candidate report JSON 和 feedback entries JSON。

路径和安全边界：

- `candidate_report_path` 沿用 DRV-036 allowed roots。
- 读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和非 JSON。
- 不访问麦克风。
- 不请求音频权限。
- 不枚举设备。
- 不读取真实用户音频或 `.m4a`。
- 不写或删除 audio chunk。
- 不写 candidate report 文件。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行 ASR provider、外部命令、Cargo 或 Tauri。

验证方式：

- 工具 TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_feedback_ingestion.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`，原因是 `tools/shadow_report_feedback_ingestion.py` 不存在。
- 工具绿灯：
  - 同一 focused command。
  - Result: `7 passed, 1 warning`。
- API 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path -q -p no:cacheprovider`
  - Result: `2 failed, 2 warnings`，原因是 `POST /shadow-reports/feedback-ingestions` 返回 404。
- API 绿灯：
  - 同一 command。
  - Result: `2 passed, 2 warnings`。
- UI 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: `2 failed, 2 warnings`，原因是 HTML/JS/CSS 中还没有 shadow feedback panel 和提交逻辑。
- UI 绿灯：
  - 同一 command。
  - Result: `2 passed, 2 warnings`。
- Focused 合并：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_report_feedback_ingestion.py code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: `11 passed, 2 warnings`。

复审条件：

DRV-038 只完成 shadow report feedback ingestion API/UI。它不代表真实麦克风会议已经发生，不代表真实 audio chunk 已采集或删除，不代表 ASR 中文技术实体质量已经达标，不代表 replay draft 可以成为 Go 证据，不代表真实 worker/mic connector 已完成，也不授权读取 `configs/local` 或调用远程 ASR/LLM。下一步不得继续做泛评测，默认应转向 `Real Tauri no-op run`、`worker/mic connector`、真实 shadow-test report ingestion/export pilot 或 ASR quality decision 的退出动作。

## DEC-149：实现 DRV-039 shadow-test pilot bundle runner

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：完整计划必须写下，转写验证由我先通过网上公开音频来源复核和模拟完成，最终真实麦克风会议由用户验证。当前公开音频阶段已经按计划停在 no-download manifest blocked，DRV-033 到 DRV-038 已把真实 shadow-test report schema、replay draft、ingestion/export readiness、ignored export writer 和 feedback ingestion API/UI 串起来。还缺一个可复跑的 pilot bundle runner：真实会议后，用户给出 report 和卡片反馈时，系统应能一次性完成 feedback write-back、Go/Pivot/Stop readiness 和 ignored JSON/Markdown 导出。

决策：

新增 DRV-039：

- `tools/shadow_test_pilot_bundle_runner.py`
- `tests/test_shadow_test_pilot_bundle_runner.py`
- `docs/drv-039-shadow-test-pilot-bundle-runner-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具合同：

- 调用 DRV-038 `shadow_report_feedback_ingestion`，不重复实现 feedback rules。
- 调用 DRV-037 `shadow_report_export_file_writer`，不重复实现 export rules。
- `output_root` 必须先通过 DRV-037 guard，且必须在 `artifacts/tmp/shadow_report_exports` 下。
- unsafe output root 在读取 candidate report 前 blocked。
- feedback ingestion blocked 时不写导出文件。
- export writer blocked 时返回 `blocked_by_export_file_writer`。
- 真实 audio-written report 且 positive feedback 达标时输出 `pilot_bundle_written`、`go_evidence_supported_by_real_feedback_report` 和两个 bundle artifacts。
- replay draft 只输出 `pilot_bundle_preview_written_not_go_evidence`，不得成为 Go 证据。

路径和安全边界：

- 只写 ignored `artifacts/tmp/shadow_report_exports` 下的 JSON/Markdown bundle。
- 阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和非 approved output root。
- 不访问麦克风。
- 不请求音频权限。
- 不枚举设备。
- 不读取真实用户音频或 `.m4a`。
- 不写或删除 audio chunk。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行 ASR provider、外部命令、Cargo 或 Tauri。
- 不启动 worker。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_shadow_test_pilot_bundle_runner.py -q -p no:cacheprovider`
  - Result: `5 failed, 1 warning`，原因是 `tools/shadow_test_pilot_bundle_runner.py` 不存在。
- 绿灯：
  - 同一 focused command。
  - Result: `5 passed, 1 warning`。
- 源码禁用片段扫描：
  - `rg -n "subprocess|os\\.system|Popen|check_call|check_output|ffmpeg|afconvert|sounddevice|pyaudio|wave\\.open|requests\\.|urllib\\.request|modelscope|AutoModel|getUserMedia|MediaRecorder" tools/shadow_test_pilot_bundle_runner.py`
  - Result: no matches。

复审条件：

DRV-039 只完成真实 shadow-test report 的 feedback/export bundle orchestration。它不代表真实麦克风会议已经发生，不代表真实 audio chunk 已采集或删除，不代表 ASR 中文技术实体质量已经达标，不代表 replay draft 可以成为 Go 证据，不代表真实 worker/mic connector 已完成，也不授权下载公开音频、读取 `configs/local`、调用远程 ASR/LLM、下载模型或运行 Cargo/Tauri。下一步不得继续做 shadow report 包装或泛评测，默认应转向 `Real Tauri no-op run`、`worker/mic connector`、真实麦克风 shadow-test 前置清单或 ASR quality decision 的退出动作。

## DEC-150：实现 PCWEB-112 desktop worker/mic connector contract

日期：2026-07-03

状态：Accepted

背景：

DRV-039 后，shadow report / feedback / export / bundle 链路已经足够，不应继续做同类包装。当前真正靠近产品形态的缺口是桌面端麦克风 adapter 和 ASR worker 之间的连接边界。PCWEB-105 已定义 `mic_adapter.start` 合同和显式用户 start 边界，PCWEB-099 已定义 worker command protocol 但仍阻断 `source_kind=mic`。因此本轮选择实现 `worker/mic connector contract`，把两边合同放到同一 session 做组合校验，但继续保持 no-execution。

决策：

新增 PCWEB-112：

- `code/desktop_tauri/worker-mic-connector-contract.policy.json`
- `tools/desktop_worker_mic_connector_contract.py`
- `tests/test_desktop_worker_mic_connector_contract.py`
- `docs/pcweb-112-desktop-worker-mic-connector-contract-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具合同：

- 复用 PCWEB-105 `desktop_mic_adapter_contract`，不重复实现 mic adapter state rules。
- 复用 PCWEB-099 `desktop_asr_worker_command_protocol`，不重复实现 worker command rules。
- `connector_request` 必须要求 `session_id`、`adapter_id`、`worker_id` 一致。
- mic side 只允许 `mic_adapter.start`，并要求 `user_consent_state=explicit_user_start_granted`。
- worker side 只允许 `worker.prepare`，且 `worker_source_kind=mic` 必须继续保留 PCWEB-099 的 `source_kind requires later approval: mic` blocker。
- 合法 request 输出 `ready_for_worker_mic_connector_contract_review`，并给出下一步 `approve_worker_mic_source_after_real_tauri_noop_run`。
- `file` 和 `system_audio` source kinds 继续 blocked。

路径和安全边界：

- mic runtime root 只允许 `artifacts/tmp/desktop_mic_adapter_runtime`。
- audio chunk root 只允许 `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`。
- worker runtime root 只允许 `artifacts/tmp/desktop_asr_worker_runtime`。
- worker event root 只允许 `artifacts/tmp/asr_events`。
- 阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和非 approved root。
- 不访问麦克风。
- 不请求权限。
- 不枚举设备。
- 不采集、读取、写入或删除真实 audio chunk。
- 不启动 worker。
- 不执行 worker command。
- 不读写 worker event file。
- 不 mutate Web session。
- 不读取 `configs/local`。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- 不运行 Cargo/Tauri。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_connector_contract.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`，原因是 `code/desktop_tauri/worker-mic-connector-contract.policy.json` 和 `tools/desktop_worker_mic_connector_contract.py` 不存在。
- 绿灯：
  - 同一 focused command。
  - Result: `7 passed, 1 warning`。

复审条件：

PCWEB-112 只完成 worker/mic connector 的 no-side-effect 合同组合门禁。它不代表真实 Tauri WebView 已运行，不代表麦克风权限已请求，不代表真实音频已采集，不代表 worker 已启动，不代表 worker 能读取 audio chunk 或写 event file，不代表 Web Live ASR session 已被真实 worker mutation，也不代表 ASR 中文技术实体质量已达标。下一步不得继续做同类 connector wrapper，默认应转向 `Real Tauri no-op run`、`worker mic source approval packet`、真实麦克风 shadow-test 前置清单或 ASR quality decision 的退出动作。

## DEC-151：确认完整计划已落档，转写验证先走公开授权来源和模拟，真实麦克风由用户最终验证

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：完整计划是否已经写下来；转写类验证需要我先通过网上寻找合规音频和模拟完成；最终真实麦克风会议由用户验证。用户同时担心项目继续陷入无止境评测循环，要求收敛到主线。

决策：

- 完整计划已经写下，不再新建散点计划；主入口仍为 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md` 和 `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`。
- 只读 Agent 复审确认：当前没有明显计划缺口，缺的是执行态的中文技术实体 ASR 质量、真实 Tauri no-op run、worker mic source approval packet 和真实 shadow-test 前置清单。
- 联网寻找音频的范围限定为官方授权来源和 no-download manifest：AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111、AISHELL-1 / OpenSLR SLR33。公开视频、Bilibili、YouTube、播客、直播回放、公开课和授权链不清录音不得进入自动评测。
- 当前公开音频阶段保持 no-download blocked：有官方来源白名单，但没有 verified archive member path、expected clip sha256 和 GB 级公开包下载审批，因此不下载、不抽取、不转码、不喂 ASR。
- 模拟转写继续使用自建中文技术会议脚本、合成音频、mock streaming events 和 approved synthetic event file，目标是验证 `partial/final/revision -> EvidenceSpan -> state/gap -> candidate/card` 主线。
- 真实麦克风会议由用户最终显式启动。进入前必须完成 desktop runtime、worker handoff、mic adapter start/pause/resume/stop/delete、导出和反馈链路。

验证方式：

- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
  - Result: `source_validation_status=passed`，3 个白名单来源均保持 `safe_to_download_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
  - Result: `plan_status=blocked_no_planned_samples`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
  - Result: `decision_status=blocked_no_verified_public_sample_manifest`。
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result: `25 passed, 1 warning`。
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_live_pipeline_replay.py tests/test_asr_event_generation_from_public_or_synthetic_audio.py tests/test_synthetic_audio_generation_plan.py tests/test_synthetic_audio_batch_smoke.py -q -p no:cacheprovider`
  - Result: `24 passed, 1 warning`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
  - Result: `decision_status=requires_funasr_model_dir_or_drv019_approval`。

复审条件：

DEC-151 只确认计划、边界和下一步收敛方向。它不授权下载公开音频大包，不授权读取真实用户音频或 `.m4a`，不授权访问麦克风，不授权读取 `configs/local`，不授权调用远程 ASR/LLM，不授权下载 FunASR/ModelScope 模型，也不代表 ASR 中文技术实体质量已达标。下一步默认应转向 `Real Tauri no-op run`、`worker mic source approval packet`、真实麦克风 shadow-test 前置清单或 ASR quality decision 的退出动作。

## DEC-152：实现 PCWEB-113 desktop Tauri no-op run result intake

日期：2026-07-03

状态：Accepted

背景：

PCWEB-091 已定义未来真实 Tauri no-op shell smoke 的手动 packet，PCWEB-107 已静态绑定 10 个 no-op IPC command，PCWEB-109 已把 no-op invocation 接到 UI，PCWEB-112 已把 mic adapter start 与 worker mic source approval blocker 合并成合同门禁。下一步如果直接进入 worker mic source approval，会缺少“真实 Tauri WebView no-op run 是否确实观察到 10 个 no-op IPC 返回”的结构化证据。因此本轮补齐 result intake，而不是运行 Cargo/Tauri。

决策：

新增 PCWEB-113：

- `code/desktop_tauri/tauri-noop-run-result-intake.policy.json`
- `tools/desktop_tauri_noop_run_result_intake.py`
- `tests/test_desktop_tauri_noop_run_result_intake.py`
- `docs/pcweb-113-desktop-tauri-noop-run-result-intake-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具合同：

- 只接收 caller-provided JSON 或 `artifacts/tmp/desktop_tauri_noop_run_results` 下的 result JSON。
- 合法 result 必须声明 `desktop_tauri_noop_run_result.v1`、`tauri_webview`、显式 Tauri run approval、local dev URL loaded 和 Tauri IPC available。
- result 必须包含 PCWEB-107 的 10 个 no-op command。
- 每个 command 必须 `invoke_status=returned`，且返回 `noop_bound/noop_only/tauri_ipc_bound`、`side_effect_status=none`、`safe_to_invoke_noop=true`、`safe_to_execute_real_action=false`、`captures_audio=false`、`spawns_process=false`、`calls_remote_provider=false`、`writes_local_files=false`。
- 合法 result 输出 `validated_noop_ipc_observed` 和 `ready_for_worker_mic_source_approval_review`，下一步为 `review_worker_mic_source_approval_packet`。
- 缺失 command、失败 command、额外 command、side-effect drift、raw `stdout/stderr/path/cwd/env/api_key/authorization/bearer_token` 等字段、非 approved result path、`.m4a` 或 forbidden roots 都会 blocked。
- Blocked report 不回显原始路径、secret-like 值或 raw output。

路径和安全边界：

- 只允许 result path 位于 `artifacts/tmp/desktop_tauri_noop_run_results`。
- 阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和 `.m4a`。
- 不运行 Cargo/Tauri。
- 不访问麦克风。
- 不请求权限。
- 不枚举设备。
- 不采集、读取、写入或删除 audio chunk。
- 不启动 worker。
- 不读写 worker event file。
- 不 mutate Web session。
- 不读取 `configs/local`。
- 不读取 secret。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_noop_run_result_intake.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`，原因是 `code/desktop_tauri/tauri-noop-run-result-intake.policy.json` 和 `tools/desktop_tauri_noop_run_result_intake.py` 不存在。
- 中间绿灯修正：
  - 首次实现后 `1 failed, 6 passed, 1 warning`，原因是源码边界扫描发现 forbidden execution keyword 仍出现在安全字段字符串中。
- 绿灯：
  - 同一 focused command。
  - Result: `7 passed, 1 warning`。

复审条件：

PCWEB-113 只完成未来真实 Tauri no-op run 的结果验收入口。它不代表 Tauri 已经运行，不代表 Cargo/Tauri 获得执行授权，不代表麦克风权限已请求，不代表真实音频已采集，不代表 worker 已启动，不代表 worker mic source 已获批，也不代表 ASR 中文技术实体质量已达标。下一步不得继续做同类 result-intake wrapper，默认应转向 `worker mic source approval packet`、真实麦克风 shadow-test 前置清单、真实 Tauri no-op run 的显式执行或 ASR quality decision 的退出动作。

## DEC-153：实现 PCWEB-114 desktop worker mic source approval packet

日期：2026-07-03

状态：Accepted

背景：

PCWEB-112 已把 `mic_adapter.start` 合同预览和 `worker.prepare(source_kind=mic)` blocker 合成同一 session 的 connector 门禁。PCWEB-113 已为未来真实 Tauri WebView no-op run 提供结构化 result intake。但如果没有一个审批包，后续容易把“connector 合同通过”或“Tauri no-op IPC 返回”误读成 worker 已经可以接受 `source_kind=mic`。因此本轮补齐 PCWEB-114，把两份证据合成同一 session 的人工 review packet，继续保持不可执行。

决策：

新增 PCWEB-114：

- `code/desktop_tauri/worker-mic-source-approval.policy.json`
- `tools/desktop_worker_mic_source_approval.py`
- `tests/test_desktop_worker_mic_source_approval.py`
- `docs/pcweb-114-desktop-worker-mic-source-approval-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具合同：

- 复用 PCWEB-112：connector request 必须通过，且 worker side 必须仍保留 `source_kind requires later approval: mic` blocker。
- 复用 PCWEB-113：Tauri no-op result 必须通过，且 10 个 no-op IPC command 仍然全部 returned、无副作用。
- connector `session_id` 必须等于 Tauri `run_id`，防止跨 session 混用证据。
- 合法 report 只输出 `ready_for_manual_review_not_executable`、`worker_mic_source_approval_status=not_approved`、`approved_to_execute_now=false` 和 `safe_to_accept_worker_mic_source_now=false`。
- 下一步只会变成 `manual_approve_worker_prepare_source_kind_mic_or_keep_blocked`。
- 缺少 connector/request、Tauri result 失败、connector 失败、跨 session 证据、forbidden `policy_path` 或 policy safety flag 漂移都会 blocked。
- Blocked report 不回显完整 forbidden path、secret-like 值或 raw command output。

路径和安全边界：

- 不批准真实 worker/mic source。
- 不运行 Cargo/Tauri。
- 不访问麦克风。
- 不请求权限。
- 不枚举设备。
- 不采集、读取、写入或删除 audio chunk。
- 不启动 worker。
- 不执行 `worker.prepare(source_kind=mic)`。
- 不读写 worker event file。
- 不 mutate Web session。
- 不读取 `configs/local`。
- 不读取 secret。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_approval.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`，原因是 `code/desktop_tauri/worker-mic-source-approval.policy.json` 和 `tools/desktop_worker_mic_source_approval.py` 不存在。
- Path guard 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_approval.py::test_policy_path_rejects_forbidden_roots_before_reading -q -p no:cacheprovider`
  - Result: `1 failed, 1 warning`，原因是 `policy_path` 指向 `configs/local` 时旧实现会尝试读取文件。
- 绿灯：
  - 同一 focused command。
  - Result: `8 passed, 1 warning`。

复审条件：

PCWEB-114 只完成 worker mic source 的人工审批包。它不代表真实 Tauri WebView 已在本轮运行，不代表麦克风权限已请求，不代表真实音频已采集，不代表 worker 已启动，不代表 `source_kind=mic` 已经被实际批准，也不代表 ASR 中文技术实体质量已达标。下一步不得继续做同类 approval wrapper，默认应转向真实 Tauri no-op run 的显式执行、真实麦克风 shadow-test 前置清单、ASR quality decision 的退出动作，或在明确授权后进入最小真实 mic adapter implementation boundary。

## DEC-154：实现 PCWEB-115 real mic shadow-test readiness gate

日期：2026-07-03

状态：Accepted

背景：

用户确认完整计划必须写下，转写验证先由我通过官方公开音频来源复核、合成音频、mock events 和模拟 replay 完成，最终真实麦克风会议由用户验证。两个只读审查 Agent 复核后结论一致：计划层面已经完整，下一步不应继续泛化测评或继续做同类 wrapper；需要把真实麦克风 shadow test 的进入条件变成机器可审查 go/no-go。

决策：

新增 PCWEB-115：

- `code/desktop_tauri/real-mic-shadow-test-readiness.policy.json`
- `tools/real_mic_shadow_test_readiness_gate.py`
- `tests/test_real_mic_shadow_test_readiness_gate.py`
- `docs/pcweb-115-real-mic-shadow-test-readiness-gate-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具合同：

- 默认组合 DRV-032 ASR quality decision、PCWEB-114 worker mic source approval packet 和 DRV-033/036/037/038/039 export/feedback readiness。
- 可接收未来 caller-provided evidence：真实 Tauri no-op observed、真实 mic adapter smoke、ASR worker mic source smoke。
- 当前默认输出 `blocked_not_ready_for_user_real_mic_shadow_test` 和 `user_can_start_real_mic_shadow_test_now=false`。
- 当前 blocker 固定为：
  - `asr_quality_decision_requires_funasr_model_dir_or_drv019_approval`
  - `real_tauri_noop_run_result_not_provided`
  - `worker_mic_source_not_approved`
  - `mic_adapter_real_implementation_not_available`
  - `asr_worker_real_mic_source_not_available`
- 未来所有 evidence 满足时，gate 只输出 `ready_for_user_manual_real_mic_shadow_test`，真实采集仍必须由用户在 UI 中显式 start。

路径和安全边界：

- 不访问麦克风。
- 不请求或枚举音频权限/设备。
- 不读取真实用户音频或 `.m4a`。
- 不写、读或删除 audio chunk。
- 不启动 worker。
- 不运行 Cargo/Tauri。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载公开音频或模型。
- `policy_path` 在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 和 `.m4a`。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result: `7 failed, 1 warning`，原因是 `code/desktop_tauri/real-mic-shadow-test-readiness.policy.json` 和 `tools/real_mic_shadow_test_readiness_gate.py` 不存在。
- 绿灯：
  - 同一 focused command。
  - Result: `7 passed, 1 warning`。
- 默认 CLI：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_readiness_gate.py`
  - Result: 退出码 `1`，因为当前真实麦克风 shadow test 仍 blocked，并输出上述 5 个 blocker。

复审条件：

PCWEB-115 只完成真实麦克风 shadow test 的静态 readiness gate。它不代表真实麦克风会议已发生，不代表 Tauri/Cargo 已运行，不代表麦克风权限已请求，不代表真实音频已采集，不代表 worker 已启动，也不代表 ASR 中文技术实体质量已达标。下一步不得继续做同类 readiness/report-only 包装器，默认应转向真实 Tauri no-op run 的显式执行、ASR quality decision 的退出动作，或在明确授权后进入最小真实 mic adapter implementation boundary。

## DEC-155：实现 PCWEB-116 desktop Tauri no-op run result collector

日期：2026-07-03

状态：Accepted

背景：

PCWEB-113 已有 Tauri no-op run result intake，但未来真实 Tauri WebView run 的 result JSON 仍缺少 UI 内采集面。PCWEB-115 又明确真实麦克风 shadow test 当前 blocked 于真实 Tauri no-op result 缺失等前置项。本轮不运行 Cargo/Tauri，也不继续新增同类 readiness wrapper，而是在 Web 工作台中补齐 Tauri no-op result collector。

决策：

修改：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `docs/pcweb-116-desktop-tauri-noop-run-result-collector-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具/UI 合同：

- 工作台 `desktop-mic-adapter-contract-panel` 新增 `No-op Result Collector` 段。
- 普通浏览器输出 `collector_browser_fallback`、`desktop_tauri_noop_run_result.v1`、10 个 `not_invoked` row 和 `real_tauri_noop_result_ready=false`。
- Tauri WebView 中通过 `window.__TAURI__.core.invoke` 或 `window.__TAURI__.tauri.invoke` 调 10 个 no-op IPC。
- Tauri WebView 中把 result 存入内存 `window.__meetingCopilotTauriNoopRunResult`，结构对齐 PCWEB-113 的 `desktop_tauri_noop_run_result.v1`。
- 10 个 command 为 `runtime_get_status`、`session_prepare`、`asr_worker_health` 和 7 个 `mic_adapter_*` no-op command。

路径和安全边界：

- 不运行 Cargo/Tauri。
- 不访问麦克风。
- 不请求或枚举音频权限/设备。
- 不采集、读取、写入或删除 audio chunk。
- 不启动 worker。
- 不读写 worker event file。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或公开音频。
- 不写本地文件。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: `1 failed, 2 warnings`，原因是 `loadDesktopTauriNoopRunResultCollector` 不存在。
- 浏览器红灯：
  - `node e2e/browser_smoke.mjs`
  - Result: `Error: expected Tauri no-op result collector browser fallback`。
- 绿灯：
  - focused static asset gate Result: `1 passed, 2 warnings`。
  - browser smoke Result: `status=ok`。

复审条件：

PCWEB-116 只完成未来真实 Tauri WebView no-op run 的内存 result collector。它不代表 Tauri/Cargo 已运行，不代表真实 Tauri result 已产生，不代表 worker mic source 已批准，不代表真实 mic adapter 已实现，不代表麦克风权限已请求，也不代表 ASR 中文技术实体质量已达标。下一步仍应转向真实 Tauri no-op run 的显式执行、ASR quality decision 的退出动作，或明确授权后的最小真实 mic adapter implementation boundary。

## DEC-156：再次锁定完整计划、公开音频模拟分工和下一步非空转主线

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：完整计划是否已经写下；转写类验证需要我先通过网上官方公开音频来源和模拟完成；最终真实麦克风会议由用户验证。两个只读审查 Agent 复核后结论一致：当前不缺计划和 gate，缺的是执行态；继续新增泛化评测、schema 或 readiness 文档会偏离主线。

决策：

- 完整计划已经写下，权威入口保持 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md`、`docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`、RTM 和本 decision-log。
- 转写验证分工固定为：我先做官方公开来源复核、no-download manifest、自建中文技术会议合成音频、mock streaming events、approved synthetic event replay 和 ASR quality gate；用户最终在前置链路满足后显式执行真实麦克风会议 shadow test。
- 公开音频阶段只允许 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111 和 AISHELL-1/OpenSLR SLR33 这类官方来源进入 no-download 工具链；MagicHub/MagicData/Common Voice 只作 observed-but-not-whitelisted，WenetSpeech、Bilibili、YouTube、播客、公开视频、公开课和版权链不清录音不得进入自动评测。
- 当前公开音频状态保持 `blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`，因为缺真实 `archive_member_path`、`expected_sha256_after_extract` 和 GB 级公开包下载审批；不下载、不抽取、不转码、不喂 ASR。
- 当前 ASR quality 状态保持 `requires_funasr_model_dir_or_drv019_approval`，因为 sherpa 中文技术实体召回不达标，FunASR 本地模型目录/cache 缺失，DRV-019 模型下载仍需显式审批。
- 下一步不再新增泛化计划、provider/source 横评或同类 report-only wrapper。默认主线只能是：ASR quality 退出动作、真实 Tauri no-op run、同 session worker mic source approval packet、明确审批后的最小真实 mic adapter implementation boundary。

验证方式：

- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
  - Result: `source_validation_status=passed`，3 个白名单来源均保持 `safe_to_download_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
  - Result: `plan_status=blocked_no_planned_samples`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
  - Result: `decision_status=blocked_no_verified_public_sample_manifest`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
  - Result: `decision_status=requires_funasr_model_dir_or_drv019_approval`。

复审条件：

DEC-156 不授权下载公开音频大包，不授权读取真实用户音频或 `.m4a`，不授权访问麦克风，不授权读取 `configs/local`，不授权调用远程 ASR/LLM，不授权下载 FunASR/ModelScope 模型，不授权运行 Cargo/Tauri，也不代表真实中文技术会议 ASR 已达标。它只把用户确认、Agent 复核、官方来源/no-download 状态和下一步主线锁进可追溯记录。

## DEC-157：实现 PCWEB-117 desktop Tauri no-op run result validation API/UI

日期：2026-07-03

状态：Accepted

背景：

PCWEB-116 已能在未来 Tauri WebView 中收集 `desktop_tauri_noop_run_result.v1`，PCWEB-113 已能校验 caller-provided result JSON。但二者之间仍缺 Web backend/API/UI 连接：如果真实 Tauri no-op run 完成后仍需人工复制 JSON 跑 CLI，worker mic source approval 的证据链仍然容易断。

决策：

修改：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `docs/pcweb-117-desktop-tauri-noop-run-result-validation-api-ui-plan.md`

同步：

- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`

工具/API/UI 合同：

- 新增 `POST /desktop/tauri-noop-run-results/validations`。
- Endpoint 接收 `run_result`，复用 `tools/desktop_tauri_noop_run_result_intake.py` 的 PCWEB-113 validator。
- Validation 通过时返回 PCWEB-113 intake report。
- Validation 失败时返回 HTTP 422，`detail` 为 PCWEB-113 report。
- 普通浏览器只显示 `validation_browser_fallback` / `pcweb_117_validation_status=not_submitted`，不提交 validation request。
- 未来 Tauri WebView 中 PCWEB-116 collector 的 10 个 no-op command 全部 returned 后，才自动调用 validation endpoint。
- `data_dir` 模式下 validation 不创建 `sessions`、`live_asr_sessions` 或 `desktop_tauri_noop_run_results` 存储目录。

路径和安全边界：

- 不运行 Cargo/Tauri。
- 不访问麦克风。
- 不请求或枚举音频权限/设备。
- 不采集、读取、写入或删除 audio chunk。
- 不启动 worker。
- 不读写 worker event file。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型或公开音频。
- 不写本地 result 文件。
- 不批准 worker mic source。

验证方式：

- TDD 红灯：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_accepts_collector_result_without_running_tauri code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_rejects_browser_fallback_or_side_effects code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_with_data_dir_does_not_create_storage code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result: `4 failed, 2 warnings`，原因是 validation endpoint 404 且前端 validation function/UI 标记不存在。
- 绿灯：
  - 同一 focused command。
  - Result: `4 passed, 2 warnings`。
- 浏览器绿灯：
  - `MEETING_COPILOT_E2E_VERBOSE=1 MEETING_COPILOT_E2E_PORT=8773 MEETING_COPILOT_E2E_CHROME_PORT=9333 node e2e/browser_smoke.mjs`
  - Result: `status=ok`，包含 `validation_browser_fallback` 与 `pcweb_117_validation_status=not_submitted`。

复审条件：

PCWEB-117 只完成 PCWEB-116 collector result 到 PCWEB-113 validator 的 API/UI 连接。它不代表真实 Tauri/Cargo 已运行，不代表真实 Tauri result 已产生，不代表 worker mic source 已批准，不代表真实 mic adapter 已实现，不代表麦克风权限已请求，也不代表 ASR 中文技术实体质量已达标。下一步仍应转向真实 Tauri no-op run 的显式执行、ASR quality decision 的退出动作，或明确授权后的最小真实 mic adapter implementation boundary。

## DEC-158：确认计划已完整落档，公开音频和模拟转写由我先做，真实麦克风由用户最终验证

日期：2026-07-03

状态：Accepted

背景：

用户最新确认：完整计划是否已经写下来；转写类验证需要我先通过网上官方公开音频和模拟完成；最终真实麦克风会议由用户验证。为避免继续陷入评测循环，本轮只做官方来源复核、现有文档/工具审查和只读多 Agent 反向检查，不新增泛化计划，不访问麦克风，不读取真实用户音频或 `.m4a`，不下载公开音频大包，不调用远程 ASR/LLM。

决策：

- 完整计划已经写下，不再新建散点计划作为主线进展。权威入口保持 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md`、`docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`、RTM 和本 decision-log。
- 两个只读审查 Agent 结论一致：计划/边界没有关键缺口；当前缺的是执行态证据，包括 ASR quality、公开音频 verified sample manifest、真实 Tauri no-op run result、worker mic source approval、真实 mic adapter 和 ASR worker real mic source。
- 转写验证分工固定为：我先做官方公开来源复核、no-download manifest、自建中文技术会议合成音频、mock streaming events、approved synthetic event replay 和 ASR quality gate；用户最终在前置链路满足后显式执行真实麦克风会议 shadow test。
- OpenSLR 官方来源复核结论保持：AliMeeting/OpenSLR SLR119 与 AISHELL-4/OpenSLR SLR111 是会议声学 no-download 主候选；AISHELL-1/OpenSLR SLR33 只做普通话 ASR sanity；MagicHub/MagicData/Common Voice 只作 observed-but-not-whitelisted；WenetSpeech、Bilibili、YouTube、播客、公开视频、公开课和版权链不清录音不得进入自动评测。
- 公开音频状态保持 `blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`，原因是缺真实 `archive_member_path`、expected clip sha256 和 GB 级公开包下载审批；不下载、不抽取、不转码、不喂 ASR。
- ASR quality 状态保持 `requires_funasr_model_dir_or_drv019_approval`，原因是 sherpa 中文技术实体召回不达标，FunASR 本地模型目录/cache 缺失；没有本地模型目录或 DRV-019 审批前不运行真实 FunASR smoke。
- 真实麦克风状态保持 `blocked_not_ready_for_user_real_mic_shadow_test`，原因是 ASR quality、真实 Tauri no-op result、worker mic source approval、真实 mic adapter 和 ASR worker real mic source 仍未全绿。

后续执行锁：

- 不再新增泛化 ASR/provider/source 横评，也不再把同类 readiness/report-only wrapper 算作主线进展。
- 下一步优先级固定为：真实 Tauri no-op run、ASR quality 退出动作、同 session worker mic source approval、明确审批后的最小真实 mic adapter implementation boundary。
- 公开音频只在拿到 official archive member path、clip start/end、expected sha256、license citation 和 cleanup manifest 后进入人工下载复核；否则继续 blocked，不绕去版权不清来源。
- 真实麦克风会议只能在 readiness gate 返回 ready 后，由用户在 UI 中显式开始。

复审证据：

- OpenSLR SLR119 官方页：AliMeeting 为 Mandarin multi-channel meeting speech corpus，License `CC BY-SA 4.0`，`Eval_Ali.tar.gz` 3.42G。
- OpenSLR SLR111 官方页：AISHELL-4 为 Mandarin multi-channel meeting speech corpus，License `CC BY-SA 4.0`，`test.tar.gz` 5.2G。
- OpenSLR SLR33 官方页：AISHELL-1 License `Apache License v.2.0`，`data_aishell.tgz` 15G。
- FunASR 官方 README：项目说明包含 ASR、speaker diarization、streaming 等能力；本项目仍要求本地模型目录或 DRV-019 审批后才执行真实 smoke。
- 只读 Agent 1 复核 README、总控计划、ASR next-run 计划、public/synthetic/real mic 计划、mainline index、decision-log、RTM 后结论：完整计划已经写下，责任边界清楚，缺的是执行态证据。
- 只读 Agent 2 复核公开音频、合成音频、ASR event、ASR quality 和 real mic readiness 工具/测试后结论：现有自动化足以做 no-download/schema/dry-run/replay gate，真实麦克风默认 blocked，需要补齐 ASR quality、Tauri no-op、worker mic source、真实 mic adapter 和 real ASR worker evidence。

复审条件：

DEC-158 不授权下载公开音频大包，不授权读取真实用户音频或 `.m4a`，不授权访问麦克风，不授权读取 `configs/local`，不授权调用远程 ASR/LLM，不授权下载 FunASR/ModelScope 模型，不授权运行真实 worker。它只把最新用户确认、官方来源复核、只读 Agent 复核和下一步执行锁写入可追溯记录。

## DEC-159：完成 PCWEB-118 desktop first controlled cargo check

日期：2026-07-03

状态：Accepted

背景：

PCWEB-116/117 已经补齐未来真实 Tauri no-op run 的 result collector 和 validation API/UI，但真实 Tauri run 之前必须先证明 Tauri Rust scaffold 至少能编译。此前文档和工具已经规定 `Cargo.lock` 只有在第一次受控 cargo check 后才允许生成，target 必须放到 ignored `artifacts/tmp/desktop_tauri_target`。本轮执行第一次受控 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`，不运行 `cargo tauri dev/build`，不访问麦克风，不启动 worker，不读取 `configs/local`，不调用远程 ASR/LLM。

Root cause：

- Tauri `generate_context!()` 默认读取 `src-tauri/icons/icon.png`，当前 scaffold 没有默认 icon，导致 proc macro panic。
- `#[tauri::command] pub fn ...` 会生成并 public reexport helper macro，在同一 module 中触发 duplicate definitions；10 个 no-op command 全部受影响。
- PCWEB-084 的 artifact policy 测试仍期待 `Cargo.lock` 不存在，但 PCWEB-118 后 `Cargo.lock` 已按计划生成并应保留，测试状态需要同步。

决策：

- 新增最小 `code/desktop_tauri/src-tauri/icons/icon.png`，只满足 Tauri context 默认 icon 读取，不启用 bundle 或 installer。
- 将 `code/desktop_tauri/src-tauri/src/lib.rs` 中 10 个 no-op Tauri command 从 `pub fn` 改为 private `fn`，仍由同 module `tauri::generate_handler!` 绑定。
- 保留 `code/desktop_tauri/src-tauri/Cargo.lock` 作为桌面 app reproducibility artifact。
- Cargo target 继续固定在 ignored `artifacts/tmp/desktop_tauri_target`，不允许 source tree `target`。
- `code/desktop_tauri/cargo-check.policy.json` 从“未来生成”更新为“PCWEB-118 后已生成/应保留”；policy tool 自身仍不执行 Cargo。

验证方式：

- Red：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_desktop_tauri_noop_shell_run_smoke.py -q -p no:cacheprovider`
  - Result：`14 failed, 7 passed, 1 warning`，缺 icon、command function public、artifact policy 仍把 `Cargo.lock` 当 blocker。
- Green policy/scaffold gate：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_cargo_check_artifact_policy.py tests/test_desktop_first_cargo_check_execution_boundary.py tests/test_desktop_tauri_scaffold.py tests/test_desktop_tauri_noop_shell_run_smoke.py -q -p no:cacheprovider`
  - Result：`40 passed, 1 warning`。
- Controlled cargo check：
  - `CARGO_HOME=artifacts/tmp/rust_toolchain/cargo RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target artifacts/tmp/rust_toolchain/cargo/bin/cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
  - Result：exit 0，`Finished dev profile`.

边界：

PCWEB-118 只证明 Tauri Rust crate 可编译。它不代表 `cargo tauri dev` 已运行，不代表 Tauri window/WebView 已打开，不代表 PCWEB-116 collector 产出真实 result，不代表 PCWEB-117 validation 已接收真实 Tauri result，不代表 worker mic source 已批准，不代表真实 mic adapter 已实现，不代表麦克风权限已请求或音频已采集，也不代表 ASR 中文技术实体质量已达标。

下一步：

下一张主线票应转向真实 Tauri no-op run 的显式执行和结果摄入，或 ASR quality exit。不得再把新增 compile/readiness/report-only 文档当作主线进展；真实麦克风会议仍必须等待 readiness gate 返回 ready 后由用户显式执行。

## DEC-160：完成 PCWEB-119 real Tauri no-op WebView IPC evidence

日期：2026-07-03

状态：Accepted

背景：

PCWEB-118 只证明 Tauri Rust crate 可编译。用户要求停止无止境评测循环，继续按照完整计划推进 PC 端主线；两个只读审查 Agent 也一致认为下一步应从评测/文档/readiness wrapper 转向真实 Tauri no-op WebView IPC evidence。PCWEB-116/117 已有 Web collector 和 validation endpoint，PCWEB-113 已有 strict result intake，当前缺的是一次真实 Tauri WebView 中的 no-op IPC 观测证据。

Root cause / 最小修复：

- 真实 run 前发现 `NoopBridgeResponse` 仍包含自由文本 `message` 字段。
- PCWEB-113 validator 只允许结构化 no-side-effect 字段，拒绝未知字段以避免 raw path、secret、stdout/stderr 或任意文本泄漏。
- 因此本轮先用 TDD 把 `tests/test_desktop_tauri_scaffold.py::test_noop_bridge_response_contract_declares_no_side_effects` 改为禁止 `message`，确认红灯后从 `code/desktop_tauri/src-tauri/src/lib.rs` 删除 `message` 字段和赋值。

决策：

- PCWEB-119 使用真实 Tauri WebView 加载本地 Web MVP dev URL `http://127.0.0.1:8765/`。
- 使用 `tools/desktop_tauri_noop_webview_run_capture_app.py` 包裹 Web backend，只在 `POST /desktop/tauri-noop-run-results/validations` 返回 200 时，把 evidence 写入 ignored `artifacts/tmp/desktop_tauri_noop_run_results`。
- 真实 Tauri run 只调用 10 个 PCWEB-107 no-op IPC command，不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM。
- PCWEB-119 evidence 只能用于后续 worker mic source approval review，不自动批准真实 worker/mic source。

验证方式：

- Red：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_scaffold.py::test_noop_bridge_response_contract_declares_no_side_effects -q -p no:cacheprovider`
  - Result：`1 failed, 1 warning`，失败原因为 `pub message:` 仍存在。
- Focused gate：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_tauri_scaffold.py tests/test_desktop_tauri_noop_shell_run_smoke.py tests/test_desktop_tauri_noop_run_result_intake.py tests/test_desktop_tauri_noop_webview_run_capture.py -q -p no:cacheprovider`
  - Result：`32 passed, 2 warnings`。
- Controlled cargo check：
  - `CARGO_HOME=artifacts/tmp/rust_toolchain/cargo RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target artifacts/tmp/rust_toolchain/cargo/bin/cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
  - Result：exit 0，`Finished dev profile`。
- Real Tauri no-op WebView run：
  - capture server：`python3 -m uvicorn desktop_tauri_noop_webview_run_capture_app:app --app-dir tools --host 127.0.0.1 --port 8765`
  - Tauri run：`CARGO_HOME=artifacts/tmp/rust_toolchain/cargo RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target artifacts/tmp/rust_toolchain/cargo/bin/cargo run --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
  - Evidence：`artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json`
  - Summary：`capture_status=captured_validated_tauri_noop_run`、`run_environment=tauri_webview`、`result_validation_status=passed`、`real_tauri_noop_run_evidence_status=ready_for_worker_mic_source_approval_review`、`validated_command_count=10`、`returned_command_count=10`。

边界：

PCWEB-119 只证明真实 Tauri WebView 已加载本地 Web app、`window.__TAURI__` no-op IPC 可用、10 个 no-op command returned、PCWEB-117 validation passed，并且 evidence 已捕获到 ignored artifact root。它不代表麦克风权限已请求，不代表真实音频已采集，不代表 ASR worker 已启动，不代表 worker mic source 已批准，不代表真实 mic adapter 已实现，不代表 ASR worker real mic source 已实现，也不代表 ASR 中文技术实体质量已达标。

下一步：

下一张主线票不得再做 Tauri result-intake/result-validation/readiness wrapper。默认应转向同 session worker mic source approval packet、ASR quality exit，或明确审批后的最小真实 mic adapter implementation boundary。真实麦克风会议仍必须等待 readiness gate 返回 ready 后由用户显式执行。

## DEC-161：实现 PCWEB-120 worker mic source from real Tauri evidence bridge

日期：2026-07-03

状态：Accepted

背景：

PCWEB-119 已生成真实 Tauri WebView no-op IPC evidence，并证明 10 个 no-op command returned、PCWEB-117 validation passed。PCWEB-114 已有 worker mic source approval packet，但它仍要求 caller 同时提供 connector request 和 Tauri run result。为避免后续人工复制 JSON 或继续新增 readiness wrapper，本轮实现 PCWEB-120：从 PCWEB-119 capture evidence 直接派生同 session PCWEB-112 connector request，再复用 PCWEB-114 生成 manual review packet。

决策：

- 新增 `code/desktop_tauri/worker-mic-source-from-tauri-evidence.policy.json`，所有执行、音频、worker、远程、secret safety flags 继续为 false。
- 新增 `tools/desktop_worker_mic_source_from_tauri_evidence.py`，只读取 `artifacts/tmp/desktop_tauri_noop_run_results` 下的 PCWEB-119 evidence JSON。
- Evidence path 在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径和 `.m4a`。
- Evidence 必须满足 `captured_validated_tauri_noop_run`、validation passed、10 个 command returned、`run_environment=tauri_webview` 和 all-false safety flags。
- 工具用 evidence `run_result.run_id` 派生同 session connector request，并声明 `connector_consent_scope=tauri_noop_ipc_only_not_real_audio_capture`。
- 工具调用 PCWEB-114 `build_worker_mic_source_approval_report`，输出仍为 `ready_for_manual_review_not_executable` / `worker_mic_source_approval_status=not_approved`，不自动批准真实 worker mic source。

验证方式：

- Red：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_from_tauri_evidence.py -q -p no:cacheprovider`
  - Result：`5 failed, 1 warning`，原因是 PCWEB-120 policy/tool 不存在。
- Green：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_from_tauri_evidence.py -q -p no:cacheprovider`
  - Result：`5 passed, 1 warning`。
- Real evidence CLI run：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_worker_mic_source_from_tauri_evidence.py --tauri-evidence-path artifacts/tmp/desktop_tauri_noop_run_results/tauri-noop-webview-2026-07-03T13-15-25-808Z.pcweb-119-tauri-noop-run-validation.json > artifacts/tmp/desktop_tauri_noop_run_results/pcweb-120-worker-mic-source-from-tauri-evidence.report.json`
  - Summary：`policy_validation_status=passed`、`tauri_evidence_validation_status=passed`、`derived_connector_request_status=derived_same_session_connector_request`、`worker_mic_source_approval_packet_status=ready_for_manual_review_not_executable`、`worker_mic_source_approval_status=not_approved`、`safe_to_capture_audio_now=false`、`safe_to_start_worker_now=false`。

边界：

PCWEB-120 只证明真实 Tauri evidence 可以被机器校验并转成同 session worker mic source manual review packet。它不代表 worker mic source 已批准，不代表 `worker.prepare(source_kind=mic)` 可以执行，不代表麦克风权限已请求或真实音频已采集，不代表真实 mic adapter 已实现，不代表 ASR worker real mic source 已实现，也不代表 ASR 中文技术实体质量已达标。

下一步：

下一张主线票不得再做 worker mic source evidence bridge、Tauri result-intake 或 readiness wrapper。默认应转向 ASR quality exit，或明确审批后的最小真实 mic adapter implementation boundary。真实麦克风会议仍必须等待 readiness gate 返回 ready 后由用户显式执行。

## DEC-162：修正公开音频模拟计划中的桌面状态漂移

日期：2026-07-03

状态：Accepted

背景：

用户再次确认完整计划需要写下，转写验证先由我通过网上官方公开音频来源和模拟完成，最终真实麦克风会议由用户验证。本轮只读审查 Agent A 确认 P0/P1 文档已经覆盖完整计划，但指出几个旧状态句仍把 `真实 Tauri no-op run` 写成未完成，或把 `缺 Rust/Cargo` 写成当前 blocker。PCWEB-118、PCWEB-119 和 PCWEB-120 已经分别完成受控 `cargo check`、真实 Tauri no-op WebView IPC evidence 和同 session worker mic source manual review packet bridge；继续保留旧句会让后续开发重复做已完成的 no-op/readiness 阶段。

决策：

- 不新建散点计划文档，只更新当前 P0/P1 入口。
- 在 `docs/current-mainline-index.md` 和 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md` 顶部边界中补充不读取任何 `.m4a`、`data/local_runtime/` 或 `outputs/`。
- 将 `current-mainline-index` 的下一步从“优先推进真实 Tauri no-op run”修正为“PCWEB-119/120 已完成；下一步只在 ASR quality exit、最小真实 mic adapter implementation boundary、ASR worker real mic source 前置实现之间推进”。
- 将总控计划中 FunASR 缺失时的默认动作修正为不重复 Tauri no-op run。
- 将 `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md` 的 Phase C 从“缺 Rust/Cargo，真实桌面窗口未跑”修正为 historical superseded，并列出当前 blockers：ASR quality、worker mic source approval、真实 mic adapter implementation、ASR worker real mic source。
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md` 顶部边界同步补充 `.m4a`、`data/local_runtime/` 和 `outputs/`。

验证方式：

- Stale 句扫描：
  - `rg -n "真实 Tauri.*尚未执行|缺 Rust/Cargo|优先真实 Tauri no-op run|真实桌面窗口未跑" docs/current-mainline-index.md docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md || true`
  - Result：无输出。
- 公开音频 no-download gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result：`25 passed, 1 warning`。
- ASR replay / real mic readiness focused gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_live_pipeline_replay.py tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`20 passed, 1 warning`。
- Sensitive scan：
  - Ran the configured sensitive-pattern scan over README, P0/P1 docs, RTM, decision-log and public source metadata without writing the sensitive patterns into the repository.
  - Result：无输出。

边界：

DEC-162 不授权下载公开音频包，不授权读取 `.m4a` 或真实用户录音，不授权访问麦克风，不授权读取 `configs/local`、`data/local_runtime` 或 `outputs`，不授权启动 worker，不授权调用远程 ASR/LLM，不授权下载模型。公开音频继续停在 no-download manifest；真实麦克风会议仍由用户最终显式执行。

## DEC-163：实现 PCWEB-121 minimal real mic adapter implementation boundary

日期：2026-07-03

状态：Accepted

背景：

PCWEB-115 readiness gate 已经能组合 ASR quality、真实 Tauri evidence、worker mic source approval、mic adapter evidence、ASR worker evidence 和 export/feedback readiness，但 PCWEB-120 后仍缺一个 mic adapter implementation-boundary evidence。PCWEB-105/107/109 只证明合同、no-op IPC 和 UI invocation，不代表真实 adapter implementation boundary 已存在。本轮按 TDD 实现 PCWEB-121，目标是让 readiness gate 可以识别 mic adapter boundary，同时继续禁止真实权限请求、真实采集、audio chunk I/O、worker 启动和远程调用。

决策：

- 新增 `code/desktop_tauri/src-tauri/src/mic_adapter_runtime.rs`，只定义 inert `MicAdapterRuntimeBoundaryEvidence`、command catalog、approved runtime/audio roots 和 all-false safety flags。
- 在 `code/desktop_tauri/src-tauri/src/lib.rs` 增加 `pub mod mic_adapter_runtime;`，不新增真实 capture IPC command。
- 新增 `code/desktop_tauri/mic-adapter-implementation-boundary.policy.json`，固定 `implementation_status=implemented_and_smoke_tested`、7 个 commands、显式用户 start、默认不上传 raw audio、默认不启用 remote ASR 和 all-false safety flags。
- 新增 `tools/desktop_mic_adapter_implementation_boundary.py`，只读取 policy/Rust source，输出 PCWEB-115 `_mic_adapter_ready()` 可识别的 evidence。
- 新增 `tests/test_desktop_mic_adapter_implementation_boundary.py`，覆盖 red/green、Rust inert boundary、tool source side-effect guard、bad policy 不可放宽和 readiness blocker 移除。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_mic_adapter_implementation_boundary.py -q -p no:cacheprovider`
  - Result：`6 failed, 1 warning`，原因是 PCWEB-121 policy、Rust module 和 tool 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_mic_adapter_implementation_boundary.py -q -p no:cacheprovider`
  - Result：`6 passed, 1 warning`。
- Focused integration gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_mic_adapter_implementation_boundary.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_desktop_worker_mic_source_from_tauri_evidence.py tests/test_desktop_mic_adapter_contract.py -q -p no:cacheprovider`
  - Result：`25 passed, 1 warning`。
- CLI evidence:
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_mic_adapter_implementation_boundary.py`
  - Result：`policy_validation_status=passed`、`rust_boundary_validation_status=passed`、`implementation_status=implemented_and_smoke_tested`、`safe_to_capture_audio_now=false`。
- Controlled Rust compile check:
  - `CARGO_HOME=artifacts/tmp/rust_toolchain/cargo RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target artifacts/tmp/rust_toolchain/cargo/bin/cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
  - Result：`Finished dev profile`.
- Sensitive scan:
  - Ran the configured sensitive-pattern scan over README, P0/P1 docs, RTM, decision-log, PCWEB-121 docs, tool, test, policy and Rust sources.
  - Result：无输出。

边界：

PCWEB-121 只证明 mic adapter implementation boundary 可编译、可静态校验、可提供 readiness gate 兼容 evidence。它不代表麦克风权限已请求，不代表真实麦克风已采集，不代表 audio chunk 已写入/删除，不代表 worker mic source 已批准，不代表 ASR worker real mic source 已实现，也不代表 ASR 中文技术实体质量已达标。下一步不得继续新增 mic adapter boundary/readiness wrapper，默认应转向 ASR worker real mic source 前置实现或 ASR quality exit。

## DEC-164：实现 PCWEB-122 ASR worker real mic source boundary

日期：2026-07-03

状态：Accepted

背景：

PCWEB-115 readiness gate 已经能组合 ASR quality、真实 Tauri evidence、worker mic source approval、mic adapter evidence、ASR worker evidence 和 export/feedback readiness。PCWEB-121 后 mic adapter implementation-boundary blocker 已可移除，但仍缺 ASR worker real mic source evidence。PCWEB-104 只提供 no-dispatch command runner skeleton，PCWEB-108 只证明 synthetic worker-like event output 能 closed 到 Web Live ASR session，PCWEB-112/120 只证明 mic connector 和 worker mic source approval packet，尚不能让 `_asr_worker_ready()` 返回 true。本轮按 TDD 实现 PCWEB-122，目标是补齐静态、可编译、可审计的 ASR worker mic source boundary，同时继续禁止 worker 启动、真实 mic source 执行、event/audio I/O 和远程调用。

决策：

- 新增 `code/desktop_tauri/src-tauri/src/asr_worker_mic_source_runtime.rs`，只定义 inert `AsrWorkerMicSourceBoundaryEvidence`、worker command catalog、event contract、worker output/runtime roots、`source_kind=mic` 和 all-false safety flags。
- 在 `code/desktop_tauri/src-tauri/src/lib.rs` 增加 `pub mod asr_worker_mic_source_runtime;`，不新增 Tauri command，不绑定真实 worker dispatch。
- 新增 `code/desktop_tauri/asr-worker-real-mic-source-boundary.policy.json`，固定 `implementation_status=implemented_and_smoke_tested`、`event_contract_status=partial_final_revision_error_end_of_stream_supported`、`worker_output_root=artifacts/tmp/asr_events`、`web_handoff_status=closed_to_evidence_state_gap`、`source_kind=mic`、`default_uploads_raw_audio=false`、`default_remote_asr_enabled=false` 和 all-false safety flags。
- 新增 `tools/desktop_asr_worker_real_mic_source_boundary.py`，只读取 policy/Rust source，输出 PCWEB-115 `_asr_worker_ready()` 可识别的 evidence。
- 新增 `tests/test_desktop_asr_worker_real_mic_source_boundary.py`，覆盖 TDD red/green、Rust inert boundary、tool side-effect guard、bad policy 不可放宽和 readiness blocker 移除。
- 新增 `docs/pcweb-122-asr-worker-real-mic-source-boundary-plan.md`，并更新 README、current-mainline-index、总控计划和 RTM，明确下一步不再是 ASR worker mic source boundary，而是 ASR quality exit 或显式单次 worker mic source approval。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_real_mic_source_boundary.py -q -p no:cacheprovider`
  - Result：`6 failed, 1 warning`，原因是 PCWEB-122 policy、Rust module 和 tool 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_real_mic_source_boundary.py -q -p no:cacheprovider`
  - Result：`6 passed, 1 warning`。
- Initial focused integration gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_real_mic_source_boundary.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_desktop_mic_adapter_implementation_boundary.py tests/test_desktop_worker_mic_source_from_tauri_evidence.py -q -p no:cacheprovider`
  - Result：`24 passed, 1 warning`。
- CLI evidence：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_asr_worker_real_mic_source_boundary.py`
  - Result：`policy_validation_status=passed`、`rust_boundary_validation_status=passed`、`implementation_status=implemented_and_smoke_tested`、`safe_to_spawn_worker_now=false`、`safe_to_capture_audio_now=false`。
- Controlled Rust compile check：
  - `CARGO_HOME=artifacts/tmp/rust_toolchain/cargo RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target artifacts/tmp/rust_toolchain/cargo/bin/cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
  - Result：`Finished dev profile`.

审查加固：

- 只读审查指出 PCWEB-122 方向正确，但 CLI/Rust validator/readiness gate/path guard 需要收紧。
- 新增红灯测试后：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_real_mic_source_boundary.py tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`4 failed, 13 passed, 1 warning`，覆盖 CLI Rust boundary fail exit、tool Rust forbidden snippet、Voice Memos path guard 和 PCWEB-115 full evidence shape。
- 加固实现：
  - `tools/desktop_asr_worker_real_mic_source_boundary.py` 的 CLI 支持 `--rust-module-path` / `--lib-rs-path`，退出码同时要求 policy passed、Rust boundary passed、implementation accepted。
  - Tool 的 Rust validator 同步阻断 process/file/network/audio/model/secret forbidden snippets。
  - Policy path guard 显式阻断 Voice Memos 风格路径，同时避免把敏感路径文本完整写入源码。
  - `tools/real_mic_shadow_test_readiness_gate.py` 的 `_asr_worker_ready()` 现在还要求 `source_kind=mic`、`worker_runtime_root=artifacts/tmp/desktop_asr_worker_runtime`、完整 worker command catalog、`requires_explicit_user_start=true`、`default_uploads_raw_audio=false` 和 `default_remote_asr_enabled=false`。
- 加固后 focused gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_asr_worker_real_mic_source_boundary.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_desktop_mic_adapter_implementation_boundary.py tests/test_desktop_worker_mic_source_from_tauri_evidence.py -q -p no:cacheprovider`
  - Result：`28 passed, 1 warning`。
- 加固后 controlled Rust compile check：
  - `CARGO_HOME=artifacts/tmp/rust_toolchain/cargo RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target artifacts/tmp/rust_toolchain/cargo/bin/cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`
  - Result：`Finished dev profile`.
- Sensitive/source hygiene：
  - Sensitive pattern scan over README, P0 docs, RTM, decision-log, PCWEB-122 docs, tests, tool, policy and Rust source returned no output.
  - Source capability scan over PCWEB-122 tool/Rust implementation for process/audio/network/model snippets returned no output.
  - Desktop artifact hygiene scan under `code/desktop_tauri` returned no output.

边界：

PCWEB-122 只证明 ASR worker real mic source boundary 可编译、可静态校验、可提供 readiness gate 兼容 evidence。它不代表真实 worker 已启动，不代表 `worker.prepare(source_kind=mic)` 已执行，不代表 event/audio 文件已读写，不代表麦克风权限已请求或真实音频已采集，不代表 worker mic source approval 已通过，也不代表 ASR 中文技术实体质量已达标。下一步不得继续新增 ASR worker mic source boundary/readiness wrapper，默认应转向 ASR quality exit 或显式单次 worker mic source approval。

## DEC-165：实现 PCWEB-123 worker mic source single-shadow approval evidence

日期：2026-07-03

状态：Accepted

背景：

PCWEB-114/120 已能生成 `ready_for_manual_review_not_executable` 的 worker mic source review packet，但 `worker_mic_source_approval_status` 仍为 `not_approved`。PCWEB-115 readiness gate 需要 `manually_approved_for_single_shadow_test` 才能移除 `worker_mic_source_not_approved` blocker。为避免把 approval 与真实 worker execution 混在一起，本轮按 TDD 实现 PCWEB-123：只把 caller-provided manual review packet 和显式 approval record 转为单次 shadow-test approval evidence，仍保持 `approved_to_execute_now=false` 和所有执行、音频、远程 safety flags 为 false。

决策：

- 新增 `code/desktop_tauri/worker-mic-source-single-shadow-approval.policy.json`，固定 `approval_mode=single_shadow_test_approval_evidence_only`、`required_packet_status=ready_for_manual_review_not_executable`、`approved_status=manually_approved_for_single_shadow_test`、`approval_scope=single_user_real_mic_shadow_test_only` 和固定 approval token。
- 新增 `tools/desktop_worker_mic_source_single_shadow_approval.py`，只接收 caller-provided JSON object，不读取 evidence file，不访问任何音频、worker、网络或 secret。
- Tool 要求 manual review packet report 仍是 PCWEB-114 的不可执行 review packet：packet ready、原始 status 为 `not_approved`、connector session 与 Tauri run id 匹配、worker source kind 为 `mic`、blocker 仍是 `source_kind requires later approval: mic`。
- Tool 要求 approval record 显式包含 `worker_mic_source_single_shadow_approval.v1`、安全 approval id、匹配 session id、`single_user_real_mic_shadow_test_only` scope 和固定 token `APPROVE_WORKER_MIC_SOURCE_FOR_SINGLE_SHADOW_TEST_NO_EXECUTION`。
- 合法 output 只把 `worker_mic_source_approval_status` 改为 `manually_approved_for_single_shadow_test`，但 `approved_to_execute_now=false`、`safe_to_spawn_worker_now=false`、`safe_to_capture_audio_now=false` 等 flags 全部保持 false。
- 新增 `tests/test_desktop_worker_mic_source_single_shadow_approval.py`，覆盖 no record blocked、valid evidence、wrong token/session/scope blocked、unready packet blocked、readiness blocker 只移除 worker mic source approval。
- 新增 `docs/pcweb-123-worker-mic-source-single-shadow-approval-plan.md`，并同步 README、current-mainline-index、总控计划和 RTM。PCWEB-123 后下一步不得继续做 approval wrapper，默认只剩 ASR quality exit。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_single_shadow_approval.py -q -p no:cacheprovider`
  - Result：`6 failed, 1 warning`，原因是 PCWEB-123 policy/tool 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_single_shadow_approval.py -q -p no:cacheprovider`
  - Result：`6 passed, 1 warning`。
- Focused integration gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_desktop_worker_mic_source_single_shadow_approval.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_desktop_worker_mic_source_approval.py tests/test_desktop_worker_mic_source_from_tauri_evidence.py tests/test_desktop_asr_worker_real_mic_source_boundary.py -q -p no:cacheprovider`
  - Result：`36 passed, 1 warning`。
- Default CLI without packet/approval record：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_worker_mic_source_single_shadow_approval.py`
  - Result：exit 1，`approval_evidence_status=blocked_missing_manual_review_packet`、`worker_mic_source_approval_status=not_approved`。

边界：

PCWEB-123 只证明单次 worker mic source approval evidence 机制可校验、可被 readiness gate 识别。它不代表真实 worker 已启动，不代表 `worker.prepare(source_kind=mic)` 已执行，不代表 event/audio 文件已读写，不代表麦克风权限已请求或真实音频已采集，也不代表 ASR 中文技术实体质量已达标。下一步不得继续新增 worker approval/readiness wrapper，默认应转向 ASR quality exit。

## DEC-166：扩展 DRV-032 ASR quality exit contract

日期：2026-07-03

状态：Accepted

背景：

PCWEB-119/120/121/122/123 已经分别补齐真实 Tauri no-op evidence、worker mic source bridge、mic adapter implementation boundary、ASR worker real mic source boundary 和单次 worker mic source approval evidence 机制。继续新增同类 readiness/report-only wrapper 会偏离主线。当前真正阻塞仍是 ASR quality：默认 `requires_funasr_model_dir_or_drv019_approval`，原因是 sherpa 中文技术实体召回不足且 FunASR runtime cache/local model dir 缺失。为停止“无限评估循环”，本轮不新建平行质量门，而是扩展现有 DRV-032，让它输出机器可读的 quality exit contract，并让 PCWEB-115 能消费显式降级试点风险接受。

决策：

- `tools/asr_quality_decision_gate.py` 稳定输出 `quality_exit_status`、`recommended_quality_exit_path_id`、`default_cost_policy`、`quality_exit_options`、`mainline_stop_conditions`、`degraded_pilot_acceptance_status`、`degraded_pilot_acceptance_errors`、`can_unblock_real_mic_shadow_test_quality_gate` 和 `counts_as_asr_quality_go_evidence`。
- 默认状态仍是 `decision_status=requires_funasr_model_dir_or_drv019_approval`、`quality_exit_status=not_exited`、`can_unblock_real_mic_shadow_test_quality_gate=false`。
- 四个出口固定为：
  - `verified_local_funasr_model_dir`：默认推荐，无额外 provider 费用；模型目录就绪后仍需一次 synthetic smoke 审批和 batch gate。
  - `drv019_manual_model_download`：需要用户显式批准，无额外 provider 费用但有约 840MB 模型下载/磁盘成本。
  - `optional_remote_asr_comparison`：默认禁用，有额外 provider 费用，只能作为显式质量对照。
  - `explicit_degraded_pilot_acceptance`：默认禁用，无额外 provider 费用；只允许一次 shadow-test timing/feedback 验证，不算 ASR quality Go evidence。
- 新增 caller-provided `asr_quality_degraded_pilot_acceptance.v1` 校验，不读取文件、不读取 `configs/local`，要求固定 acceptance id、scope、token、risk list 和 operator note。
- 合法降级接受时，DRV-032 输出 `decision_status=degraded_pilot_accepted_with_quality_risk`、`quality_exit_status=degraded_pilot_accepted_with_quality_risk`、`can_unblock_real_mic_shadow_test_quality_gate=true`、`counts_as_asr_quality_go_evidence=false`。
- `tools/real_mic_shadow_test_readiness_gate.py` 的 `_asr_quality_ready()` 现在接受两类质量前置：严格 `asr_quality_current_gate_not_blocking`，或合法 `degraded_pilot_accepted_with_quality_risk`。后一类必须同时满足 `degraded_pilot_acceptance_status=accepted_for_single_shadow_test_quality_risk`、`can_unblock_real_mic_shadow_test_quality_gate=true`、`counts_as_asr_quality_go_evidence=false`、非工程 candidate 为 0 和所有 upstream safety flags 安全。
- 所有 ASR gate 和 readiness gate safety flags 继续保持 false；本变更不访问麦克风、不读取真实音频、不启动 worker、不运行 Cargo/Tauri、不下载模型/公开音频、不调用远程 ASR/LLM。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`4 failed, 12 passed, 1 warning`。
  - 失败原因：DRV-032 尚未输出 quality exit 字段或接受 `degraded_pilot_acceptance`，PCWEB-115 尚不接受 degraded pilot quality precondition。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`16 passed, 1 warning`。
- Default CLI：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
  - Result：exit `1`，`decision_status=requires_funasr_model_dir_or_drv019_approval`、`quality_exit_status=not_exited`、`degraded_pilot_acceptance_status=not_requested`、`can_unblock_real_mic_shadow_test_quality_gate=false`。
- Focused integration gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_desktop_worker_mic_source_single_shadow_approval.py tests/test_desktop_worker_mic_source_approval.py tests/test_desktop_worker_mic_source_from_tauri_evidence.py tests/test_desktop_asr_worker_real_mic_source_boundary.py tests/test_desktop_mic_adapter_implementation_boundary.py -q -p no:cacheprovider`
  - Result：`50 passed, 1 warning`。
- Default real mic readiness CLI：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_readiness_gate.py`
  - Result：exit `1`，`readiness_status=blocked_not_ready_for_user_real_mic_shadow_test`、`user_can_start_real_mic_shadow_test_now=false`、`asr_quality_exit_status=not_exited`。

边界：

DEC-166 不证明 ASR 中文技术实体质量达标，不证明真实麦克风可以自动开始，也不授权任何下载、远程调用或音频采集。它只把未来路径收束到四个可审计出口，并允许用户在知情风险下选择一次降级 shadow-test 验证桌面时序和反馈闭环；该结果不得作为 ASR quality Go evidence。

## DEC-167：补齐 PCWEB-115 CLI evidence input

日期：2026-07-03

状态：Accepted

背景：

PCWEB-115 已经能通过 Python 参数组合 DRV-032、PCWEB-119/120/121/122/123 和 DRV-033/036/037/038/039 evidence，但 CLI 默认只能使用内部 default reports，无法直接消费已存在的 evidence JSON。这会让真实 shadow-test readiness 的可执行证据链断在命令行入口：即使已有 ignored artifact evidence，CLI 仍可能只显示 `real_tauri_noop_run_result_not_provided`、`worker_mic_source_not_approved`、`mic_adapter_real_implementation_not_available` 和 `asr_worker_real_mic_source_not_available` 等默认 blocker。

决策：

- 在 `tools/real_mic_shadow_test_readiness_gate.py` 中新增 evidence input contract：每类 evidence 同时支持 `--*-json` inline JSON 和 `--*-path` JSON file path，且二者不能同时提供。
- Evidence path 只允许 approved `artifacts/tmp/**` 下的 `.json` 文件；在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。
- `_tauri_noop_ready()` 接受 PCWEB-119 capture evidence shape：`capture_status=captured_validated_tauri_noop_run`、`desktop_tauri_noop_webview_run_capture.v1`、validation passed、10 个 command returned、Tauri WebView run result 和 all-false safety flags。
- CLI 报告新增 `evidence_input_status=loaded/default_reports_only/blocked` 和 `evidence_input_errors`，便于区分“没有输入证据”和“输入证据被路径守卫阻断”。
- 该变更只加载 caller-provided JSON object，不访问麦克风、不读取真实音频、不启动 worker、不运行 Cargo/Tauri、不读取 `configs/local`、不调用远程 ASR/LLM、不下载模型或公开音频。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`3 failed, 9 passed, 1 warning`。
  - 失败原因：CLI 尚不识别 `--asr-quality-report-json`、`--tauri-noop-evidence-json/path` 等参数，且 `_tauri_noop_ready()` 尚不接受 PCWEB-119 capture evidence shape。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`12 passed, 1 warning`。

边界：

DEC-167 不代表真实麦克风 shadow test 可以开始，不代表 ASR quality 已达标，也不代表 worker 或 mic adapter 会执行。它只把 PCWEB-115 readiness CLI 从“只能看默认报告”推进到“能从 approved ignored artifact evidence 计算 readiness”，并继续保持所有执行、音频、远程和 secret safety flags 为 false。

## DEC-168：记录新增公开/技术域音频候选但不扩大自动白名单

日期：2026-07-03

状态：Accepted

背景：

用户再次确认：转写类验证应由我先通过网上公开音频和模拟完成，最终真实麦克风会议由用户验证。并行网络研究确认，除 AliMeeting/AISHELL-4/AISHELL-1 外，MISP-Meeting、PyCon China、QCon/InfoQ 等更贴近真实会议或真实技术词，但其许可和授权边界不适合直接进入商业 MVP 自动评测。

决策：

- 默认自动白名单保持不变：只包含 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33，且全部保持 `default_download_enabled=false`。
- 新增 observed-only 候选记录：
  - MISP-Meeting：真实普通话多人会议候选，但 non-commercial research license，不进入商业 MVP 自动评测。
  - PyCon China：真实中文技术会议域候选，但公开可观看不等于可抽音频、生成转写或商用评测；需要书面授权和人工标注计划。
  - QCon/InfoQ：真实中文架构/工程实践域候选，但属于平台/课程内容，且可能有讲者或企业发布限制；需要书面授权和人工标注计划。
- 这些候选只用于未来授权路线和域内验证集规划，不触发下载、抽样、转码、ASR、LLM 或产品价值 gate。

验证方式：

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py -q -p no:cacheprovider`
- 预期：白名单 source ids 仍严格等于 AliMeeting/AISHELL-4/AISHELL-1；observed candidates 必须全部 `default_download_enabled=false`、`raw_audio_committed_to_repo=false`、`product_value_validation_allowed=false`。

边界：

DEC-168 不授权抓取 Bilibili、YouTube、播客、公开课、技术大会录播或平台课程音频，不授权下载 MISP/PyCon/QCon/InfoQ 音频，不授权生成转写或派生数据。公开音频阶段仍以 no-download manifest 和人工审批为硬边界。

## DEC-169：补齐 DRV-032 degraded pilot acceptance CLI input

日期：2026-07-03

状态：Accepted

背景：

DRV-032 已经支持 Python caller 传入 `asr_quality_degraded_pilot_acceptance.v1`，并能输出 `degraded_pilot_accepted_with_quality_risk`。但 CLI 默认只能运行内部 default reports，无法从 ignored artifact evidence 中读取显式降级试点接受记录。这样 PCWEB-115 虽然已经能消费 DRV-032 的降级质量前置，命令行证据链仍断在 ASR quality gate。

决策：

- `tools/asr_quality_decision_gate.py` 新增 `--degraded-pilot-acceptance-json` 和 `--degraded-pilot-acceptance-path`。
- 两种输入互斥；inline JSON 必须是 object，path JSON 必须是 object。
- Path 只允许 approved `artifacts/tmp/**` 下的 `.json` 文件。
- Path guard 在读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。
- CLI 报告新增 `acceptance_input_status=not_requested/loaded/blocked` 和 `acceptance_input_errors`。
- 合法 degraded pilot acceptance 仍不把 ASR 质量标记为 Go，只允许 PCWEB-115 继续检查其它真实会议前置条件。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`2 failed, 7 passed, 1 warning`。
  - 失败原因：CLI 尚不识别 `--degraded-pilot-acceptance-json` / `--degraded-pilot-acceptance-path`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`9 passed, 1 warning`。
- PCWEB-115 regression：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider`
  - Result：`12 passed, 1 warning`。

边界：

DEC-169 不访问麦克风，不读取真实音频，不读取 `configs/local`，不调用远程 ASR/LLM，不下载模型或公开音频，不启动 worker，不运行 Cargo/Tauri。显式降级接受只是一条单次 shadow-test timing/feedback 风险接受路径，`counts_as_asr_quality_go_evidence=false`。

## DEC-170：实现 PCWEB-124 real mic shadow-test readiness API/UI

日期：2026-07-04

状态：Accepted

背景：

用户再次确认：完整计划已经写下，转写类验证需要我先通过网上官方公开音频来源和本地模拟完成，最终真实麦克风会议由用户验证。PCWEB-115 已有真实麦克风 shadow-test readiness gate，但 Web 工作台没有直接展示该 gate 的默认 blockers。继续只在文档或 CLI 里看 readiness，容易让后续开发误以为主线又陷入评估循环，或者误判真实会议已经可以开始。

决策：

- 新增 Web backend `GET /desktop/real-mic-shadow-test-readiness`。
- Endpoint 只调用 PCWEB-115 `build_real_mic_shadow_test_readiness_report()` 默认静态报告，不读取 evidence path。
- Web 工作台新增 `desktop-real-mic-shadow-readiness-panel`，展示：
  - readiness status。
  - ASR quality exit status 和 Go evidence 标记。
  - Tauri、worker mic source、mic adapter、ASR worker、export/feedback 前置状态。
  - blockers。
  - pilot protocol。
  - 全部 safety flags。
- PCWEB-124 不是新 gate，只是把已有 PCWEB-115 go/no-go 可见化；默认仍显示 `blocked_not_ready_for_user_real_mic_shadow_test`。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_reports_static_gate_without_audio_access code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_does_not_probe_audio_or_read_secrets code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_with_data_dir_does_not_create_local_storage code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result：`5 failed, 2 warnings`。
  - 失败原因：endpoint 404、HTML 缺 `desktop-real-mic-shadow-readiness-panel`、JS 缺 `loadDesktopRealMicShadowTestReadiness`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_reports_static_gate_without_audio_access code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_does_not_probe_audio_or_read_secrets code/web_mvp/backend/tests/test_app.py::test_desktop_real_mic_shadow_test_readiness_with_data_dir_does_not_create_local_storage code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result：`5 passed, 2 warnings`。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/pcweb-124-real-mic-shadow-test-readiness-api-ui-plan.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-170 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不读取 evidence 文件，不读取 `configs/local`，不创建 `sessions`、`live_asr_sessions` 或 `real_mic_shadow_reports`，不启动 worker，不运行 Cargo/Tauri，不调用远程 ASR/LLM，不下载模型或公开音频。它只让当前 readiness blocker 可见；真实麦克风会议仍必须等待 readiness gate 返回 ready 后，由用户显式启动。

复审条件：

如果后续引入真实 evidence path 或用户启动真实 shadow test，必须另开 TDD/decision 记录，明确 evidence allowed roots、UI user action、audio retention/delete、worker start 和 LLM/remote ASR 成本边界。

## DEC-171：实现 DRV-041 simulated shadow pipeline smoke runner

日期：2026-07-04

状态：Accepted

背景：

用户确认：完整计划已经写下，转写验证需要我先通过网上官方公开音频来源和本地模拟完成，最终真实麦克风会议由用户验证。两个只读审查 Agent 的结论一致：当前缺口不是继续写计划，而是执行态证据；下一步应把已有 approved ASR event replay、shadow report draft 和 export preview 串成一条可复跑的模拟产品链路，而不是继续新增泛化 readiness/report-only wrapper。

决策：

- 新增 `tools/simulated_shadow_pipeline_smoke.py`，作为 DRV-041 纯内存 smoke runner。
- Runner 只编排已有 builder：
  - `asr_live_pipeline_replay.build_asr_live_pipeline_replay_report`
  - `replay_shadow_report_draft_adapter.build_replay_shadow_report_draft`
  - `shadow_report_ingestion_export_feedback.build_shadow_report_ingestion_export_feedback`
- 成功时输出 `pipeline_status=simulated_shadow_pipeline_preview_created`、`export_readiness_status=draft_export_preview_only`、`go_evidence_status=not_go_evidence_replay_or_feedback_missing`。
- 非工程 control 或没有 candidate timeline 时输出 `blocked_by_no_candidate_timeline`，不伪造 candidate report、suggestion card 或 export preview。
- 可选 event provenance manifest 只用于保留 `public_audio_sample` / `synthetic_audio` / `mock_streaming` 来源审计，不触发公开音频下载。
- CLI 只有成功 preview 时 exit 0；blocked 状态 exit 1。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py -q -p no:cacheprovider`
  - Result：`5 failed, 1 warning`。
  - 失败原因：`tools/simulated_shadow_pipeline_smoke.py` 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py -q -p no:cacheprovider`
  - Result：`5 passed, 1 warning`。

影响范围：

- `tools/simulated_shadow_pipeline_smoke.py`
- `tests/test_simulated_shadow_pipeline_smoke.py`
- `docs/drv-041-simulated-shadow-pipeline-smoke-plan.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-171 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk，不写真实导出文件。它只证明模拟转写事件能进入产品价值 preview，不代表 ASR quality 达标、真实会议 ready 或产品 Go。

后续：

下一步不得继续新增同类 smoke/readiness 包装器。主线只能继续向 ASR quality exit、明确降级试点风险接受，或用户最终真实麦克风 shadow test 前置准备推进。

## DEC-172：实现 DRV-042 simulated shadow pipeline batch smoke

日期：2026-07-04

状态：Accepted

背景：

DRV-041 已把单个 approved ASR event JSON 纯内存串到 replay、shadow draft 和 export preview。为了避免只看单场景造成误判，需要把 4 个工程 mock 场景和 1 个非工程负控变成一键批量门：工程场景必须形成 preview，非工程 control 必须保持 no candidate。

决策：

- 在 `tools/simulated_shadow_pipeline_smoke.py` 中新增 `build_simulated_shadow_pipeline_batch_smoke(...)`。
- 新增 CLI flag `--batch-default-mock-events`。
- 默认场景固定为：
  - `api-review-001`
  - `architecture-review-001`
  - `incident-review-001`
  - `release-review-001`
  - `non-engineering-control-001`
- batch 通过条件：
  - 4 个工程场景全部 `pipeline_status=simulated_shadow_pipeline_preview_created` 且 `candidate_cards>0`。
  - 非工程 control `pipeline_status=blocked_by_no_candidate_timeline` 且 `candidate_cards=0`。
  - `negative_control_fake_candidate_count=0`。
- batch 输出 `go_evidence_status=not_go_evidence_batch_replay_or_feedback_missing`，明确它不是真实 Go evidence。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py -q -p no:cacheprovider`
  - Result：`4 failed, 5 passed, 1 warning`。
  - 失败原因：`build_simulated_shadow_pipeline_batch_smoke` 和 `--batch-default-mock-events` 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py -q -p no:cacheprovider`
  - Result：`9 passed, 1 warning`。
- Real local artifact smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events`
  - Expected：`scenario_count=5`、`engineering_preview_created_count=4`、`negative_control_blocked_count=1`、`negative_control_fake_candidate_count=0`、`artifact_write_status=not_written`。

影响范围：

- `tools/simulated_shadow_pipeline_smoke.py`
- `tests/test_simulated_shadow_pipeline_smoke.py`
- `docs/drv-042-simulated-shadow-pipeline-batch-smoke-plan.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-172 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk，不写真实导出文件。它只证明 mock 层产品价值链路具备批量自测，不代表 ASR quality 达标、真实会议 ready 或产品 Go。

后续：

下一步不得继续扩展 mock smoke。主线应回到 ASR quality exit：本地 FunASR 模型目录、DRV-019 审批、显式远端 ASR 对照，或显式降级试点风险接受。

## DEC-173：ASR quality gate 消费 DRV-042 batch status 并落当前执行报告

日期：2026-07-04

状态：Accepted

背景：

用户再次确认：完整计划必须写下来，转写由我先通过网上官方公开音频和模拟完成，最终真实麦克风会议由用户验证。DRV-041/042 已证明 mock/approved ASR events 可以闭合到产品价值 preview，但 ASR quality gate 仍需要显式消费这个 batch 状态，否则后续可能继续把“产品模拟链路是否可用”和“真实 ASR provider 质量是否达标”混在一起。

决策：

- `tools/asr_quality_decision_gate.py` 消费 DRV-042 `simulated_shadow_pipeline_batch_smoke`。
- 当 DRV-042 batch 未通过时，ASR quality gate 返回 `fix_simulated_shadow_pipeline_first`，blocked reasons 固定为：
  - `simulated_shadow_pipeline_batch_not_passed`
  - `do_not_blame_asr_provider_until_mock_shadow_pipeline_gate_recovers`
- 当 DRV-042 batch 通过时，ASR quality gate 继续按真实 ASR 质量出口判断；当前默认仍为 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。
- 新增 `docs/current-plan-and-validation-report-2026-07-04.md`，集中记录当前完整计划、网上官方公开音频来源、模拟转写执行态、真实麦克风用户最终验证边界和本轮验证命令。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`2 failed, 8 passed, 1 warning`。
  - 失败原因：`build_asr_quality_decision_gate_report()` 尚不接受 `simulated_shadow_batch_report`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`10 passed, 1 warning`。
- Integrated gate：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_simulated_shadow_pipeline_smoke.py tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result：`56 passed, 1 warning`。
- CLI sanity：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events`
  - Result：exit `0`，`batch_status=simulated_shadow_pipeline_batch_passed`、`engineering_preview_created_count=4`、`negative_control_fake_candidate_count=0`、`artifact_write_status=not_written`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
  - Result：exit `1`，`decision_status=requires_funasr_model_dir_or_drv019_approval`、`quality_exit_status=not_exited`、`simulated_shadow_batch_status=simulated_shadow_pipeline_batch_passed`、`public_audio_decision_status=blocked_no_verified_public_sample_manifest`。

影响范围：

- `tools/asr_quality_decision_gate.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `README.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-173 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk，不写真实导出文件。

后续：

下一步不得继续新增同类计划、readiness 或 mock smoke 包装器。主线只能在三个出口中选择：ASR quality exit、public audio bounded clip manifest、或用户显式降级试点后进入最终真实麦克风 shadow-test 准备。

## DEC-174：实现 DRV-043 FunASR local readiness evidence input

日期：2026-07-04

状态：Accepted

背景：

DRV-032 已把 ASR quality exit 收束到本地 FunASR、DRV-019、显式远端 ASR 对照或显式降级试点。DRV-041/042 和 DEC-173 已证明产品 mock 链路可过 batch，当前剩余主阻塞是 FunASR 本地模型目录或 DRV-019。为了继续推进主线且不新增费用、不下载模型，需要先让“本地模型/cache 已预检”的 evidence 能进入 ASR quality gate。

决策：

- `tools/funasr_synthetic_smoke_readiness.py` CLI 新增 `--model-cache-root`。
- 显式 model cache root 只检查必需模型文件存在，不读取模型内容，不运行 FunASR，不下载模型。
- 报告新增 `model_cache_root_input_status`，且不回显本机绝对路径。
- model cache root 如果位于 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 或音频路径，会在读取模型组件前 blocked。
- `tools/asr_quality_decision_gate.py` CLI 新增 `--funasr-readiness-path`。
- FunASR readiness path 只允许 approved `artifacts/tmp/**` JSON，并在读取前阻断 forbidden roots、仓库外路径、`.m4a` 和非 JSON。
- 合法 ready evidence 只把 DRV-032 推进到 `funasr_cache_preflight_ready_requires_execution_approval`，仍不执行 synthetic smoke。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_readiness.py -q -p no:cacheprovider`
  - Result：`2 failed, 6 passed, 1 warning`。
  - 失败原因：CLI 不支持显式 model cache root，forbidden model root 未预读阻断。
- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`2 failed, 10 passed, 1 warning`。
  - 失败原因：CLI 不识别 `--funasr-readiness-path`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_readiness.py -q -p no:cacheprovider`
  - Result：`8 passed, 1 warning`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`12 passed, 1 warning`。

影响范围：

- `tools/funasr_synthetic_smoke_readiness.py`
- `tools/asr_quality_decision_gate.py`
- `tests/test_funasr_synthetic_smoke_readiness.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/drv-043-funasr-local-readiness-evidence-input-plan.md`
- `docs/asr-quality-decision-gate-2026-07-03.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-174 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

如果后续本机已经有合法 FunASR 模型目录，先用 DRV-043 生成 readiness evidence，再用 DRV-032 消费该 evidence。只有 DRV-032 输出 `funasr_cache_preflight_ready_requires_execution_approval` 后，才另开一次明确审批去跑 synthetic smoke；该 smoke 通过后仍需复跑 batch gate，才能作为 ASR quality Go evidence 的候选。

## DEC-175：锁定 DRV-044 为下一轮唯一主线票并记录开源基座决策

日期：2026-07-04

状态：Accepted

背景：

用户再次确认：完整计划必须写下来；转写验证由我先通过网上官方公开音频来源和本地模拟完成；最终真实麦克风会议由用户验证。只读对抗审查结论是：当前文档已经形成主线级完整计划，产品初心没有丢，但如果不写死下一轮唯一主线票，后续仍可能继续在 ASR readiness、smoke 和 report 外围打圈。

决策：

- 下一轮唯一主线票锁定为 `DRV-044 FunASR synthetic smoke result evidence schema/gate`。
- DRV-044 只定义未来本地 FunASR synthetic smoke result 的 evidence schema、硬阈值和 gate 结果；它不运行 ASR、不下载模型、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM。
- DRV-044 的 first-pilot ASR quality 硬阈值固定为：
  - 4 个工程 synthetic scenarios 的 normalized technical entity recall 均 `>=0.80`。
  - raw recall 和 normalized recall 必须分开记录。
  - non-engineering control 的 engineering state/candidate/card 必须全部为 `0`。
  - first partial latency p95 `<=2.0s`，final latency p95 `<=8.0s`，ASR RTF `<=0.60`，suggestion candidate latency p95 `<=30.0s`。
  - 每张 candidate/card 必须能追溯到 EvidenceSpan。
- 单次 FunASR synthetic smoke 只能成为 `quality_candidate_requires_batch_confirmation`，不能直接成为真实会议 Go evidence。
- 公开音频阶段继续保持 no-download：除非用户提供合法 planned samples 或明确批准 GB 级公开包下载，否则公开音频不再作为下一主线。
- 开源基座决策：不 fork 某个会议助手开源项目作为二开基座；采用自建薄主线，复用 Tauri、FastAPI、本地 ASR provider、FunASR/sherpa 和 OpenAI-compatible LLM 协议这些成熟组件。未来如果发现 500+ star 项目有可复用模块，只能作为 provider/adapter，不替代本项目的 EvidenceSpan、工程 gap/card、反馈和真实 shadow-test 主线。
- PC 交付路线固定为 Mac local shadow MVP、Mac AI pilot、Mac private beta、Windows Phase 2；Windows 不与 Mac MVP 并行抢主线。

影响范围：

- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `README.md`
- `docs/decision-log.md`

边界：

DEC-175 只修改文档和决策记录，不访问麦克风，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

下一轮如果进入实现，必须按 TDD 做 DRV-044：先写失败用例，验证 red，再实现 `funasr_synthetic_smoke_result_evidence` gate，最后更新 DRV-032/RTM/decision-log 并跑敏感信息扫描。不能再新增同类泛化 readiness 或 report-only wrapper。

## DEC-176：实现 DRV-044 FunASR synthetic smoke result evidence gate

日期：2026-07-04

状态：Accepted

背景：

DEC-175 已锁定下一轮唯一主线票为 DRV-044。DRV-043 只能证明本地 FunASR 模型/cache readiness 可被接入 DRV-032，但 readiness 不是 ASR 质量证据。为了防止后续真的跑出 FunASR 文本后继续争论“怎么算通过”，本轮把 smoke result 的 schema、硬阈值、单场景候选和 batch confirmation 出口实现成机器可验收 gate。

决策：

- 新增 `tools/funasr_synthetic_smoke_result_evidence.py`。
- 新增 `tests/test_funasr_synthetic_smoke_result_evidence.py`。
- `funasr_synthetic_smoke_result.v1` 只接受 caller-provided JSON，不运行 ASR、不读取音频、不下载模型。
- approved report path 限定为 `artifacts/tmp/asr_reports/**.json`；path guard 在读取前阻断 forbidden roots、仓库外路径、`.m4a` 和非 JSON。
- 单场景 `single_synthetic_smoke` 通过时输出 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`，`counts_as_asr_quality_go_evidence=false`。
- Batch `batch_synthetic_confirmation` 通过时输出 `funasr_synthetic_smoke_quality_batch_confirmed`，`counts_as_asr_quality_go_evidence=true`，但 `counts_as_real_mic_go_evidence=false`。
- Batch 硬阈值：4 个工程 synthetic scenarios、1 个 non-engineering control、工程 normalized technical entity recall 均 `>=0.80`、first partial p95 `<=2.0s`、final p95 `<=8.0s`、RTF `<=0.60`、suggestion candidate p95 `<=30.0s`、每张卡可追溯 EvidenceSpan、非工程 control candidate/card 为 0。
- `tools/asr_quality_decision_gate.py` 接受 `funasr_smoke_result_report` 和 `--funasr-smoke-result-path`。
- DRV-032 对单场景 candidate 输出 `funasr_smoke_candidate_requires_batch_confirmation` / `quality_exit_status=not_exited`；对 batch confirmed 输出 `asr_quality_current_gate_not_blocking` / `strict_quality_gate_not_blocking`；blocked result 先修 evidence。
- `--funasr-smoke-result-path` 必须消费 DRV-044 gate report，拒绝 raw unvalidated smoke JSON。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`9 failed, 12 passed, 1 warning`。
  - 失败原因：`tools/funasr_synthetic_smoke_result_evidence.py` 不存在；`build_asr_quality_decision_gate_report()` 不接受 `funasr_smoke_result_report`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`23 passed, 1 warning`。
- Integrated：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_funasr_synthetic_smoke_readiness.py tests/test_asr_quality_decision_gate.py tests/test_simulated_shadow_pipeline_smoke.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result：`77 passed, 1 warning`。
- Additional input-guard red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`1 failed, 15 passed, 1 warning`。
  - 失败原因：`--funasr-smoke-result-path` 会接受未经 DRV-044 gate 验证的 raw smoke JSON。

影响范围：

- `tools/funasr_synthetic_smoke_result_evidence.py`
- `tools/asr_quality_decision_gate.py`
- `tests/test_funasr_synthetic_smoke_result_evidence.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/drv-044-funasr-synthetic-smoke-result-evidence-gate-plan.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-176 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

DRV-044 已经把“未来本地 FunASR smoke result 怎么算过”固定下来，但还没有执行真实 FunASR smoke。下一步只能在已有本地 FunASR 模型目录、DRV-019 手动模型下载审批、或显式降级试点风险接受之间选择；没有这些输入时，真实麦克风 shadow test 仍保持 blocked。

## DEC-177：加固 DRV-044 batch artifact provenance and hash gate

日期：2026-07-04

状态：Accepted

背景：

DEC-176 已完成 DRV-044 evidence gate，但 batch confirmation 仍有一个边界风险：如果只校验 caller-provided JSON 的指标字段，手工构造的 fixture-like `batch_synthetic_confirmation` 也可能让 DRV-032 误以为 ASR quality 已严格退出。为了避免“看起来像结果”的 JSON 替代真实本地 smoke artifact，本轮加固 batch provenance 和 hash 校验。

决策：

- `batch_synthetic_confirmation` 必须提供 `batch_artifact_provenance`。
- `batch_artifact_provenance.source_kind` 必须为 `local_funasr_synthetic_smoke_artifacts`；`fixture_only` 必须 blocked。
- 每个 scenario 必须绑定一个 approved JSON artifact，路径只允许位于 `artifacts/tmp/asr_reports/**.json` 或 `artifacts/tmp/asr_events/**.json`。
- 工具会在读取 artifact 前阻断 forbidden roots、仓库外路径、`.m4a` 和非 JSON。
- 工具只读取 artifact bytes 计算 sha256；不运行 ASR、不读取音频、不下载模型、不调用远程 provider。
- sha256 不匹配、artifact 缺失、artifact 未覆盖所有 scenario 或 path 越界时，DRV-044 必须输出 `quality_evidence_status=blocked`、`counts_as_asr_quality_go_evidence=false`。
- DRV-032 只在 `funasr_synthetic_smoke_quality_batch_confirmed` 且 `batch_artifact_provenance_status=validated` 时允许 `asr_quality_current_gate_not_blocking`；缺少 validated provenance 时返回 `fix_funasr_smoke_result_evidence_first`。

验证方式：

- Provenance hardening red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py -q -p no:cacheprovider`
  - Result：`4 failed, 6 passed, 1 warning`。
  - 失败原因：batch 缺少 artifact provenance 仍会通过，`fixture_only` 未阻断，sha256 未校验，输出缺少 `batch_artifact_provenance_status`。
- ASR gate hardening red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py::test_asr_quality_decision_rejects_batch_confirmed_funasr_smoke_without_validated_provenance -q -p no:cacheprovider`
  - Result：`1 failed, 1 warning`。
  - 失败原因：DRV-032 会接受缺少 validated provenance 的 batch-confirmed report。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`27 passed, 1 warning`。
- Integrated：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_funasr_synthetic_smoke_readiness.py tests/test_asr_quality_decision_gate.py tests/test_simulated_shadow_pipeline_smoke.py tests/test_real_mic_shadow_test_readiness_gate.py tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`
  - Result：`81 passed, 1 warning`。

影响范围：

- `tools/funasr_synthetic_smoke_result_evidence.py`
- `tools/asr_quality_decision_gate.py`
- `tests/test_funasr_synthetic_smoke_result_evidence.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/drv-044-funasr-synthetic-smoke-result-evidence-gate-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-177 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

DRV-044 batch confirmed 现在只能作为“带 provenance/hash 的本地 synthetic smoke evidence”进入 DRV-032。它仍不是真实麦克风 Go evidence；没有真实 batch artifact、合法本地 FunASR 模型目录/DRV-019 审批或显式降级试点接受时，真实麦克风 shadow test 继续 blocked。

## DEC-178：确认完整计划短入口、网上官方音频复核和模拟转写责任边界

日期：2026-07-04

状态：Accepted

背景：

用户再次确认：完整计划是否已经写下来；转写类验证需要我先通过网上寻找公开音频和模拟完成；最终真实麦克风会议由用户验证。当前仓库已有 P0/P1 总控计划，但这些内容分散在多个长文档中，后续执行容易再次陷入“是不是还要继续评估”的循环。因此本轮把计划短入口、联网来源复核和执行责任边界再次写成一个单独可引用的 execution lock。

决策：

- 新增 `docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`，作为短入口回答“完整计划已写下，转写由我先做公开官方来源复核和本地模拟，真实麦克风由用户最终验证”。
- 本轮联网复核 OpenSLR 官方页面后，默认公开音频白名单仍只保留：
  - AliMeeting / OpenSLR SLR119：Mandarin multi-channel meeting speech corpus，CC BY-SA 4.0，Eval 包约 3.42G。
  - AISHELL-4 / OpenSLR SLR111：conference scenario Mandarin multi-channel meeting corpus，CC BY-SA 4.0，test 包约 5.2G。
  - AISHELL-1 / OpenSLR SLR33：Mandarin speech corpus，Apache License v2.0，speech data 包约 15G，只做普通话 sanity。
- MagicData-RAMC / OpenSLR SLR123 因 CC BY-NC-ND 4.0 和非会议主集属性继续保持 `observed_but_not_whitelisted`，不进入自动下载、抽样、转码、ASR 或产品价值 gate。
- 公开音频阶段继续只允许 no-download manifest；没有 `archive_member_path`、clip start/end、expected sha256、license citation 和 cleanup manifest 前必须保持 `blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`。
- 模拟转写继续使用本地中文技术会议合成/Mock events 和 `tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events`，用来验证 EvidenceSpan、state/gap、candidate-card 和负控 0 fake candidate。
- 真实麦克风会议仍由用户最后执行；当前默认 readiness 仍应保持 `blocked_not_ready_for_user_real_mic_shadow_test`，主因是 ASR quality 未退出。

影响范围：

- `docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/decision-log.md`
- `docs/requirements-traceability-matrix.md`

边界：

DEC-178 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

下一步不得继续泛搜更多音频网站或新增同类计划文档来替代进展。主线只允许在三个出口推进：ASR quality exit、AliMeeting/AISHELL-4 bounded clip manifest、或用户显式降级试点风险接受后进入真实麦克风 shadow-test 准备。

## DEC-179：实现 DRV-045 FunASR synthetic smoke execution packet

日期：2026-07-04

状态：Accepted

背景：

DRV-043 已能证明本地 FunASR readiness，但 readiness 不是 ASR quality evidence。DRV-044 已能验证 caller-provided FunASR smoke result 的质量阈值、provenance 和 sha256，但不会告诉后续执行者应该怎么从 readiness 产出这批 artifacts。为了避免本地模型目录就绪后再次争论“跑哪些场景、产物放哪里、sha256 怎么交给 DRV-044”，本轮实现 DRV-045 执行包。

决策：

- 新增 `tools/funasr_synthetic_smoke_execution_packet.py`。
- 新增 `tests/test_funasr_synthetic_smoke_execution_packet.py`。
- 新增 `docs/drv-045-funasr-synthetic-smoke-execution-packet-plan.md`。
- DRV-045 只消费 DRV-043 readiness evidence；readiness 必须是 `funasr_synthetic_smoke_readiness.v1`，状态为 `cache_preflight_passed_offline_execution_not_proven`，且 `required_cached_models_status=present`。
- DRV-045 默认 5 场景：`api-review-001`、`architecture-review-001`、`incident-review-001`、`release-review-001`、`non-engineering-control-001`。
- 合法 packet 输出 `ready_for_manual_batch_funasr_synthetic_smoke_run`、5 个 command preview、expected output paths 和 `expected_drv044_batch_artifact_provenance` template。
- `expected_drv044_batch_artifact_provenance.source_kind` 固定为 `local_funasr_synthetic_smoke_artifacts`；每个 artifact 的 `sha256_source` 是 `compute_after_manual_run`，不能伪造为已验证 sha256。
- readiness path 只允许 approved `artifacts/tmp/**.json`，读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、`.m4a` 和仓库外路径。
- readiness safety flags 只要出现远程 ASR、LLM、用户音频、模型下载或 configs/local 访问意图，packet 必须 blocked。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_packet.py -q -p no:cacheprovider`
  - Result：`6 failed, 1 warning`。
  - 失败原因：`tools/funasr_synthetic_smoke_execution_packet.py` 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_packet.py -q -p no:cacheprovider`
  - Result：`6 passed, 1 warning`。

影响范围：

- `tools/funasr_synthetic_smoke_execution_packet.py`
- `tests/test_funasr_synthetic_smoke_execution_packet.py`
- `docs/drv-045-funasr-synthetic-smoke-execution-packet-plan.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-179 不运行 ASR，不读取音频，不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不调用远程 ASR/LLM，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk 或 ASR artifacts。

后续：

DRV-045 让 ASR quality exit 的本地路径变成可执行但不自动执行的顺序：先 DRV-043 readiness，再 DRV-045 packet，再人工执行 FunASR synthetic smoke，之后用 DRV-044 验证 batch artifact provenance/hash，最后由 DRV-032 决定是否退出 ASR quality blocker。没有 readiness/model 证据时，真实麦克风 shadow test 仍保持 blocked。

## DEC-180：实现 DRV-046 FunASR synthetic smoke batch evidence assembler

日期：2026-07-04

状态：Accepted

背景：

DRV-045 已能生成未来手动 FunASR synthetic smoke 的 5 场景 command preview、expected output paths 和 DRV-044 provenance template。但手动执行后仍需要一个机器化交接步骤：读取 5 个 smoke report artifacts、计算 sha256、合并 scenario_results、组装 `batch_synthetic_confirmation`，并交给 DRV-044 gate 验证。否则后续可能手工拼错 artifact path、sha256 或 scenario set。

决策：

- 新增 `tools/funasr_synthetic_smoke_batch_evidence_assembler.py`。
- 新增 `tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py`。
- 新增 `docs/drv-046-funasr-synthetic-smoke-batch-evidence-assembler-plan.md`。
- DRV-046 只消费 DRV-045 execution packet 和 approved `artifacts/tmp/asr_reports/**.json` smoke report artifacts。
- DRV-046 只读取 JSON artifact bytes，用于计算 sha256 和解析 scenario_results；不读取音频，不运行 ASR，不写 artifacts。
- 合法 artifacts 会被组装为 `funasr_synthetic_smoke_result.v1` / `batch_synthetic_confirmation`，并调用 DRV-044 gate。
- 如果缺 artifact、packet path unsafe、artifact path unsafe、artifact JSON 无效、scenario_id 不匹配或 DRV-044 返回 blocked，DRV-046 不得声明 ASR quality Go。
- DRV-046 输出 `counts_as_real_mic_go_evidence=false`；即使 DRV-044 batch 通过，也只代表 synthetic ASR quality evidence，不代表真实麦克风 ready。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py -q -p no:cacheprovider`
  - Result：`6 failed, 1 warning`。
  - 失败原因：`tools/funasr_synthetic_smoke_batch_evidence_assembler.py` 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py -q -p no:cacheprovider`
  - Result：`6 passed, 1 warning`。

影响范围：

- `tools/funasr_synthetic_smoke_batch_evidence_assembler.py`
- `tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py`
- `docs/drv-046-funasr-synthetic-smoke-batch-evidence-assembler-plan.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-180 不运行 ASR，不读取音频，不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不调用远程 ASR/LLM，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk 或 ASR artifacts。

后续：

本地 FunASR strict quality exit 的可复跑路径变为：DRV-043 readiness -> DRV-045 execution packet -> 人工执行 5 个 smoke -> DRV-046 assembler -> DRV-044 gate -> DRV-032 quality decision。当前仍缺真实本地 smoke artifacts，因此默认 ASR quality 和真实麦克风 shadow test 继续 blocked。

## DEC-181：实现 DRV-047 ASR quality DRV-046 assembly intake

日期：2026-07-04

状态：Accepted

背景：

DRV-046 已能把未来手动 FunASR synthetic smoke artifacts 装配成 DRV-044 batch evidence，并在输出里包含 `drv044_gate_report`。但 DRV-032 在本轮前只能直接接收 DRV-044 gate report path，后续执行者仍需要手动从 DRV-046 report 中复制嵌套报告。为减少手工交接和证据漂移，本轮让 ASR quality gate 直接接收 DRV-046 assembly report。

决策：

- 修改 `tools/asr_quality_decision_gate.py`。
- 修改 `tests/test_asr_quality_decision_gate.py`。
- 新增 `docs/drv-047-asr-quality-drv046-assembly-intake-plan.md`。
- `build_asr_quality_decision_gate_report()` 新增 `funasr_smoke_assembly_report` 参数。
- CLI 新增 `--funasr-smoke-assembly-path` 和 `--funasr-smoke-assembly-json`。
- DRV-047 只消费 caller-provided DRV-046 JSON，不主动运行 DRV-046，不读取 artifact 内容，不计算 sha256。
- DRV-046 assembly 必须是 `assembly_status=drv044_batch_evidence_validated`、`artifact_read_status=read`、`artifact_count=5`、`counts_as_asr_quality_go_evidence=true`、`counts_as_real_mic_go_evidence=false`，且 safety flags 全 false。
- 嵌套 `drv044_gate_report` 必须是合法 DRV-044 gate report，并满足 batch confirmed、counts_as_asr_quality_go_evidence=true 和 validated artifact provenance。
- 合法输入输出 `funasr_smoke_result_source=drv046_batch_assembly`，并复用既有 `asr_quality_current_gate_not_blocking` / `strict_quality_gate_not_blocking` 逻辑。
- 直接 DRV-044 report 与 DRV-046 assembly report 同时提供时，阻断为 `blocked_by_funasr_smoke_assembly_input_guard`。
- path 仍只允许 approved `artifacts/tmp/**.json`，读取前阻断 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs`、`.m4a`、仓库外路径和非 JSON。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`4 failed, 17 passed, 1 warning`。
  - 失败原因：`funasr_smoke_assembly_report` 参数和 `--funasr-smoke-assembly-path` CLI flag 不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`21 passed, 1 warning`。

影响范围：

- `tools/asr_quality_decision_gate.py`
- `tests/test_asr_quality_decision_gate.py`
- `docs/drv-047-asr-quality-drv046-assembly-intake-plan.md`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `README.md`

边界：

DEC-181 不运行 ASR，不运行 DRV-046，不读取 artifact 内容，不读取音频，不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频，不下载 FunASR/ModelScope 模型，不调用远程 ASR/LLM，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk 或 ASR artifacts。

后续：

当前仍缺真实本地 FunASR smoke artifacts，因此默认 ASR quality 仍应保持 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。下一步只有在本地 FunASR 模型目录就绪、DRV-019 下载审批通过、用户显式选择远端 ASR 对照，或用户显式接受降级试点风险时，才改变真实麦克风前置判断。

## DEC-182：三路审查确认完整计划已写下并停止泛化评测循环

日期：2026-07-04

状态：Accepted

背景：

用户再次确认：完整计划是否已经写下；转写类验证是否由我先通过网上官方公开音频和本地模拟完成；最终真实麦克风会议是否由用户验证。同时用户明确担心项目陷入无限测评，而不是推进最初的中文技术会议实时 Copilot 主线。

决策：

- 三路只读审查结论一致：完整计划、边界、公开音频白名单、模拟转写链路、真实麦克风用户最终验证分工已经写下。
- 公开音频当前只允许官方来源复核和 no-download manifest；AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 仍在默认白名单，但在没有 verified clip manifest 和用户批准前不得下载 GB 级包。
- 模拟转写 / Shadow Pipeline 已证明产品链路 preview 可闭合：4 个工程场景产生 candidate-card preview，非工程 control 保持 0 fake candidate；该结果证明产品逻辑不是普通转写，但不证明真实 ASR 质量达标。
- 当前 ASR quality gate 默认仍是 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。
- 下一步不再新增同类计划、readiness wrapper、report-only schema 或开放式 provider 横评。
- 后续只能选择 ASR quality exit 路径：已有本地 FunASR 模型目录/cache、DRV-019 手动模型下载审批、显式远端 ASR 对照，或合法显式降级试点风险接受。
- 真实麦克风会议仍由用户在 readiness gate 满足后显式启动；当前不得把 mock replay、public audio blocked 状态或 synthetic preview 写成真实会议 Go evidence。

验证方式：

- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
  - Result：`exit 0`，`source_validation_status=passed`，`source_count=3`，`safe_to_download_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
  - Result：`exit 1`，`plan_status=blocked_no_planned_samples`，`safe_to_download_now=false`，`safe_to_extract_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
  - Result：`exit 1`，`decision_status=blocked_no_verified_public_sample_manifest`，`safe_to_call_asr_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events`
  - Result：`exit 0`，`batch_status=simulated_shadow_pipeline_batch_passed`，`engineering_preview_created_count=4`，`negative_control_fake_candidate_count=0`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
  - Result：`exit 1`，`decision_status=requires_funasr_model_dir_or_drv019_approval`，`quality_exit_status=not_exited`。

影响范围：

- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/decision-log.md`

边界：

DEC-182 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

下一步只允许围绕 ASR quality exit 或用户显式降级试点推进。若无本地模型目录、无 DRV-019 审批、无远端 ASR 对照选择、无合法降级试点接受记录，则真实麦克风 shadow test 继续保持 blocked，不再用新增文档或同类 wrapper 替代主线进展。

## DEC-183：实现 REQ-CARD-003 负反馈终态保护

日期：2026-07-04

状态：Accepted

背景：

REQ-CARD-003 要求补齐建议卡片冷却、dismiss、mark_wrong、too_late 的节流测试。当前真实会议价值闭环依赖用户反馈标签；如果用户已经把一张卡标记为 `too_late`、`marked_wrong`、`too_intrusive` 或 `dismissed`，后续误操作或重复请求不应把该负反馈覆盖成 `kept`，否则会污染 Go/Pivot/Stop 的质量判断。

决策：

- 在 `meeting_copilot_web_mvp.repository` 中新增 `NEGATIVE_CARD_FEEDBACK_STATUSES` 和 `CardStatusTransitionError`。
- `InMemorySessionRepository.set_card_status()` 和 `JsonFileSessionRepository.set_card_status()` 在生成候选 snapshot 并通过既有状态合法性校验后，执行负反馈终态迁移校验。
- `dismissed`、`marked_wrong`、`too_late`、`too_intrusive` 一旦成为当前状态，只允许重复提交同一状态，不能改为 `kept` 或其它不同状态。
- `app.py` 将 `CardStatusTransitionError` 映射为 HTTP 409，表示业务状态冲突。
- 未知状态仍保持既有 422 路径；未知 card id 仍保持 404；被 silenced 的卡片仍保持 422；失败请求不得污染 repository。
- 该实现只保护反馈资产，不新增 LLM 调用、不访问麦克风、不读取音频、不下载模型或公开音频。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_update_card_status_blocks_overwriting_negative_feedback_without_mutating_record -q -p no:cacheprovider`
  - Result：`1 failed, 2 warnings`。
  - 失败原因：第二次把 `too_late` 改回 `kept` 时 API 返回 `200`，而不是预期 `409`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_update_card_status_blocks_overwriting_negative_feedback_without_mutating_record code/web_mvp/backend/tests/test_app.py::test_update_card_status_updates_snapshot code/web_mvp/backend/tests/test_app.py::test_update_card_status_rejects_unknown_status code/web_mvp/backend/tests/test_app.py::test_update_card_status_rejects_unknown_status_without_mutating_record code/web_mvp/backend/tests/test_app.py::test_update_card_status_rejects_unknown_card_id_without_mutating_record code/web_mvp/backend/tests/test_app.py::test_json_repository_persists_session_and_card_status_across_instances -q -p no:cacheprovider`
  - Result：`6 passed, 2 warnings`。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/repository.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-183 不访问麦克风，不请求权限，不读取真实用户音频或 `.m4a`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

REQ-CARD-003 的负反馈终态保护已落地；剩余的 cooldown/budget/throttle 仍应在真实 scheduler/card lifecycle 层继续补，不再用同一张卡的反馈覆盖来近似节流。

## DEC-184：确认完整计划已落档并将下一步收口到 ASR quality exit

日期：2026-07-04

状态：Accepted

背景：

用户再次追问：完整计划是否已经写下来，转写是否需要我先去网上寻找官方公开音频并模拟验证，最终真实麦克风会议是否由用户验证。用户同时担心项目继续陷入评测循环，而不是推进最初的中文技术会议实时 Copilot 主线。

决策：

- 不新增新的总控文档；继续以 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md`、`docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`、`docs/current-plan-and-validation-report-2026-07-04.md`、`docs/requirements-traceability-matrix.md` 和本 decision-log 为权威入口。
- 三路只读审查结论一致：完整计划已经写下，公开官方音频/no-download manifest/本地合成与 Mock 转写/approved ASR event replay 由我先做，真实麦克风会议由用户最终在 readiness gate 满足后显式验证。
- 产品主线继续锁定为中文技术会议实时 Copilot：`ASR final/revision -> EvidenceSpan -> meeting state / engineering gap -> suggestion card -> feedback/export -> desktop runtime -> user real mic shadow test`。不得把项目降级成普通录音转文字或会后总结工具。
- 公开音频当前 blocked 是合理状态：AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111 和 AISHELL-1/OpenSLR SLR33 仍是默认官方白名单；没有 verified bounded clip manifest、archive member path、expected clip sha256 和 GB 级公开包下载审批前，不能下载、抽取、转码或喂 ASR。
- Public sample manifest 必须继续被解释为 schema/manual-review gate，而不是事实下载验证 gate。即使 planned sample JSON 格式通过，也不能在未下载、未读 archive index 的情况下证明 archive member 存在或 checksum 正确。
- DRV-042 simulated shadow pipeline batch 已证明产品 preview 链路可跑：4 个工程场景 preview，非工程 control 0 fake candidate；该证据不算 ASR quality Go evidence，也不算真实麦克风 evidence。
- PCWEB-115/124 已把真实麦克风 readiness 落到 policy、工具、API/UI 和测试；不得继续新增同类 readiness/report-only wrapper 来替代 ASR quality exit。
- 下一步只允许三类出口：ASR quality exit、public audio bounded manifest、explicit degraded pilot。没有本地 FunASR 模型目录、没有 DRV-019 审批、没有远端 ASR 对照选择、也没有合法降级试点接受记录时，真实麦克风 shadow test 继续 blocked。

验证方式：

- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
  - Result：`exit 0`，`source_validation_status=passed`，`source_count=3`，`safe_to_download_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
  - Result：`exit 1`，`plan_status=blocked_no_planned_samples`，`safe_to_download_now=false`，`safe_to_extract_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
  - Result：`exit 1`，`decision_status=blocked_no_verified_public_sample_manifest`，`safe_to_call_asr_now=false`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events`
  - Result：`exit 0`，`batch_status=simulated_shadow_pipeline_batch_passed`，`engineering_preview_created_count=4`，`negative_control_fake_candidate_count=0`。
- `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
  - Result：`exit 1`，`decision_status=requires_funasr_model_dir_or_drv019_approval`，`quality_exit_status=not_exited`。

影响范围：

- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-184 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

下一步不能再用新增计划、schema、readiness 面板、report-only wrapper 或开放式 provider 横评替代主线进展。默认首选 `verified_local_funasr_model_dir` 或 DRV-019 手动模型下载审批；如果用户要尽快做产品节奏验证，只能走显式降级试点风险接受，且该路径不算 ASR quality Go evidence。

## DEC-185：补齐 card policy/lifecycle 的 scheduler cooldown 审计字段

日期：2026-07-04

状态：Accepted

背景：

REQ-CARD-003 要求补齐建议卡片冷却、dismiss、mark_wrong、too_late 的节流测试。DEC-183 已先保护负反馈终态；本轮继续补 scheduler/card lifecycle 侧的冷却门禁。此前 PCWEB-039 已让密集 revision 进入 `llm_candidate_skipped` / `decision_reason=cooldown`，PCWEB-064 也已阻断 cooldown-skipped candidate，但 dry-run/lifecycle preview 响应没有显式暴露 scheduler 审计字段，UI/报告只能从 `policy_errors` 文案里间接读出 cooldown。

决策：

- `POST /live/asr/sessions/{session_id}/llm-card-creation-policy-dry-runs` 和 `POST /live/asr/sessions/{session_id}/llm-card-lifecycle-preview-dry-runs` 响应新增：
  - `scheduler_policy_status`
  - `scheduler_decision_reason`
  - `scheduler_candidate_event_type`
- 对 queued candidate 返回 `scheduler_policy_status=queued`、`scheduler_decision_reason=state_change`、`scheduler_candidate_event_type=llm_candidate_queued`。
- 对 cooldown-skipped candidate 返回 `scheduler_policy_status=blocked_by_cooldown`、`scheduler_decision_reason=cooldown`、`scheduler_candidate_event_type=llm_candidate_skipped`，并继续通过 `scheduler_candidate_not_queued` policy error 阻断正式卡片。
- lifecycle preview 对 cooldown-skipped candidate 只预览 `llm_schema_result` + `suggestion_silenced`，`future_lifecycle_status=would_silence_candidate`，不得预览 `suggestion_card`。
- 该变更只复用现有 `suggestion_candidate_event` payload，不读取 LLM config/secret，不调用 LLM，不写 events，不访问 ASR/音频/麦克风。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_card_creation_policy_dry_run_endpoint_blocks_cooldown_skipped_candidate code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_silences_cooldown_skipped_candidate -q -p no:cacheprovider`
  - Result：`2 failed, 2 warnings`。
  - 失败原因：响应缺少 `scheduler_policy_status`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_card_creation_policy_dry_run_endpoint_blocks_cooldown_skipped_candidate code/web_mvp/backend/tests/test_app.py::test_asr_live_llm_card_lifecycle_preview_dry_run_endpoint_silences_cooldown_skipped_candidate -q -p no:cacheprovider`
  - Result：`2 passed, 2 warnings`。
- Focused regression：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest <18 PCWEB-064/065 card creation/lifecycle tests> -q -p no:cacheprovider`
  - Result：`18 passed, 2 warnings`。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-185 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk，不追加真实 card lifecycle events。

后续：

REQ-CARD-003 现在覆盖了负反馈终态保护和 cooldown-skipped candidate 的 creation/lifecycle preview 审计。剩余节流方向仍包括更完整的 budget/hourly cap、too_late/too_intrusive 指标聚合和 enabled card lifecycle 的幂等写入，但这些不得绕开当前 no-LLM/no-mutation dry-run 边界。

## DEC-186：三路审查后停止评测循环，下一步收口到 ASR quality exit 或 Mac local shadow MVP

日期：2026-07-04

状态：Accepted

背景：

用户追问完整计划是否已经写下，并再次确认转写类验证应由我先通过网上官方公开音频和本地模拟完成，最终真实麦克风会议由用户验证。用户同时指出当前工作已经执行很多轮，担心陷入评测循环、偏离最初的中文技术会议实时 Copilot 产品初心。

决策：

- 完整计划已经写下，不再新增总控文档。权威入口仍是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md`、`docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`、`docs/current-plan-and-validation-report-2026-07-04.md`、`docs/requirements-traceability-matrix.md` 和本 decision-log。
- 三路只读审查结论一致：产品主线没有丢，仍是 `ASR final/revision -> EvidenceSpan -> meeting state / engineering gap -> suggestion card -> feedback/export -> desktop runtime -> 用户真实麦克风 shadow test`，不是普通录音转文字或会后总结。
- 转写验证分工继续锁定：官方公开音频来源复核、no-download manifest、本地中文技术会议合成/Mock、approved ASR event replay 和 simulated shadow pipeline 由我先完成；真实麦克风会议由用户最后在 readiness gate 满足后显式启动验证。
- 公开音频路线可执行但当前只能执行到 `官方白名单 + no-download bounded manifest + 人工下载复核`。AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 继续作为默认白名单；没有 verified archive member path、clip sha256、license citation 和 GB 级公开包下载审批前，不下载、不抽取、不转码、不喂 ASR。
- DRV-042 simulated shadow pipeline batch 已证明 mock/approved ASR event 层产品链路可闭合：4 个工程场景生成 preview，1 个非工程 control 保持 0 fake candidate；该结果证明产品不是普通转写链路，但不证明真实 ASR 质量、真实麦克风采集或真实用户价值。
- ASR quality 当前仍是主阻塞：默认仍为 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。没有本地 FunASR 模型目录/cache、没有 DRV-019 手动模型下载审批、没有显式远端 ASR 对照选择、也没有合法降级试点接受记录时，不能进入真实麦克风 shadow test。
- PC 端下一阶段不得继续新增同类 readiness/report-only wrapper。下一张主线票只能二选一：`ASR quality exit` 的实际出口执行，或 `Mac Local Shadow MVP: Tauri/WebView synthetic event demo closure`，用 approved synthetic/mock streaming events 在 Mac/Tauri/WebView 等价壳里展示 transcript 增量、EvidenceSpan、meeting state、gap/candidate、no-LLM request draft 和真实麦克风 readiness blockers。

验证方式：

- 三路只读审查：
  - 产品价值审查：计划已完整、主线未偏，但已接近评测/门禁过度生长边缘；下一步应切到 ASR quality exit 或明确风险接受的产品节奏试点。
  - ASR/公开音频审查：公开音频 blocked 合理；模拟转写证明产品链路，不证明 ASR 质量；最小动作是 FunASR synthetic batch 证据链、DRV-019 审批或显式降级试点。
  - PC 端开发审查：PC 端应进入 Mac local shadow MVP 可演示切片，但真实麦克风会议仍 blocked；下一票不要叫 readiness/report。
- 本地复跑：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py`
    - Result：`exit 0`，`source_validation_status=passed`，`source_count=3`，`safe_to_download_now=false`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio`
    - Result：`exit 1`，`plan_status=blocked_no_planned_samples`，`safe_to_download_now=false`，`safe_to_extract_now=false`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_planned_sample_manifest_decision.py`
    - Result：`exit 1`，`decision_status=blocked_no_verified_public_sample_manifest`，`safe_to_call_asr_now=false`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events`
    - Result：`exit 0`，`batch_status=simulated_shadow_pipeline_batch_passed`，`engineering_preview_created_count=4`，`negative_control_fake_candidate_count=0`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py`
    - Result：`exit 1`，`decision_status=requires_funasr_model_dir_or_drv019_approval`，`quality_exit_status=not_exited`。

影响范围：

- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-186 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

下一步不得继续新增总控计划、计划确认文档、同类 readiness/report-only wrapper、开放式 ASR/provider 横评或版权链不清音频搜索。默认主线是 `ASR quality exit`：优先本地 FunASR 模型目录/cache；若没有，则只允许 DRV-019 手动模型下载审批、显式远端 ASR 对照选择，或合法 `asr_quality_degraded_pilot_acceptance.v1` 降级试点接受。若选择产品演示推进，则下一张开发票锁定为 `Mac Local Shadow MVP: Tauri/WebView synthetic event demo closure`，不得读取真实音频、访问麦克风或调用远程 ASR/LLM。

## DEC-187：实现 PCWEB-125 Mac local shadow MVP synthetic demo closure

日期：2026-07-04

状态：Accepted

背景：

DEC-186 已确认下一步不得继续做 readiness/report-only wrapper，若选择产品演示推进，下一张开发票应锁定为 `Mac Local Shadow MVP: Tauri/WebView synthetic event demo closure`。当前 Web 工作台已有 Live ASR mock session、EvidenceSpan/state/candidate/no-LLM request draft、真实麦克风 readiness panel 和 SSE 增量渲染，但缺少一个明确的一键 Mac local shadow MVP 演示入口，容易让用户继续在 demo fixture、Live Mock、Live ASR 和 readiness 面板之间来回解释。

决策：

- 新增 `PCWEB-125`，提供 `POST /desktop/mac-local-shadow-mvp-demo/sessions`。
- 该端点创建一个 `mac_local_shadow_mvp` synthetic Live ASR session，复用现有 `build_asr_live_events`、ASR live repository、`/live/asr/sessions/{session_id}/events`、`/live/asr/sessions/{session_id}/llm-request-drafts` 和真实麦克风 readiness gate。
- Synthetic stream 覆盖 partial、final、revision、OpenQuestion、Risk、ActionItem 和 end_of_stream，闭合到 `transcript_partial -> transcript_final/revision -> EvidenceSpan -> state_event -> scheduler_event -> suggestion_candidate_event -> llm_request_draft_event -> real_mic_readiness_blocked`。
- 响应显式返回 `closure_status=closed_to_no_llm_request_draft_and_readiness_blockers`、`formal_card_creation_status=not_created`、`all_llm_statuses=["not_called"]`、真实麦克风 readiness status 和 normalized blocker `asr_quality_gate_not_exited`。
- Web 工作台新增 `Shadow MVP` 按钮和 `mac-local-shadow-mvp-panel`。点击后调用新端点，进入现有 `live_asr` SSE 渲染路径，展示 transcript 增量、EvidenceSpan、meeting state、suggestion candidate/no-LLM draft、card lifecycle readiness 和真实麦克风 blockers。
- 该入口是 synthetic demo closure，不是 ASR quality Go evidence，不是真实麦克风 pilot，不会读取真实音频、访问麦克风、运行 ASR、运行 Cargo/Tauri、启动 worker、下载模型/公开音频或调用远程 ASR/LLM。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_mac_local_shadow_mvp_demo_session_creates_synthetic_live_asr_closure code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result：`3 failed, 2 warnings`。
  - 失败原因：端点返回 404，HTML 缺 `mac-local-shadow-mvp-button`，CSS/JS 缺 `mac-local-shadow-mvp-panel` 和 demo closure 标记。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_mac_local_shadow_mvp_demo_session_creates_synthetic_live_asr_closure code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result：`3 passed, 2 warnings`。
- Regression：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py -q -p no:cacheprovider`
  - Result：`285 passed, 2 warnings`。
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_live_events.py -q -p no:cacheprovider`
  - Result：`40 passed, 1 warning`。
- Browser E2E：
  - `node code/web_mvp/e2e/browser_smoke.mjs`
  - Initial Result：`failed`，原因是新增 E2E 断言把 metric 文本顺序写死为 `Finals4` / `Drafts5`，而实际 DOM 为数值优先的 `4Finals` / `5Drafts`。修正为同时校验 label/value 后复跑。
  - Final Result：`status=ok`，checked list 包含 `Mac Local Shadow MVP synthetic demo closure`。
  - 覆盖：点击 `Shadow MVP` 按钮、调用 `/desktop/mac-local-shadow-mvp-demo/sessions`、打开 Live ASR SSE、确认 EventSource 打开前无预加载 card/state/transcript/evidence、确认 panel 显示 `mac_local_shadow_mvp` / `closed_to_no_llm_request_draft_and_readiness_blockers` / `asr_quality_gate_not_exited`、确认 draft report 使用 `/draft.md` 且不请求 `/report.md`。
- Sensitive scan：
  - Ran the standard repository sensitive marker scan over README, docs, tools, tests, and Web backend code.
  - Result：no matches。
- Pycache check：
  - `find code/web_mvp/backend -path '*__pycache__*' -type f -print`
  - Result：empty。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-187 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。该 demo 只创建 Web MVP ASR live session 记录，输入是内置 synthetic streaming events。

后续：

PCWEB-125 把 PC 端主线从“只有 gate/readiness”推进到“可一键演示 Mac local shadow MVP synthetic workflow”，并已补齐浏览器 E2E 点击验证。下一步仍不能进入真实麦克风会议；默认应继续选择 ASR quality exit，或在用户显式接受风险时进入一次降级 shadow-test timing/feedback 试点。不得把 synthetic demo 或 browser E2E 写成 ASR quality 或真实会议 Go evidence。

## DEC-188：二次只读审查确认完整计划已写下且下一步回到 ASR quality exit

日期：2026-07-04

状态：Accepted

背景：

用户再次确认：完整计划是否已经写下；转写类验证是否由我先通过网上官方公开音频和本地模拟完成；最终是否由用户进行真实麦克风会议验证。同时用户担心继续在评测和计划里打圈，要求明确后续是否还要继续评估，还是进入主线开发和可验收出口。

决策：

- 完整计划已经写下，不再新增总控计划。权威入口仍是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md`、`docs/current-plan-and-validation-report-2026-07-04.md`、`docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`、`docs/requirements-traceability-matrix.md` 和本 decision-log。
- 两路只读 Agent 审查结论一致：主线没有本质矛盾，产品仍是中文技术会议实时 Copilot；真实麦克风最终验证边界清楚；当前 blocker 是 ASR quality exit，不是计划缺失。
- PCWEB-125 `Mac Local Shadow MVP synthetic demo closure` 已完成，不能再把它当作下一张未完成开发票。
- Public audio bounded manifest 仍是条件分支，不是当前默认主线。没有真实 `archive_member_path`、clip start/end、expected sha256、license citation、cleanup strategy 和 GB 级公开包下载审批前，必须保持 blocked，不下载、不抽取、不转码、不喂 ASR。
- 官方公开来源口径保持不变：AliMeeting/OpenSLR SLR119 和 AISHELL-4/OpenSLR SLR111 只作为会议声学候选，AISHELL-1/OpenSLR SLR33 只作为普通话 ASR/runtime sanity；Bilibili、YouTube、播客、公开课、技术大会录播、平台课程音频或授权链不清来源不进入自动评测。
- 默认下一步回到 `ASR quality exit` 的实际出口：已验证本地 FunASR 模型目录/cache、DRV-019 手动模型下载审批、合法 DRV-046 batch evidence assembly，或用户显式 degraded pilot 风险接受。Degraded pilot 不算 ASR quality Go evidence。

验证方式：

- 两路只读 Agent：
  - 文档主线审查：计划完整；无限评测循环已被文档明确禁止；真实麦克风最终验证边界清楚；建议只补 PCWEB-125 后置状态同步和 public audio manifest 条件分支口径。
  - 公开音频/模拟转写审查：官方 no-download 白名单合理且偏保守；当前可模拟验证的是产品链路和事件合同，不是真实 ASR 质量；真实麦克风前仍缺 ASR quality exit、PC/runtime readiness 和用户显式授权。
- 本轮联网复核继续只保留 OpenSLR 官方来源和 FunASR 官方项目作为可追溯参考；没有执行下载、抽取、转码、ASR 或远程调用。

影响范围：

- `docs/current-mainline-index.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-188 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。

后续：

下一步不得继续新增总控计划、同类 readiness/report-only wrapper、开放式 provider 横评或版权链不清音频搜索。默认进入 ASR quality exit 的实际出口；如果没有本地 FunASR 模型目录/cache、没有 DRV-019 审批、没有合法 DRV-046 assembly、也没有 degraded pilot 风险接受，当前正确状态就是继续 blocked，而不是继续评测。

## DEC-189：实现 PCWEB-126 Realistic Meeting Simulation Pack

日期：2026-07-04

状态：Accepted

背景：

用户要求继续按计划实现和自测，并指出当前进度太慢，需要尽量真实模拟。DEC-188 已确认不应继续新增总控计划或同类评测 wrapper，且 PCWEB-125 已完成基础 synthetic Shadow MVP 演示。下一步需要把体验从“短 synthetic demo”推进到“更像真实中文技术会议节奏”的可点击模拟包，同时仍不触碰麦克风、真实音频、远程调用或模型下载。

决策：

- 新增 `PCWEB-126 Realistic Meeting Simulation Pack`。
- Web backend 新增 `POST /desktop/realistic-meeting-simulation-pack/sessions`。
- 该端点生成 `realistic_meeting_simulation_pack` synthetic Live ASR session，场景为 `pcweb_126_release_incident_review`，会议形态固定为 4 speaker / 8 turn / 47.2s。
- Synthetic stream 覆盖：
  - multi speaker turns
  - partial corrections
  - 2 次 revision
  - pause markers
  - overlap marker
  - payment-gateway、P99、0.1%、Kafka lag、rollback、feature flag 等技术词
  - open question、risk、action item、decision candidate
- 该端点复用现有 `build_asr_live_events`、ASR live repository、SSE、no-LLM request draft 和真实麦克风 readiness gate，不创建平行 pipeline。
- Web 工作台新增 `真实模拟` 按钮和 `renderRealisticMeetingSimulationPack`，点击后进入 Live ASR mode，展示 simulation/scenario、speaker/turn/revision/state/draft 计数、realism features、technical terms 和 readiness blockers。
- 该功能只增强产品体验模拟真实性，不算 ASR quality Go evidence，不算真实麦克风 Go evidence。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session -q -p no:cacheprovider`
  - Result：`1 failed, 2 warnings`。
  - 失败原因：`POST /desktop/realistic-meeting-simulation-pack/sessions` 返回 404。
- Focused Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result：`3 passed, 2 warnings`。
- Browser E2E：
  - `node code/web_mvp/e2e/browser_smoke.mjs`
  - Initial Result：failed，原因是 E2E 没有清空上一轮 Shadow MVP 的 EventSource URL 数组，等待条件被旧 URL 误满足，`realisticDomAtOpen` 为 null。
  - Fix：点击真实模拟前清空 `window.__meetingCopilotEventSourceUrls`，并等待 `window.__meetingCopilotShadowMvpDomAtEventSourceOpen !== null`。
  - Final Result：`status=ok`，checked list 包含 `realistic meeting simulation pack`。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `docs/current-mainline-index.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-189 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。该模拟包只使用代码内置 synthetic streaming events。

后续：

PCWEB-126 已把产品体验验证从短 demo 推进到更真实的中文技术会议 synthetic simulation。下一步仍应回到 ASR quality exit：本地 FunASR 模型目录/cache、DRV-019 手动模型下载审批、合法 DRV-046 batch evidence assembly，或用户显式 degraded pilot 风险接受。不得把 PCWEB-126 写成真实 ASR 或真实麦克风通过。

## DEC-190：实现 PCWEB-127 Long Realistic Meeting Simulation Profile

日期：2026-07-04

状态：Accepted

背景：

PCWEB-126 已提供 47.2s 的真实感短会议模拟，但用户要求尽量真实模拟，并且后续需要可复盘的 shadow report preview。为了继续推进产品体验而不触碰真实麦克风、真实音频、远程 provider 或模型下载，本轮在同一 simulation endpoint 上增加长会 profile，而不是新建平行链路。

决策：

- `POST /desktop/realistic-meeting-simulation-pack/sessions` 支持 `profile` 字段。
- `profile` 只允许 `standard` 或 `long_shadow`；其它值返回 422。
- `standard` 保持 PCWEB-126 的 4 speaker / 8 turn / 47.2s synthetic profile。
- `long_shadow` 生成 `pcweb_127_long_architecture_release_review`，会议形态为 5 speaker / 16 turn / 615s。
- `long_shadow` synthetic stream 覆盖 5 partial、13 final、3 revision、pause/overlap markers、recommendation-service、payment-gateway、idempotency-key、Redis cluster、MySQL、P99、SLO、Kafka lag、rollback、feature flag 等技术词。
- 长会 profile 复用现有 `build_asr_live_events`、ASR live repository、SSE、no-LLM request draft、draft markdown 和真实麦克风 readiness gate。
- Web 工作台新增 `长会模拟` 按钮；短模拟和长会模拟共用 `loadRealisticMeetingSimulationPackProfile` 和 `renderRealisticMeetingSimulationPack`。
- 该功能只增强 synthetic long-shadow 产品体验和 draft preview，不算 ASR quality Go evidence，不算真实麦克风 Go evidence。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_long_shadow_profile_creates_report_preview -q -p no:cacheprovider`
  - Result：`1 failed, 2 warnings`。
  - 失败原因：请求模型禁止 `profile`，endpoint 返回 422。
- Focused Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_creates_richer_live_asr_session code/web_mvp/backend/tests/test_app.py::test_desktop_realistic_meeting_simulation_pack_long_shadow_profile_creates_report_preview code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served -q -p no:cacheprovider`
  - Result：`3 passed, 2 warnings`。
- Browser E2E：
  - `node code/web_mvp/e2e/browser_smoke.mjs`
  - Result：`status=ok`，checked list 包含 `long realistic meeting simulation pack`。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `docs/current-mainline-index.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

边界：

DEC-190 不访问麦克风，不请求权限，不枚举音频设备，不读取真实用户音频或 `.m4a`，不读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，不下载公开音频大包，不下载 FunASR/ModelScope 模型，不运行 ASR，不调用远程 ASR/LLM，不启动 worker，不运行 Cargo/Tauri，不写 audio chunk。该 profile 只使用代码内置 synthetic streaming events。

后续：

PCWEB-127 已把模拟推进到 10 分钟级长会 shadow preview。下一步不应继续只增加 synthetic realism；默认应回到 ASR quality exit 的实际路径：本地 FunASR 模型目录/cache、DRV-019 手动模型下载审批、合法 DRV-046 batch evidence assembly，或用户显式 degraded pilot 风险接受。

## DEC-191：本地 FunASR readiness 预检通过并推进到 smoke 执行许可前

日期：2026-07-04

状态：Accepted

背景：

用户要求真实汇报当前进度，并指出不希望继续陷入无限评测。DEC-190 后，下一步不应继续增加 synthetic realism，而应回到 ASR quality exit。重新检查本机环境时发现，项目内已有 FunASR runtime，标准 ModelScope 缓存中也已有 Paraformer online 所需模型文件；因此“缺模型/必须下载审批”不再是当前默认卡点。

决策：

- 记录本机 FunASR readiness 预检已通过，但不把它写成 ASR quality Go evidence。
- 已生成 ignored readiness artifact：`artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json`。
- `tools/asr_quality_decision_gate.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 将 ASR quality 状态推进到 `funasr_cache_preflight_ready_requires_execution_approval`。
- `tools/funasr_synthetic_smoke_execution_packet.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 已生成 5 场景 manual execution packet，状态为 `ready_for_manual_batch_funasr_synthetic_smoke_run`。
- no-execution packet 已固化到 ignored artifact：`artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`。
- 当前仍不运行本地 FunASR smoke，因为这会读取合成音频并写 ASR artifacts；执行前需要明确许可。

原因：

- 本机缓存预检通过后，不应继续把“提供模型目录或下载模型”作为唯一下一步。
- 但 readiness 只证明环境和模型文件存在，不证明中文技术词召回、延迟、final/revision 质量或非工程负控表现。
- 运行 smoke 是实际 ASR 质量出口的一步，但它会执行本地模型，因此需要和“只读/不执行”的 readiness 区分。

验证方式：

- FunASR readiness：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/funasr_synthetic_smoke_readiness.py --audio-path artifacts/tmp/synthetic_audio/api-review-001.wav --events-output-path artifacts/tmp/asr_events/api-review-001.funasr.events.json --provider-output-path artifacts/tmp/asr_reports/api-review-001.funasr.provider.json --transcript-report-path artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json --smoke-report-path artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json --model-cache-root <local ModelScope iic cache>`
  - Result：`readiness_status=cache_preflight_passed_offline_execution_not_proven`，`required_cached_models_status=present`，`model_download_status=not_started`，`validation_errors=[]`。
- ASR quality gate with readiness:
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json`
  - Result：exit `1`，`decision_status=funasr_cache_preflight_ready_requires_execution_approval`，`quality_exit_status=not_exited`，`safe_to_run_funasr_smoke_now=false`。
- Execution packet:
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/funasr_synthetic_smoke_execution_packet.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json`
  - Result：exit `0`，`packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run`，`scenario_count=5`，`execution_approval_status=not_approved_manual_run_only`，`safe_to_run_asr_now=false`。
- Regression and browser smoke in same status pass:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py code/web_mvp/backend/tests/test_live_events.py -q -p no:cacheprovider`
  - Result：`327 passed, 2 warnings`。
  - `node code/web_mvp/e2e/browser_smoke.mjs`
  - Result：`status=ok`，checked list includes `realistic meeting simulation pack` and `long realistic meeting simulation pack`。

成本/隐私影响：

- 不产生远程 ASR 费用。
- 不调用 LLM。
- 不下载 FunASR/ModelScope 模型。
- 不访问麦克风、不请求权限、不枚举设备。
- 不读取真实用户音频或 `.m4a`。
- 不读取 `configs/local`。
- 后续若批准 local smoke，只允许读取 `artifacts/tmp/synthetic_audio/**` 合成音频，并写 ignored `artifacts/tmp/asr_events/**` / `artifacts/tmp/asr_reports/**`。

影响范围：

- `docs/current-mainline-index.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

后续：

下一步不是继续评测，也不是继续找模型，而是决定是否批准一次本地 FunASR synthetic smoke。若批准，按 DRV-045 5 场景 packet 执行，随后用 DRV-046 汇总 artifacts、DRV-044 校验 provenance/hash/质量阈值、DRV-032 判断 ASR quality 是否退出 blocker。未批准前真实麦克风 shadow test 继续 blocked。

## DEC-192：补齐 FunASR synthetic smoke 后处理命令闭环

日期：2026-07-04

状态：Accepted

背景：

DEC-191 已确认本机 FunASR readiness 预检通过，并生成了 5 场景 no-execution packet。但 packet 只明确了本地 FunASR provider/events 产物路径和 smoke report 预期路径，没有把 transcript report 与 synthetic ASR smoke report 的后处理命令固定下来。这样即使后续获批运行本地 FunASR，也可能在 provider/events artifact 到 DRV-046/044/032 之间再次出现人工拼命令和 golden script 映射错误。

决策：

- `tools/funasr_synthetic_smoke_execution_packet.py` 增加 `postprocess_command_previews`。
- 每个 scenario 输出：
  - `transcript_report_argv`
  - `smoke_report_argv`
  - `smoke_report_stdout_redirect_path`
- postprocess preview 显式绑定：
  - `data/asr_eval/glossaries/technical-terms.zh.json`
  - `data/asr_eval/synthetic_meetings/scripts/*.json`
  - provider JSON、events JSON、transcript report JSON 和 smoke report JSON 路径。
- `code/asr_runtime/scripts/command_result.py` 的 `ProviderTranscript` 增加 `duration_seconds`。
- `code/asr_runtime/scripts/transcript_report.py` 在 provider JSON 含 `audio_duration_seconds` 时允许省略 `--duration-seconds`，减少手工执行参数。
- ignored packet artifact `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json` 已重新生成并包含 postprocess previews。

验证方式：

- Red：
  - `tests/test_transcript_report.py::test_load_provider_transcript_reads_text_latency_and_provider`
  - `tests/test_transcript_report.py::test_transcript_report_cli_uses_provider_duration_when_provider_json_has_it`
  - Result：失败，原因是 `ProviderTranscript` 无 `duration_seconds` 且 CLI 仍要求 `--duration-seconds`。
- Red：
  - `tests/test_funasr_synthetic_smoke_execution_packet.py::test_ready_readiness_builds_batch_execution_packet_without_leaking_local_paths`
  - Result：失败，原因是 packet 无 `postprocess_command_previews`。
- Green：
  - `cd code/asr_runtime && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_transcript_report.py tests/test_demo_pipeline.py tests/test_transcribe_funasr.py -q -p no:cacheprovider`
  - Result：`21 passed, 1 warning`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_asr_smoke_report.py tests/test_funasr_synthetic_smoke_execution_packet.py tests/test_funasr_synthetic_smoke_readiness.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`38 passed, 1 warning`。

成本/隐私影响：

不运行 ASR，不读取合成音频或真实音频，不访问麦克风，不读取 `.m4a`，不读取 `configs/local`，不下载模型，不调用远程 ASR/LLM。该变更只让未来一次获批的本地 synthetic smoke 在执行后能机器化进入 report/gate 链路。

后续：

下一步仍是显式批准一次本地 FunASR synthetic smoke。获批后按 packet 的 provider command previews 与 postprocess command previews 生成 5 场景 artifacts，再进入 DRV-046 batch assembler、DRV-044 quality/provenance gate 和 DRV-032 ASR quality exit。

## DEC-193：实现 FunASR synthetic smoke approved runner dry-run

日期：2026-07-04

状态：Accepted

背景：

DEC-191/192 已经让本地 FunASR readiness、5 场景 execution packet 和 postprocess command previews 就绪。但如果后续获批执行，还需要一个受控入口来按 packet 顺序执行命令，避免人工复制 5 个 provider command 和 10 个后处理 command。当前仍未获得运行本地 ASR 的明确许可，因此本轮只能实现和验证 dry-run/default blocked 路径，并用 fake runner 覆盖 execute 分支。

决策：

- 新增 `tools/funasr_synthetic_smoke_approved_runner.py`。
- 新增 `tests/test_funasr_synthetic_smoke_approved_runner.py`。
- runner 默认只 dry-run：
  - `runner_status=dry_run_ready_requires_execute_flag_and_approval`
  - `planned_provider_command_count=5`
  - `planned_postprocess_command_count=10`
  - `executed_command_count=0`
  - `safe_to_run_asr_now=false`
- execute 分支必须同时具备：
  - `--execute`
  - 合法 `funasr_synthetic_smoke_execution_approval.v1` approval record
  - `approval_token=APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY`
  - `approval_scope=local_funasr_synthetic_smoke_5_scenarios_only`
  - `approved_packet_path` 与当前 packet path 一致
  - 本地模型目录存在且包含 `model.pt`、`config.yaml`
- execute 分支会把 packet 中的 `<modelscope_runtime_models_iic/...>` placeholder 替换为调用方提供的本地模型目录，但报告不回显绝对路径。
- 本轮只用 fake runner 验证 execute 分支顺序和 stdout redirect，没有运行真实 FunASR。
- dry-run artifact 写入 ignored root：`artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json`。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_approved_runner.py -q -p no:cacheprovider`
  - Result：`4 failed, 1 warning`，原因是 runner 工具不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_approved_runner.py -q -p no:cacheprovider`
  - Result：`4 passed, 1 warning`。
- Regression：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_approved_runner.py tests/test_funasr_synthetic_smoke_execution_packet.py tests/test_funasr_synthetic_smoke_readiness.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`39 passed, 1 warning`。
- Dry-run artifact：
  - `artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json`
  - Result：`runner_status=dry_run_ready_requires_execute_flag_and_approval`、`executed_command_count=0`。

成本/隐私影响：

默认不运行 ASR，不读取合成音频，不写 ASR artifacts，不访问麦克风，不读取真实用户音频或 `.m4a`，不读取 `configs/local`，不调用远程 ASR/LLM，不下载模型。execute 分支即使获批，也只允许本地 FunASR、合成音频和 ignored ASR artifacts。

后续：

下一步仍需显式批准一次本地 FunASR synthetic smoke。批准后可用 runner 的 execute 分支生成 5 场景 artifacts，再进入 DRV-046 batch assembler、DRV-044 quality/provenance gate 和 DRV-032 ASR quality exit。

## DEC-194：实现 FunASR synthetic smoke approval record 模板

日期：2026-07-04

状态：Accepted

背景：

DEC-193 已实现受控 runner，但 runner 的 execute 分支需要合法 approval record。为了避免把聊天中的自动续跑、模板生成或 dry-run 误当作执行许可，本轮补齐一个 approval record 模板工具，并加固 runner：模板默认不算批准，必须显式将 `approval_confirmed_by_user=true` 后才会被 runner 接受。

决策：

- 新增 `tools/funasr_synthetic_smoke_execution_approval_record.py`。
- 新增 `tests/test_funasr_synthetic_smoke_execution_approval_record.py`。
- 默认模板 artifact 写入：`artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json`。
- approval record version：`funasr_synthetic_smoke_execution_approval.v1`。
- 默认模板 `approval_confirmed_by_user=false`。
- runner `_validate_approval_record` 新增硬要求：`approval_confirmed_by_user must be true`。
- 模板固定 token/scope/packet path/场景数和边界：
  - `approval_token=APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY`
  - `approval_scope=local_funasr_synthetic_smoke_5_scenarios_only`
  - `approved_packet_path=artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`
  - `allow_read_synthetic_audio=true`
  - `allow_write_ignored_asr_artifacts=true`
  - `allow_run_local_funasr=true`
  - `deny_real_user_audio=true`
  - `deny_microphone=true`
  - `deny_remote_asr=true`
  - `deny_llm=true`
  - `deny_model_download=true`

验证方式：

- Red：
  - `tests/test_funasr_synthetic_smoke_approved_runner.py::test_execute_rejects_unconfirmed_approval_template`
  - Result：失败，因为 runner 还未要求 `approval_confirmed_by_user=true`。
- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_approval_record.py -q -p no:cacheprovider`
  - Result：`3 failed, 1 warning`，原因是 approval record 工具不存在。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_approval_record.py tests/test_funasr_synthetic_smoke_approved_runner.py tests/test_funasr_synthetic_smoke_execution_packet.py tests/test_funasr_synthetic_smoke_readiness.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`43 passed, 1 warning`。
- Safety smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/funasr_synthetic_smoke_approved_runner.py --execution-packet-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json --approval-record-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json --execute`
  - Result：`runner_status=blocked_missing_or_invalid_execution_approval`，`validation_errors` 包含 `approval_confirmed_by_user must be true`，`executed_command_count=0`。

成本/隐私影响：

不运行 ASR，不读取合成音频，不写 ASR artifacts，不访问麦克风，不读取真实用户音频或 `.m4a`，不读取 `configs/local`，不调用远程 ASR/LLM，不下载模型。该模板只把未来执行许可变成可审计 JSON，不代表许可已经给出。

后续：

下一步仍是用户显式确认本地 FunASR synthetic smoke approval record。确认后 runner 才可执行本地 5 场景 synthetic smoke，并进入 DRV-046/044/032 quality exit。

## DEC-195：修复 FunASR approval wrapper 与 runner 互操作

日期：2026-07-04

状态：Accepted

背景：

DEC-194 生成的 approval artifact 是一个 report wrapper，真正的 approval record 位于 `approval_record_template` 字段。复核时发现 `tools/funasr_synthetic_smoke_approved_runner.py --approval-record-path` 会把整个 wrapper 当作 approval record 校验，导致未确认模板返回一组无关字段错误，而不是精确返回 `approval_confirmed_by_user must be true`。这会让后续执行前诊断变得混乱。

决策：

- 修复 runner：读取 approval record path 后，如果 JSON 包含 `approval_record_template` object，则自动解包该 object 再执行 approval 校验。
- 保持安全边界不变：`approval_confirmed_by_user=false` 的模板仍被拒绝，`executed_command_count=0`。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_approved_runner.py::test_runner_unwraps_approval_record_template_report_from_path -q -p no:cacheprovider`
  - Result：失败，原因是 runner 校验 wrapper 而不是 `approval_record_template`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_approval_record.py tests/test_funasr_synthetic_smoke_approved_runner.py tests/test_funasr_synthetic_smoke_execution_packet.py tests/test_funasr_synthetic_smoke_readiness.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider`
  - Result：`44 passed, 1 warning`。
- Safety smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/funasr_synthetic_smoke_approved_runner.py --execution-packet-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json --approval-record-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json --execute`
  - Result：`runner_status=blocked_missing_or_invalid_execution_approval`，`validation_errors=['approval_confirmed_by_user must be true']`，`executed_command_count=0`。

成本/隐私影响：

不运行 ASR，不读取合成音频，不写 ASR artifacts，不访问麦克风，不读取真实用户音频或 `.m4a`，不读取 `configs/local`，不调用远程 ASR/LLM，不下载模型。

后续：

当前执行前链路已无已知 wrapper/runner 互操作缺口。下一步仍是用户显式确认 approval record，并运行本地 FunASR synthetic smoke。

## DEC-196：真实麦克风全链路升级确认

日期：2026-07-04

状态：Accepted

背景：

用户指出“适当的时候要调麦克风走真实全链路流程”。此前为了避免隐私和权限越界，主线一直停在 synthetic/mock、approval record、runner dry-run 和 ASR quality gate。该用户确认说明真实麦克风阶段必须进入产品主线，不能把项目长期停留在模拟工具层。

决策：

- 真实麦克风全链路是后续必须执行的阶段。
- 该确认不等于当前立即授权访问麦克风。
- 当前仍不得访问麦克风、枚举音频设备、请求 macOS 麦克风权限、读取真实用户录音或写真实 audio chunk。
- 进入真实麦克风前必须先完成本地 FunASR synthetic smoke 或合法 degraded pilot quality acceptance。
- 正式顺序固定为：
  - confirm FunASR synthetic smoke approval record
  - run local FunASR 5 scenario synthetic smoke
  - run DRV-046 batch assembler
  - run DRV-044 quality/provenance gate
  - run DRV-032 ASR quality exit
  - run PCWEB-115 real mic readiness gate
  - explicit real mic approval/start
  - real meeting shadow test
  - feedback/export

原因：

- 产品最终价值必须在真实会议中验证，不能只停留在 synthetic simulation。
- 但真实麦克风涉及隐私、系统权限和真实 audio chunk，不能被“适当的时候”这类方向性表达直接触发。
- 先过 ASR quality exit 可以避免把明显不可用的 ASR 带进真实会议，浪费用户会议验证机会。

成本/隐私影响：

当前无新增成本，无远程调用。未来真实麦克风阶段必须再次确认音频保留/删除策略、是否上传、是否调用远程 ASR/LLM、费用和权限提示。

验证方式：

- 本决策为方向与边界锁定，不运行命令。
- 后续进入真实麦克风前必须由 PCWEB-115 readiness gate 给出可执行状态，并由用户显式启动。

后续：

下一步仍是确认本地 FunASR synthetic smoke approval record，并运行本地 synthetic smoke。只有 ASR quality exit 通过或用户显式接受 degraded pilot 风险后，才进入真实麦克风全链路。

## DEC-197：加固 FunASR approved runner 的 packet command contract

日期：2026-07-04

状态：Accepted

背景：

多 Agent 只读审查指出，`tools/funasr_synthetic_smoke_approved_runner.py` 在默认 dry-run、审批模板和模型目录校验上已经安全，但 `_validate_packet()` 只校验 packet 顶层字段，没有二次校验 caller-provided packet 内部的 provider/postprocess argv、输入路径、stdout redirect 和脚本路径。一旦未来用户批准执行，runner 会直接执行 packet 内的命令。虽然当前未授权不会执行，但这是获批前应补的安全合同缺口。

决策：

- 在 runner 中新增 DRV-045 packet command contract 校验。
- provider command 必须精确匹配 approved local FunASR script、synthetic audio root、events output root、provider stdout root、model placeholder 和固定 streaming 参数。
- postprocess command 必须精确匹配 transcript report script、FunASR single-result builder script、provider/transcript/events/report roots、golden script root 和 smoke report stdout root。
- scenario id/order 必须精确匹配 4 个工程场景 + 1 个 non-engineering control。
- 缺少 `argv`、非法路径、forbidden root、`.m4a`、系统语音备忘录临时路径、`outputs/**` 或仓库外路径时，runner 在执行前返回 `blocked_invalid_execution_packet`，`executed_command_count=0`，不调用 `run_command`。
- `_default_run_command()` 写 stdout 前也复用 approved report root guard，避免未来绕过 packet 校验时写到非 approved 目录。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_approved_runner.py -q -p no:cacheprovider`
  - Result：`4 failed, 6 passed, 1 warning`，原因是 forbidden provider audio path、stdout redirect、postprocess provider-json 和 malformed argv 都会进入执行分支或抛 `KeyError`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_approved_runner.py -q -p no:cacheprovider`
  - Result：`10 passed, 1 warning`。
- Safety smoke：
  - `PYTHONDONTWRITEBYTECODE=1 python3 tools/funasr_synthetic_smoke_approved_runner.py --execution-packet-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json --approval-record-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json --execute`
  - Result：`runner_status=blocked_missing_or_invalid_execution_approval`，`validation_errors=['approval_confirmed_by_user must be true']`，`executed_command_count=0`。

成本/隐私影响：

不运行 ASR，不读取合成音频，不写 ASR artifacts，不访问麦克风，不读取真实用户音频或 `.m4a`，不读取 `configs/local`，不调用远程 ASR/LLM，不下载模型。该变更只让未来获批执行前的 packet 更难被篡改。

后续：

runner 的执行前安全合同已加固。下一步仍不是访问麦克风，而是修复并验证 approved runner postprocess artifact 到 DRV-046/044/032 的格式桥接，然后再等待用户显式批准本地 FunASR synthetic smoke。

## DEC-198：实现 FunASR single-result builder 并修复 runner 到 DRV-046 的 evidence 格式桥接

日期：2026-07-04

状态：Accepted

背景：

多 Agent 只读审查继续发现一个更实质的执行链断点：DRV-045 packet 的 `smoke_report_argv` 原本指向 `tools/synthetic_asr_smoke_report.py`，该脚本输出旧版 `synthetic_asr_smoke_report.v1` 扁平摘要；而 DRV-046 assembler 读取手动 smoke artifact 时要求每个 artifact 包含 `funasr_synthetic_smoke_result.v1` / `single_synthetic_smoke` / `scenario_results=[...]`。因此即使未来用户批准、runner 的 15 条命令全部返回 0，也会在 DRV-046 batch assembly 处因为缺 `scenario_results` 被 blocked。

决策：

- 新增 `tools/funasr_synthetic_smoke_single_result_builder.py`。
- 新增 `tests/test_funasr_synthetic_smoke_single_result_builder.py`。
- DRV-045 packet 的 `smoke_report_argv` 改为调用新 builder，而不是旧 synthetic ASR smoke summary。
- 新 builder 只读取 approved postprocess JSON：
  - `artifacts/tmp/asr_reports/**.funasr.provider.json`
  - `artifacts/tmp/asr_reports/**.funasr.transcript-report.json`
  - `artifacts/tmp/asr_events/**.funasr.events.json`
  - `data/asr_eval/synthetic_meetings/scripts/*.json`
- 输出 DRV-044 可验证的单场景 evidence：
  - `manifest_version=funasr_synthetic_smoke_result.v1`
  - `evidence_kind=single_synthetic_smoke`
  - `provider=funasr_streaming`
  - `model_alias=paraformer-zh-streaming`
  - `scenario_results=[...]`
- builder 从 events 计算 event contract 和 latency，从 provider/transcript/script 计算 raw/normalized technical entity recall，从 synthetic script expectation 与 transcript evidence spans 生成工程闭环计数；non-engineering control 保持 `state_event_count=0`、`candidate_card_count=0`。
- 新增跨工具测试：用 DRV-045 packet 里的 `smoke_report_argv` 调用 builder 生成 5 个单场景 artifact，再交给 DRV-046 assembler，确认可以进入 `drv044_batch_evidence_validated` 并让 DRV-044 返回 `funasr_synthetic_smoke_quality_batch_confirmed`。

验证方式：

- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_single_result_builder.py -q -p no:cacheprovider`
  - Result：`2 failed, 1 warning`，原因是 builder 文件不存在。
- Red：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_packet.py::test_ready_readiness_builds_batch_execution_packet_without_leaking_local_paths -q -p no:cacheprovider`
  - Result：失败，原因是 packet 仍指向 `tools/synthetic_asr_smoke_report.py`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_single_result_builder.py tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py -q -p no:cacheprovider`
  - Result：`9 passed, 1 warning`。
- Green：
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_execution_packet.py tests/test_funasr_synthetic_smoke_approved_runner.py -q -p no:cacheprovider`
  - Result：`16 passed, 1 warning`。
- Regenerated no-execution packet:
  - `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`
  - Result：`packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run`，first smoke postprocess script 为 `tools/funasr_synthetic_smoke_single_result_builder.py`。
- Regenerated dry-run:
  - `artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json`
  - Result：`runner_status=dry_run_ready_requires_execute_flag_and_approval`，`planned_provider_command_count=5`，`planned_postprocess_command_count=10`，`executed_command_count=0`。

成本/隐私影响：

不运行 ASR，不读取合成音频，不访问麦克风，不读取真实用户音频或 `.m4a`，不读取 `configs/local`，不调用远程 ASR/LLM，不下载模型。builder 只读取未来 runner 已生成的 provider/transcript/events JSON 和 synthetic script JSON；本轮测试使用临时 JSON fixture。

后续：

获批后的执行链现在变为：local FunASR provider/events artifact -> transcript report -> FunASR single-result builder -> DRV-046 batch assembler -> DRV-044 quality/provenance gate -> DRV-032 ASR quality exit。当前仍未获得本地 FunASR synthetic smoke 执行批准，因此真实 ASR artifacts 不存在，`quality_exit_status` 仍未退出，真实麦克风仍 blocked。

## DEC-199：执行本地 FunASR synthetic smoke 主链路并确认 ASR 质量门未过

日期：2026-07-04

状态：Accepted

背景：

用户要求停止深入边界测试，先把真实主链路自测跑通。DEC-197/198 已修复 runner 执行安全合同与 runner -> postprocess -> DRV-046/044/032 的 evidence 格式桥接；此时继续停留在 dry-run 会偏离主线。因此本轮只执行本地合成中文技术会议 FunASR smoke 主链路，不访问麦克风、不读取真实用户录音、不调用远程 ASR/LLM。

决策：

- 生成 confirmed local synthetic smoke approval record，并仅授权 `local_funasr_synthetic_smoke_5_scenarios_only`。
- 执行 DRV-045 approved runner 的 `--execute` 分支，按 packet 顺序跑 5 个 provider command 和 10 个 postprocess command。
- 执行 DRV-046 batch assembler，读取 5 个单场景 smoke artifacts 并复用 DRV-044 质量/provenance gate。
- 执行 DRV-032 ASR quality decision gate，使用 DRV-046 assembly 作为输入判断是否退出 ASR quality blocker。
- 不放宽阈值、不手工改写 ASR 文本、不把失败的质量证据伪装成 Go evidence。

实际结果：

- Approved runner：`runner_status=executed_local_funasr_synthetic_smoke_commands`，`executed_command_count=15`，`validation_errors=[]`。
- DRV-046 assembler：`assembly_status=drv044_batch_evidence_blocked`，`artifact_read_status=read`，`artifact_count=5`，`counts_as_asr_quality_go_evidence=false`。
- DRV-032 quality gate：`decision_status=blocked_by_funasr_smoke_assembly_input_guard`，`quality_exit_status=not_exited`，`safe_to_capture_microphone_now=false`。
- 5 个场景 event contract 均能产生 required event sequence，说明 ASR event -> transcript report -> single-result builder -> batch assembler 的结构链路已通。
- 工程场景 normalized technical entity recall 分别约为 `0.50`、`0.20`、`0.25`、`0.00`，未达到 `>=0.80` 阈值。
- 5 个场景 RTF 约为 `0.92-1.01`，未达到 `<=0.60` 阈值。
- non-engineering control 保持 `state_event_count=0`、`candidate_card_count=0`，未误触发工程建议卡。

结论：

主链路已从本地合成音频跑到 DRV-032 质量门，流程层面跑通；当前失败点不是 runner、postprocess 或 batch assembly，而是真实 ASR 输出质量。主要问题是中文会议中夹杂的英文技术词、服务名、指标名和错误码保存不稳定，例如服务名被连写、截断或误识别，导致技术实体召回低；同时当前每场景单进程加载/推理测得 RTF 偏高，不适合作为真实会议可用性的 Go evidence。

成本/隐私影响：

本轮未访问麦克风，未枚举音频设备，未请求 macOS 麦克风权限，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型。执行只读取已有合成音频与本地模型缓存，产物写入 ignored `artifacts/tmp/asr_events/**` 和 `artifacts/tmp/asr_reports/**`。

后续：

下一步不再新增泛化评测。默认主线是一次收敛型修复：先在不重跑 ASR 的前提下基于现有 provider/events artifacts 修复 deterministic hotword/normalizer 和技术词归一化，再重新跑 transcript report、single-result builder、DRV-046 和 DRV-032；如果 recall 仍不足，则进入本地 FunASR 参数/热词方案或显式降级试点取舍。RTF 不能靠文档或阈值放宽解决，后续需要把模型加载从每文件命令中剥离为长驻 worker/batch mode 后重新测，不能把当前 RTF 失败伪装成通过。真实麦克风继续 blocked，直到 ASR quality exit 通过或用户显式接受 degraded pilot 风险。

## DEC-200：实现技术词 deterministic normalizer 收敛与 FunASR batch runtime 测量

日期：2026-07-04

状态：Accepted

背景：

DEC-199 确认主链路已跑通到 ASR quality gate，但失败点落在两个维度：技术词召回不足和 RTF 偏高。用户要求按文档计划实现和自测完成，因此本轮只做两类收敛工作：基于已有 provider/events artifacts 修复可解释的技术词 near-miss；实现一个单进程复用 FunASR model 的 batch runtime 测量入口，验证 RTF 是否主要来自每文件模型加载。

决策：

- 在 `code/asr_runtime/scripts/transcript_normalizer.py` 增加 observed near-miss 归一化，不从 `<unk>` 或完全缺失文本中补实体。
- 在 `data/asr_eval/glossaries/technical-terms.zh.json` 增加已观察到的中英混合技术词别名：`paymentgateway`、`featurestore`、`dationservice`、`errorate`、`check koutservice`、`autoker` 等。
- 对 `字段 request`、`REDIScostbQPS`、`p九九/pp九 + speaker marker` 采用上下文规则，分别恢复 `request_id`、`redis cluster` 和 `P99`。
- 在 `code/asr_runtime/scripts/transcribe_funasr.py` 新增 `transcribe_streaming_batch()`，同一进程只加载一次 FunASR model，并把 `model_load_latency_ms` 与每段 `latency_ms/rtf` 分开记录。
- 不把 chunk 参数调优结果直接替换为质量门证据；chunk20 只作为 speed/quality trade-off 对照。

验证方式：

- TDD red/green：
  - `tests/test_transcript_normalizer.py::test_committed_technical_glossary_recovers_funasr_observed_near_misses_without_guessing_unseen_terms`
  - 先失败于 `paymentgateway`、`REDIScostbQPS`、`pp九b`、`pP99b` 等近似形未恢复；实现后通过。
- Runtime batch red/green：
  - `tests/test_transcribe_funasr.py::test_transcribe_streaming_batch_reuses_one_model_and_reports_per_file_rtf`
  - 先失败于 `transcribe_streaming_batch` 不存在；实现后确认 `AutoModel` 只初始化一次，多文件 per-item RTF 与 model load 分开记录，且不泄露真实本地模型路径。
- Focused regression：
  - `cd code/asr_runtime && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_transcript_normalizer.py tests/test_transcript_report.py tests/test_transcribe_funasr.py -q -p no:cacheprovider`
  - Result：`25 passed, 1 warning`。
- Final postprocess rerun：
  - 重新执行 10 条 postprocess command，不重跑原 DRV-045 provider command。
  - Result：`postprocess_executed=10`。
- DRV-046/DRV-032 rerun：
  - `assembly_status=drv044_batch_evidence_blocked`
  - `counts_as_asr_quality_go_evidence=false`
  - `decision_status=blocked_by_funasr_smoke_assembly_input_guard`
  - `quality_exit_status=not_exited`
  - `safe_to_capture_microphone_now=false`

主链路 recall 变化：

| Scenario | DEC-199 normalized recall | DEC-200 normalized recall | RTF |
| --- | ---: | ---: | ---: |
| `api-review-001` | `0.50` | `1.00` | `1.012` |
| `architecture-review-001` | `0.20` | `0.80` | `0.917` |
| `incident-review-001` | `0.25` | `0.50` | `0.960` |
| `release-review-001` | `0.00` | `0.75` | `0.984` |
| `non-engineering-control-001` | `0.00` | `0.00` | `0.920` |

Batch runtime 对照：

- `chunk_size=[0,10,5]` batch mode：`transcribe_only_rtf=0.679`，仍高于 `0.60`。
- `chunk_size=[0,20,10]` batch mode：`transcribe_only_rtf=0.358`，RTF 达标，但 ASR 文本质量下降；对应 technical recall 约为 `0.50 / 0.60 / 0.25 / 0.50`，不能作为质量门替代方案。

结论：

技术词 deterministic normalizer 已明显改善可恢复 near-miss，但不能修复 ASR 原文里缺失的 `timeout`、`监控阈值`、`staging` 等实体。长驻/batch mode 能证明模型加载是 RTF 的重要因素，且较大 chunk 可以让 RTF 过线，但会牺牲技术词质量。当前 ASR quality exit 仍未完成，真实麦克风仍 blocked。

成本/隐私影响：

本轮未访问麦克风，未枚举音频设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型。实际 ASR 复测只读取合成音频和已有本地缓存模型，产物写入 ignored `artifacts/tmp/**`。

后续：

下一步不应继续堆 normalizer 规则，也不应直接进入真实麦克风。可选主线只剩两个：其一，做受控 FunASR streaming 参数/热词机制，让 `chunk20` 速度优势尽量保留同时恢复技术词；其二，显式进入 degraded pilot 风险接受，只验证 timing/feedback，不宣称 ASR quality Go。

## DEC-201：跑真实主链路自测并确认热词参数不能解锁 ASR quality exit

日期：2026-07-04

状态：Accepted

背景：

用户要求“跑真实主链路自测，不要深入边界测试，先把主流程跑通”。DEC-200 已证明 deterministic normalizer 有改善，但 ASR quality exit 仍未通过；下一步不能继续泛化评测。因此本轮只把已生成的 FunASR hotword runtime provider/events artifacts 接入现有主链路：transcript report、single-result evidence、DRV-046 batch assembler、DRV-032 quality gate，以及产品侧 replay/shadow pipeline。

决策：

- 保留 `data/asr_eval/glossaries/funasr-hotwords.zh.json` 和 `transcribe_funasr.py` 的 hotword manifest 支持，作为可审计 provider 参数口子。
- 对 `chunk10_hotword` 与 `chunk20_hotword` 只做主链路判定，不扩大 provider bake-off。
- 使用 DRV-046/DRV-032 硬门禁判断，不放宽 `rtf <= 0.6` 和工程场景 normalized recall `>=0.8` 阈值。
- 用 FunASR 真实事件跑产品侧 replay/shadow pipeline，区分“ASR 质量问题”和“产品状态/建议链路问题”。
- 当前不访问麦克风、不读取真实录音、不调用远程 ASR/LLM、不增加新付费项。

实际结果：

| Candidate | RTF | Engineering normalized recall | DRV-046/DRV-032 |
| --- | ---: | ---: | --- |
| `chunk10_hotword` | `0.668-0.694` | `1.00 / 0.80 / 0.25 / 0.50` | blocked |
| `chunk20_hotword` | `0.355-0.363` | `0.50 / 0.60 / 0.25 / 0.50` | blocked |

质量门结果：

- `chunk10_hotword`：`assembly_status=drv044_batch_evidence_blocked`，`decision_status=blocked_by_funasr_smoke_assembly_input_guard`，`quality_exit_status=not_exited`。失败原因同时包含 RTF 超阈值和部分工程场景 normalized recall 低于 `0.8`。
- `chunk20_hotword`：`assembly_status=drv044_batch_evidence_blocked`，`decision_status=blocked_by_funasr_smoke_assembly_input_guard`，`quality_exit_status=not_exited`。RTF 达标，但四个工程场景均未达到 recall 阈值。
- non-engineering control 保持无工程候选，未出现负控误触发。

产品侧 replay/shadow pipeline 结果：

- `chunk10_hotword` 和 `chunk20_hotword` 均能让 `api-review-001`、`architecture-review-001`、`release-review-001` 生成 preview/candidate。
- 两组都在 `incident-review-001` 失败：`pipeline_status=blocked_by_no_candidate_timeline`，说明 ASR 文本丢失了足够多的事故上下文。
- mock control 仍通过：`engineering_preview_created_count=4`，`negative_control_blocked_count=1`，`negative_control_fake_candidate_count=0`。

结论：

真实主链路已经从已生成 FunASR artifacts 跑到最终质量门和产品 replay。当前不是流程没跑通，也不是产品 card pipeline 完全不可用，而是本地 FunASR 对中文技术会议中的服务名、指标名、错误码、事故术语仍不稳定。热词 manifest 对当前模型/参数没有带来足够收益，`chunk20` 的速度收益以质量回退为代价，不能作为主线替代。

成本/隐私影响：

本轮未访问麦克风，未枚举音频设备，未请求权限，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，没有产生中转站费用。

后续：

默认主线不再继续做开放式 ASR 边界评测。下一步应推进 PC 端产品主流程，以 synthetic/live-event replay 明确展示 ASR quality blocked 的状态；若要提前触达真实麦克风，只能走显式 degraded pilot 风险接受，且该试点只验证 timing/feedback，不算 ASR quality Go evidence。

## DEC-202 / PCWEB-128：实现主线 ASR-blocked 试运行入口

日期：2026-07-04

状态：Accepted

背景：

DEC-201 已确认主链路不是没跑通，而是 ASR 质量未过。继续做开放式 ASR 测评会偏离产品初心；但如果只停在文档结论，PC 端工作台仍像一组分散模拟入口，用户无法直接看到“产品流能跑，但真实麦克风被 ASR quality 阻断”的主线状态。

决策：

- 新增 PCWEB-128 `Mainline ASR-Blocked Trial`。
- 后端提供 `POST /desktop/mainline-asr-blocked-trial/sessions`，创建本地 synthetic Live ASR session，并返回 DEC-201 质量阻断摘要。
- 前端新增 `主线试运行` 按钮，复用现有 Live ASR SSE 和 Shadow MVP panel。
- 面板必须展示 `DEC-201`、`not_exited`、`blocked_by_funasr_smoke_assembly_input_guard`、`chunk10_hotword`、`chunk20_hotword`、`incident-review-001` 和下一步 `continue_pc_product_flow_keep_real_mic_blocked`。
- 不把该入口写成真实麦克风或 ASR Go；它只是产品主流程与质量阻断的可视化试运行。

实现：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
  - `CreateMainlineAsrBlockedTrialSessionRequest`
  - `POST /desktop/mainline-asr-blocked-trial/sessions`
  - `_mainline_asr_blocked_trial_response`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
  - `mainline-asr-blocked-trial-button`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
  - `loadMainlineAsrBlockedTrial`
  - `renderMainlineAsrBlockedTrial`
- `code/web_mvp/e2e/browser_smoke.mjs`
  - 点击主线试运行并验证 SSE 与阻断摘要。

验证：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker \
  tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 2 passed, 2 warnings
```

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline ASR blocked trial"
```

成本/隐私影响：

本轮未访问麦克风，未枚举设备，未请求权限，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，没有新增中转站费用。

后续：

PC 端现在有一个主线入口能表达当前真实状态。下一步应继续补产品可用性：围绕该入口扩展“建议候选 -> 用户反馈 -> 报告导出”的闭环，而不是重启 ASR provider 横评。真实麦克风仍需要 ASR quality exit 或显式 degraded pilot 接受。

## DEC-203：跑 PC 产品主流程 smoke 并停止扩展边界评测

日期：2026-07-04

状态：Accepted

背景：

用户再次明确要求不要继续深入边界测试，先把真实主流程跑通。本轮将“真实主流程”限定为当前已经实现的产品程序路径，而不是麦克风或私有音频路径：

```text
mainline endpoint / workbench
  -> Live ASR trial session
  -> transcript final/revision
  -> EvidenceSpan/state/scheduler/suggestion candidate
  -> no-call LLM request draft
  -> draft review/report data
  -> ASR quality blocked / real mic blocked visible
```

执行：

- `GET /health` 返回 `{"status":"ok","service":"meeting-copilot-web-mvp"}`。
- `POST /desktop/mainline-asr-blocked-trial/sessions` 创建 `manual_true_mainline_selftest_20260704`。
- focused pytest：`1 passed, 2 warnings`。
- browser smoke：`status=ok`，checked 包含 `mainline ASR blocked trial`。

主流程观测：

- `transcript_final=13`
- `transcript_revision=3`
- `state_event=17`
- `scheduler_event=17`
- `suggestion_candidate_event=17`
- `llm_request_draft_event=17`
- draft review：`transcript_segments=16`、`evidence_spans=19`、`state_candidates=17`、`suggestion_candidates=17`
- LLM 状态保持 `not_called`
- real mic readiness 仍为 `blocked_not_ready_for_user_real_mic_shadow_test`

结论：

PC 产品主流程 smoke 已跑通：当前不是“产品链路没有接起来”，而是“真实 ASR quality 与真实麦克风 readiness 尚未解锁”。后续不应再把同类边界/readiness 测试作为主线；当时默认进入的 `PCWEB-129 Mainline Trial Feedback And Export Closure` 已由 DEC-205 完成。

边界：

本轮未访问麦克风，未枚举设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，没有新增付费项。

## DEC-204 / PCWEB-130：安装 UI/UX Pro Max 并重设计 PC Web 工作台视觉

日期：2026-07-04

状态：Accepted

背景：

用户要求安装 `ui-ux-pro-max-skill`，并按该 skill 改善展示页，因为当前页面太粗糙。

执行：

- 运行官方安装命令 `npx --yes ui-ux-pro-max-cli init --ai codex`，生成项目级 `.codex/skills/ui-ux-pro-max`。
- 同步 `ui-ux-pro-max` 到全局 `~/.codex/skills/ui-ux-pro-max`，供 Codex 重启后发现。
- 按 skill 要求执行 design-system search，查询对象为 `AI meeting copilot developer tool realtime desktop dashboard B2B productivity`。
- 保留暗色开发者工具、Inter/system 字体、微交互、focus-visible、reduced-motion、状态反馈等建议；放弃其误判出的 landing-page pattern，因为本项目是工作台，不是营销页。

实现：

- `index.html` 新增 `app-shell`、`brand-lockup`、`brand-mark`、分组 toolbar 和 `mainline-status-strip`。
- `styles.css` 重构为深色专业工作台视觉：暗色 token、绿色主 CTA、琥珀 blocked chip、红色危险按钮、深色 panel/tile、移动端自适应工具栏和状态条。
- `test_app.py::test_workbench_static_assets_are_served` 加入新视觉骨架断言。

验证：

```text
TDD red: test_workbench_static_assets_are_served 失败于缺少 class="app-shell"
Focused green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline ASR blocked trial"
Visual screenshots:
  artifacts/tmp/ui_screenshots/workbench-home-dark-v4.png
  artifacts/tmp/ui_screenshots/workbench-mobile-dark-v4.png
Mobile layout probe: scrollWidth == clientWidth, no overflow offenders
```

边界：

本轮只改 UI 展示和静态测试，不访问麦克风，不读取用户音频，不调用远程 ASR/LLM，不改变 ASR quality gate 或真实麦克风 readiness。

## DEC-205 / PCWEB-129：实现主线试运行后的反馈与导出预览闭环

日期：2026-07-04

状态：Accepted

背景：

DEC-203 已证明 PC 产品主流程能从 `mainline_asr_blocked_trial` 跑到 Live ASR draft review，但仍停在“试运行可见”。为了避免主线退化成转写工具，PCWEB-129 将主线继续推进到产品价值闭环：

```text
mainline trial session
  -> suggestion candidates
  -> deterministic local feedback
  -> Markdown/JSON export preview
  -> explicit not-Go evidence
```

实现：

- Web backend 新增 `POST /desktop/mainline-trial-feedback-export-closures`。
- Endpoint 只读取已有 `mainline_asr_blocked_trial` Live ASR session。
- 从 Live ASR draft review 派生 DRV-033-compatible candidate report。
- 默认选择前两个 suggestion candidate，并写入 deterministic feedback：
  - first candidate: `useful`
  - second candidate: `would_have_asked`
- 复用 DRV-038 `shadow_report_feedback_ingestion` 和 DRV-036 `shadow_report_ingestion_export_feedback`，不重写反馈/导出判断。
- Web 工作台新增 `闭环预览` 按钮和 `主线闭环` 面板；点击后显示 closure/export/not-Go 状态，并把 Markdown export preview 写入既有 `report-panel`。

关键结论：

- `export_readiness_status=draft_export_preview_only`
- `go_evidence_status=not_go_evidence_replay_or_feedback_missing`
- `final_decision=inconclusive_requires_more_shadow_tests`

这说明当前主线闭环已经能展示“建议是否有价值”和“报告会长什么样”，但 synthetic/replay feedback 仍不能作为真实麦克风 Go 证据。

验证：

```text
Backend TDD red:
  test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence
  failed with 404 before endpoint existed

Backend focused green:
  1 passed, 2 warnings

Static UI TDD red:
  test_workbench_static_assets_are_served
  failed because mainline-feedback-export-closure-button did not exist

Static UI focused green:
  1 passed, 2 warnings

Browser E2E:
  node code/web_mvp/e2e/browser_smoke.mjs
  status=ok
  checked includes "mainline trial feedback export closure"
```

审查后加固：

- 补齐 missing session 返回 404 的回归测试。
- 补齐 non-mainline session 返回 `blocked_by_source_trial` / 422 的回归测试。
- 补齐默认 deterministic feedback 明细断言：`useful=1`、`would_have_asked=1`、`negative_feedback_count=0`。
- 补齐安全边界 sentinel：closure 不得读取 LLM config/secret、不得探测 native audio/process、不得发起 outbound call、不得写 `shadow_report_exports` 导出文件。
- 浏览器 smoke 追加断言 UI 中可见 `inconclusive_requires_more_shadow_tests`、`positive=2`、`negative=0` 和 selected candidate id。

最终回归：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest code/web_mvp/backend/tests/test_app.py -q -p no:cacheprovider
Result: 292 passed, 2 warnings

node code/web_mvp/e2e/browser_smoke.mjs
Result: status=ok, checked includes "mainline trial feedback export closure"

Sensitive scan
Result: no matches
```

边界：

本轮未访问麦克风，未枚举设备，未读取真实用户录音或 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，未调用远程 ASR/LLM，未下载模型或公开音频，未写导出文件，没有新增付费项。

## DEC-204: Real Microphone Mainflow Ran, Product Value Blocked By ASR Quality

状态：Accepted

背景：

用户明确要求开启真实麦克风，跑真实全链路主流程，不再继续无限边界评估。本轮目标是验证：

```text
real microphone
  -> local ASR
  -> ASR event file
  -> Web Live ASR handoff
  -> draft/report artifact
```

实现与修复：

- 新增 `tools/real_mic_full_chain_runner.py` 作为真实麦克风主链路 runner。
- 新增 `tests/test_real_mic_full_chain_runner.py`，覆盖：
  - 不调用远程 ASR/LLM、不读本地密钥配置的 traceable artifacts；
  - unsafe session id guard；
  - microphone capture timeout；
  - file-replayed ASR event timestamp adaptation for Web handoff。
- 扩展 `tools/real_mic_shadow_test_report_schema.py`，允许用户批准真实麦克风测试后，将音频暂存在 ignored artifact root 以便人工复核。

真实麦克风结果：

- `real_mic_20260704_mainflow_003`：麦克风采集成功，Sherpa 识别出 `真是麦克风主流承测试`，timestamp adaptation 后 Web handoff `201`，但没有工程状态或建议候选。
- `real_mic_20260704_mainflow_004`：麦克风采集成功，Web handoff `201`，但 Sherpa 没有 final transcript。
- 同一段真实录音用 FunASR streaming/non-streaming 复核，分别得到 `我是我样` / `六 六`，技术词召回为 0。

关键结论：

- Mac 真实麦克风采集可行。
- Web Live ASR handoff 可接真实麦克风衍生事件；原先 422 是文件回放时间戳未适配实时契约，已修。
- 当前零成本本地 ASR provider 不能支撑中文技术会议产品价值。
- 当前不能宣称真实全链路产品价值跑通，因为没有可靠 transcript、没有工程 state、没有 suggestion candidate。
- 下一主线应先确定 real-meeting ASR provider strategy，再继续做 UI 或建议逻辑。

边界：

- 本轮未读取旧 `.m4a`，未读取 `configs/local`、`data/asr_eval/local_samples` 或 `data/local_runtime`，未上传音频，未调用远程 ASR/LLM，未产生 provider 费用。
- 注意：一次 FunASR non-streaming 复核触发了 ModelScope 对 VAD alias 的 metadata/README 拉取。不是远程 ASR/LLM，也未上传音频，但说明 FunASR non-streaming 脚本尚不能宣称 strict offline-only，后续必须把 VAD/punc 依赖也显式绑定到本地目录。

证据文档：

- `docs/asr-mainchain-fullflow-selftest-2026-07-04.md`

## DEC-206：把 Audio Capture Healthcheck 设为生产可用性的第一道闸门

日期：2026-07-04

状态：Accepted

背景：

用户要求不要继续陷入无止境测评，要先把主流程跑通，并在适当时机走真实麦克风/真实链路。真实麦克风主流程已经证明 Mac 能采集音频、Web handoff 能接入 ASR event，但当前本地 ASR 对中文技术会议的识别质量不足，导致没有稳定 transcript、工程 state 和 suggestion candidate。

关键判断：

- 外放声音再由笔记本麦克风采集只适合 smoke，不是生产级验证路径。
- 继续用 speaker-to-microphone 样本比较 ASR provider 会混淆“采集质量差”和“模型质量差”两个问题。
- 后续 provider bake-off 必须建立在 clean capture track 上，否则结论不可信。
- 产品主线价值仍是实时会议 Copilot，不是录音转文字工具；因此音频输入质量必须先成为进入 ASR/LLM 前的硬闸门。

实现：

- 新增 `tools/audio_capture_healthcheck.py`。
- 新增 `tests/test_audio_capture_healthcheck.py`。
- 新增 `docs/audio-capture-production-readiness-plan.md`。
- 工具只分析批准目录内的 WAV 文件，默认不录音、不调用远程 ASR/LLM、不读取密钥、不上传音频。
- 可显式使用 `ffmpeg avfoundation` 录制短麦克风样本；录音输出路径必须先通过同一套 path guard，超时和错误都会返回标准 health report blocker。
- 审查后加固生产闸门：只有 `16 kHz mono 16-bit PCM WAV` 且至少 `10 seconds` 的样本能进入 `audio_capture_health_passed`；错误采样率、stereo、多声道和短样本会阻断。

健康闸门状态：

```text
audio_capture_health_passed
blocked_by_path_guard
blocked_by_wav_read_error
blocked_audio_too_short
blocked_audio_too_quiet
blocked_no_clear_speech
blocked_audio_clipping
blocked_unsupported_wav_format
blocked_missing_audio_path
blocked_by_microphone_capture_path_guard
blocked_by_microphone_capture_timeout
blocked_by_microphone_capture_error
```

验证：

```text
Focused:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audio_capture_healthcheck.py -q -p no:cacheprovider
Result: 11 passed, 1 warning

Adjacent regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_audio_capture_healthcheck.py tests/test_real_mic_full_chain_runner.py tests/test_real_mic_shadow_test_report_schema.py -q -p no:cacheprovider
Result: 29 passed, 1 warning
```

下一步：

```text
M2: Mac system audio digital capture spike
```

执行边界：

本轮未启动真实麦克风录制，未读取旧用户音频，未读取本地密钥配置，未调用远程 ASR/LLM，未上传音频，未新增付费项目。

## DEC-207：修正主目标为“完整主链路可用化”，不是只做自测报告

日期：2026-07-04

状态：Accepted

背景：

用户指出上一版目标偏窄：目标不应只是“完成完整主链路自测”，还必须包含修复、自测中发现的问题处理、补充未实现功能，以及最终形成可用闭环。这个反馈成立。

修正后的目标：

```text
audio input readiness
  -> ASR event generation or approved replay
  -> Web Live ASR handoff
  -> transcript / EvidenceSpan
  -> meeting state extraction
  -> suggestion candidates
  -> LLM request draft or explicit no-call blocker
  -> feedback / export preview
  -> final self-test report
  -> gap list with fixes or explicit blockers
```

执行原则：

- 自测不是终点；自测发现的本地产品链路缺口必须修。
- 在当前 local/no-paid/no-private-data 边界内能补的功能要补齐。
- 只有需要用户显式授权、真实音频、系统音频权限、远程 ASR/LLM、付费 provider 或生产级 ASR 证据的部分，才允许作为 blocker 留下。
- 后续完成标准必须包含：实现、修复、回归测试、浏览器 smoke、敏感扫描、可追溯报告和下一步 blocker 清单。

立即执行项：

```text
M1.5 Mainline Usable E2E Runner
```

该 runner 应把 M1 healthcheck、Web mainline ASR-blocked trial、Live ASR events、draft review、feedback/export closure 和最终 JSON/Markdown 报告串成一个本地命令。它不调用远程 ASR/LLM，不读取 secrets，不读取旧录音，不上传音频；如果发现本地产品链路断点，必须修复后再宣称通过。

证据文档：

- `docs/mainline-usable-e2e-goal-2026-07-04.md`

## DEC-208：实现 M1.5 Mainline Usable E2E Runner 并跑通完整本地主链路自测

日期：2026-07-04

状态：Accepted

背景：

DEC-207 修正目标后，主线需要一个可执行 runner，而不是继续依赖分散测试、手工 curl、浏览器 smoke 和文档汇总。M1.5 的目的不是证明真实 ASR 已可用，而是把当前 PC 本地主链路完整串起来，并把剩余缺口分类为已实现、已修复或明确 blocker。

实现：

- 新增 `tools/mainline_usable_e2e_runner.py`。
- 新增 `tests/test_mainline_usable_e2e_runner.py`。
- 新增 `docs/mainline-usable-e2e-selftest-2026-07-04.md`。
- Runner 生成 approved synthetic WAV，先跑 M1 `audio_capture_healthcheck`。
- Runner 使用 FastAPI `TestClient(create_app())` 走真实本地接口：
  - `POST /desktop/mainline-asr-blocked-trial/sessions`
  - `GET /live/asr/sessions/{session_id}/events`
  - `GET /live/asr/sessions/{session_id}/draft.md`
  - `POST /desktop/mainline-trial-feedback-export-closures`
- Runner 可选执行 `node code/web_mvp/e2e/browser_smoke.mjs`，本轮真实执行并通过。
- Runner 写出 JSON/Markdown artifact：
  - `artifacts/tmp/mainline_selftests/m15_full_selftest_20260704.mainline-usable-e2e.json`
  - `artifacts/tmp/mainline_selftests/m15_full_selftest_20260704.mainline-usable-e2e.md`

自测结果：

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
audio_health=audio_capture_health_passed
trial_status=mainline_trial_session_created
closure_status=mainline_trial_feedback_export_preview_created
browser_smoke_status=passed
final_decision=inconclusive_requires_more_shadow_tests
```

Live ASR event counts:

```text
transcript_partial=5
transcript_final=13
transcript_revision=3
state_event=17
scheduler_event=17
suggestion_candidate_event=17
llm_request_draft_event=17
evaluation_summary=1
```

Gap classification:

```text
implemented_and_verified=5
blocked_by_asr_quality=1
blocked_requires_m2_system_audio_capture=1
blocked_requires_explicit_user_approval=1
```

验证：

```text
Focused RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 3 failed because tools/mainline_usable_e2e_runner.py did not exist

Focused GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 3 passed, 2 warnings

Adjacent regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py tests/test_audio_capture_healthcheck.py code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_asr_blocked_trial_creates_live_session_and_reports_dec201_quality_blocker code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_trial_feedback_export_closure_creates_preview_not_go_evidence -q -p no:cacheprovider
Result: 16 passed, 2 warnings

CLI selftest with browser smoke:
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py --session-id m15_full_selftest_20260704 --run-browser-smoke
Result: exit 0, browser_smoke_status=passed
```

边界：

本轮没有读取旧录音或 `.m4a`，没有读取 `configs/local`、`data/asr_eval/local_samples`、`data/local_runtime` 或 `outputs`，没有上传音频，没有调用远程 ASR/LLM，没有使用付费 provider，没有启动真实麦克风录制，也没有请求系统音频权限。浏览器 smoke 仅启动本地 backend 和 headless Chrome。

下一步：

```text
M2 Mac system audio digital capture
```

后续不应继续增加 report-only wrapper；剩余最高价值缺口是生产级音频输入。

## DEC-209：M2 Mac 系统音频采集适配层采用显式、本地、健康门控方案

日期：2026-07-04

状态：Accepted

背景：

M1.5 已经证明本地主链路可以从合成 WAV healthcheck 进入 Web mainline trial、Live ASR event、EvidenceSpan、state、suggestion candidate、no-call LLM draft 和 feedback/export preview。剩余最高价值缺口是生产级音频输入，而不是继续增加同类 report-only 包装。

决策：

新增 M2 Mac system audio capture adapter，但默认不录制、不请求权限、不读取私密音频、不调用远程 ASR/LLM、不使用付费 provider。

实现：

- 新增 `tools/mac_system_audio_capture_adapter.py`。
- 新增 `tests/test_mac_system_audio_capture_adapter.py`。
- 新增 `docs/mac-system-audio-capture-m2-plan-2026-07-04.md`。
- 更新 `tools/mainline_usable_e2e_runner.py`，在主链路报告中加入 `system_audio_capture`。
- 更新 `tests/test_mainline_usable_e2e_runner.py`，覆盖默认 preflight blocker 和 M2 health pass 后的 gap 变化。

默认模式：

```text
capture_adapter_status=preflight_only_not_capturing
capture_backend=ffmpeg_avfoundation_explicit_device
recommended_route=virtual_system_audio_device_first
screen_capturekit_status=future_native_path_not_implemented
safe_to_capture_system_audio_now=false
safe_to_request_system_audio_permission_now=false
```

显式录制命令契约：

```text
ffmpeg -hide_banner -nostdin -y -f avfoundation -i :<device_index> -t <seconds> -ac 1 -ar 16000 -sample_fmt s16 <audio_path>
```

路径和边界：

- 输出 WAV 必须通过 M1 audio healthcheck 的 approved-root/path guard。
- 采集前先校验路径，路径不合法时不创建目录、不启动 `ffmpeg`。
- `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/` 继续阻断。
- process error summary 进入 JSON 前做用户路径和旧音频标记脱敏。
- 所有 privacy/cost flags 保持 false。

主链路分类：

```text
默认 preflight:
mac_system_audio_capture -> blocked_requires_m2_system_audio_capture

M2 report audio_health.health_status=audio_capture_health_passed:
mac_system_audio_capture -> implemented_and_verified
```

但即使 M2 health pass，也不能移除：

```text
production_asr_quality -> blocked_by_asr_quality
real_meeting_go_evidence -> blocked_requires_explicit_user_approval
```

原因：

M2 证明的是 clean digital capture readiness，不是 ASR 生产质量，也不是真实会议 Go evidence。

验证：

```text
M2 adapter RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py -q -p no:cacheprovider
Result: 7 failed because tools/mac_system_audio_capture_adapter.py did not exist

M2 adapter GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py -q -p no:cacheprovider
Result: 8 passed, 1 warning

Mainline integration RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 2 failed, 2 passed because runner had no system_audio_capture report/parameter

Mainline integration GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 4 passed, 2 warnings

Browser smoke redaction hardening:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_browser_smoke_output_redacts_local_absolute_paths -q -p no:cacheprovider
Result: 1 passed, 2 warnings

Adjacent regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py tests/test_audio_capture_healthcheck.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 26 passed, 2 warnings

Syntax:
python3 -m py_compile tools/mac_system_audio_capture_adapter.py tests/test_mac_system_audio_capture_adapter.py tools/mainline_usable_e2e_runner.py tests/test_mainline_usable_e2e_runner.py
Result: exit 0

M2 preflight CLI:
PYTHONDONTWRITEBYTECODE=1 python3 tools/mac_system_audio_capture_adapter.py --preflight-only
Result: capture_adapter_status=preflight_only_not_capturing

Mainline CLI with browser smoke:
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py --session-id m2_final_mainline_selftest_20260704 --run-browser-smoke
Result: exit 0, overall_status=mainline_product_chain_exercised_with_expected_blockers, browser_smoke_status=passed

Sensitive scan:
Result: no matches
```

只读审查闭环：

- 修复：`mac_system_audio_capture` 只能由 `audio_health.health_status=audio_capture_health_passed` 清除，不能只依赖顶层 `capture_adapter_status`。
- 修复：`--output-root` 位于 repo 外时，报告中的 artifact path 写为 `<redacted_outside_repo_path>`，不序列化本机绝对路径。
- 加固：M2 health pass 测试显式断言 `production_asr_quality` 仍为 `blocked_by_asr_quality`。

M2.1 主链路显式系统音频 capture 入口：

- `tools/mainline_usable_e2e_runner.py` 新增 `--system-audio-record-seconds`、`--system-audio-device-index`、`--system-audio-output-path`。
- 只有 `--system-audio-record-seconds > 0` 才调用 `mac_system_audio_capture_adapter.record_system_audio_sample`。
- capture result 会通过 `build_system_audio_capture_health_report` 进入 M1 health gate，再参与 `mac_system_audio_capture` gap 分类。
- 自动测试使用 monkeypatch recorder/health wrapper，不启动真实 `ffmpeg`，不触碰设备。

验证：

```text
M2.1 RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_explicit_system_audio_capture_uses_adapter_and_health_gate tests/test_mainline_usable_e2e_runner.py::test_cli_explicit_system_audio_capture_calls_recorder_without_remote_services -q -p no:cacheprovider
Result: 2 failed because runner did not normalize capture result and CLI did not recognize system-audio args

M2.1 GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_explicit_system_audio_capture_uses_adapter_and_health_gate tests/test_mainline_usable_e2e_runner.py::test_cli_explicit_system_audio_capture_calls_recorder_without_remote_services -q -p no:cacheprovider
Result: 2 passed, 2 warnings

Mainline runner:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 9 passed, 2 warnings

Adjacent regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mac_system_audio_capture_adapter.py tests/test_audio_capture_healthcheck.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 28 passed, 2 warnings
```

下一步：

在用户明确授权并完成 Mac 虚拟系统音频输入配置后，才允许真实 Mac virtual-system-audio health capture。未授权前继续保持 preflight-only。

## DEC-210：本地主链路 artifact retention/delete 边界落地

日期：2026-07-04

状态：Accepted

背景：

产品需要保存录音、转写和报告用于复盘，但这必须同时具备明确删除语义。隐私文档已经要求用户可暂停、停止、删除，且删除应覆盖音频、转写、中间结果和导出物。当前主链路已经写出本地 ignored artifacts，因此需要先补齐本地 retention/delete 边界，避免真实会议音频落地后没有生命周期控制。

实现：

- 新增 `tools/local_artifact_retention.py`。
- 新增 `tests/test_local_artifact_retention.py`。
- 新增 `docs/local-artifact-retention-delete-2026-07-04.md`。
- 更新 `tools/mainline_usable_e2e_runner.py`，在每次主链路报告中写入 `artifact_retention` summary。
- 更新 `tests/test_mainline_usable_e2e_runner.py`，验证主链路报告包含 JSON、Markdown 和 audio health WAV 三个 artifact 的 retention 状态。

默认策略：

```text
retention_status=local_artifacts_retained
retention_policy=local_artifacts_retained_until_explicit_delete
```

显式删除：

```text
--delete
```

边界：

- 只允许 approved ignored runtime roots。
- 不读取 artifact 内容，只记录 path、existence、size 和 action。
- forbidden roots、repo outside paths 在 delete 前阻断。
- 不上传音频、不调用 ASR/LLM、不读取 `configs/local`、不使用付费 provider。

验证：

```text
TDD RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py -q -p no:cacheprovider
Result: 4 failed because tools/local_artifact_retention.py did not exist

Focused GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py -q -p no:cacheprovider
Result: 4 passed, 1 warning

Mainline integration:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 13 passed, 2 warnings

Adjacent regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_local_artifact_retention.py tests/test_mac_system_audio_capture_adapter.py tests/test_audio_capture_healthcheck.py tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 32 passed, 2 warnings

Syntax:
python3 -m py_compile tools/local_artifact_retention.py tests/test_local_artifact_retention.py tools/mainline_usable_e2e_runner.py tests/test_mainline_usable_e2e_runner.py
Result: exit 0
```

下一步：

默认主链路继续保留 artifacts 供复盘；真实桌面 capture runtime 激活后，需要把 pause/stop/delete UI 和生产用户数据目录接入同一删除语义。

## DEC-211：主链路 Copilot 复盘预览成为一等报告字段

日期：2026-07-04

状态：Accepted

背景：

用户明确要求产品不能退化成“音频转文字工具”。本轮主链路 smoke 发现本地链路已经有 transcript、EvidenceSpan、state、suggestion candidate、LLM request draft 和 feedback/export preview，但 runner 只把 `/draft.md` 归纳成 `draft_review_created`，正式复盘预览仍显示 `formal_report_status=not_created`。这会让交付物看起来只是在统计转写事件，没有呈现会议 Copilot 的产品价值。

实现：

- 更新 `tools/mainline_usable_e2e_runner.py`。
- 更新 `tests/test_mainline_usable_e2e_runner.py`。
- runner 现在读取 `/live/asr/sessions/{session_id}/draft` 的结构化 JSON，并生成 `copilot_report_preview`。
- `draft_review.formal_report_status` 现在为 `formal_report_preview_created`。
- Markdown 报告新增 `Copilot Report Preview` 章节，列出 value chain、meeting state count、suggestion candidate count、LLM request draft count、closure decision、quality blockers、top state items 和 top suggestion candidates。

边界：

- 不调用 LLM。
- 不把 preview 标成真实会议 Go evidence。
- 不调用远程 ASR。
- 不读取私有音频或 secrets。

TDD 验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_executes_mainline_and_writes_traceable_reports -q -p no:cacheprovider
Result: failed because formal_report_status was still not_created

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_executes_mainline_and_writes_traceable_reports -q -p no:cacheprovider
Result: 1 passed, 2 warnings
```

结论：

主链路报告现在能直接证明当前产品链路的核心价值：

```text
transcript
  -> evidence_span
  -> meeting_state
  -> suggestion_candidate
  -> llm_request_draft
  -> feedback_export_preview
```

仍然要明确：这是本地 synthetic/mock/replay 复盘预览，不是生产 ASR 质量通过，也不是真实会议 Go evidence。

## DEC-212：主链路接入 ASR quality decision evidence

日期：2026-07-04

状态：Accepted

背景：

多 Agent 只读审查指出，当前 PC 主链路最大风险不是 UI 或 readiness 文档，而是 ASR quality exit 仍未通过，并且 mainline runner 默认只显示 endpoint 内部的静态 blocker。仓库中已有本地 FunASR synthetic smoke 的 ASR quality decision artifacts，且实际结论是 `blocked_by_funasr_smoke_assembly_input_guard`，主要原因包括 batch evidence 未 validated 和工程场景 normalized recall 未达到 `0.8`。主链路需要能消费这个证据，而不是继续只用 hardcoded mock blocker。

实现：

- 更新 `tools/mainline_usable_e2e_runner.py`。
- 更新 `tests/test_mainline_usable_e2e_runner.py`。
- 新增函数参数 `asr_quality_decision`。
- 新增 CLI 参数 `--asr-quality-decision-path`。
- 只允许读取 approved `artifacts/tmp/asr_reports/*.json` ASR quality decision artifact。
- `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/`、repo 外路径、`.m4a` 和非 JSON 在读取前阻断。
- 主报告新增 `asr_quality` 字段，`production_asr_quality` gap 现在使用实际 decision evidence 生成 detail。

TDD 验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py::test_runner_can_ingest_blocked_asr_quality_decision_evidence \
  tests/test_mainline_usable_e2e_runner.py::test_cli_loads_asr_quality_decision_path_from_approved_artifact \
  tests/test_mainline_usable_e2e_runner.py::test_cli_blocks_asr_quality_decision_path_outside_approved_artifacts \
  -q -p no:cacheprovider
Result: 3 failed because function/CLI support did not exist

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py::test_runner_can_ingest_blocked_asr_quality_decision_evidence \
  tests/test_mainline_usable_e2e_runner.py::test_cli_loads_asr_quality_decision_path_from_approved_artifact \
  tests/test_mainline_usable_e2e_runner.py::test_cli_blocks_asr_quality_decision_path_outside_approved_artifacts \
  -q -p no:cacheprovider
Result: 3 passed, 2 warnings

Mainline runner:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 12 passed, 2 warnings
```

真实已有 ASR quality artifact 入口验证：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_evidence_mainline_20260704 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json
Result: exit 0
```

关键结论：

```text
asr_quality.source_status=provided_asr_quality_decision_report
asr_quality.decision_status=blocked_by_funasr_smoke_assembly_input_guard
asr_quality.quality_exit_status=not_exited
production_asr_quality=blocked_by_asr_quality
gap_summary.implemented_and_verified=6
```

当前不能宣称 ASR quality 已通过。下一步不应继续堆 readiness/report-only wrapper，而应做两类工作之一：

- 修复/改进本地 ASR synthetic smoke 质量或参数，让 DRV-046/044/032 真正通过。
- 在主链路支持 approved ASR event artifact 进入 Web Live ASR handoff，减少 hardcoded mock 对产品主线的占比。

## DEC-213：主链路 Runner 接入 approved ASR event artifact handoff

日期：2026-07-04

状态：Accepted

背景：

DEC-212 让主链路可以读取 ASR quality decision evidence，但主线仍有一个本地可实现缺口：Web 端已经支持 `/live/asr/local-event-files/sessions`，但 `tools/mainline_usable_e2e_runner.py` 没有把 approved ASR event artifact 作为一条总控自测分支执行。这样产品主线仍主要依赖 hardcoded mock trial，不能在同一个报告里证明“真实/本地 ASR event artifact 可进入 Web Live ASR handoff”。

实现：

- 更新 `tools/mainline_usable_e2e_runner.py`。
- 更新 `tests/test_mainline_usable_e2e_runner.py`。
- 新增函数参数 `asr_events_path` 和 `asr_events_provider`。
- 新增 CLI 参数 `--asr-events-path` 和 `--asr-events-provider`。
- runner 通过现有 Web API `/live/asr/local-event-files/sessions` 创建辅助 handoff session，避免和 mainline trial session id 冲突。
- 主报告新增 `asr_event_handoff` 字段。
- 当传入 event artifact 时，gap entries 新增 `asr_event_artifact_handoff`。
- Markdown 报告新增 `ASR Event Artifact Handoff` 章节。

TDD 验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_can_handoff_approved_asr_event_artifact -q -p no:cacheprovider
Result: failed because run_mainline_usable_e2e_selftest did not accept asr_events_path

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py::test_runner_can_handoff_approved_asr_event_artifact -q -p no:cacheprovider
Result: 1 passed, 2 warnings

Mainline runner:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_mainline_usable_e2e_runner.py -q -p no:cacheprovider
Result: 13 passed, 2 warnings
```

总控 smoke：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_handoff_verified_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/api-review-001.mock.events.json \
  --asr-events-provider local_mock_asr_artifact
Result: exit 0
```

关键结论：

```text
asr_event_handoff.handoff_status=local_asr_event_file_handoff_created
asr_event_handoff.event_source.is_mock=false
asr_event_handoff.live_event_counts.transcript_final=3
asr_event_handoff.live_event_counts.state_event=1
asr_event_handoff.live_event_counts.suggestion_candidate_event=1
asr_event_handoff.live_event_counts.llm_request_draft_event=1
asr_event_artifact_handoff=implemented_and_verified
gap_summary.implemented_and_verified=7
```

边界：

- 只通过现有 Web handoff API 读取 approved `artifacts/tmp/asr_events/*.json`。
- 不读取音频。
- 不启动 worker。
- 不访问麦克风。
- 不调用远程 ASR/LLM。
- 不把 artifact handoff 误标成 ASR quality Go evidence 或真实会议 Go evidence。

下一步：

主线剩余本地优先级进一步收敛为：

```text
1. 修复/优化本地 FunASR synthetic smoke 质量，让 DRV-046/044/032 通过，或形成 Stop/Pivot 决策。
2. 把 ASR event artifact handoff 从 runner 辅助分支推进为 mainline trial 可选输入，进而支持 feedback/export closure 直接基于 artifact session。
3. 用户明确授权后，再跑真实 Mac system audio / mic health capture。
```

## DEC-214：ASR event artifact 升级为 mainline trial / feedback closure 输入

日期：2026-07-04

状态：Accepted

背景：

DEC-213 证明 approved ASR event artifact 可以进入 Web Live ASR handoff，但它仍是 runner 的辅助 session；主 session、draft review、feedback/export closure 仍然默认来自 `mainline_asr_blocked_trial`。这会让总控报告只能证明“旁路 handoff 成功”，不能证明产品核心链路可以围绕一份 ASR 事件工件闭环。用户要求不要继续只做测评和 wrapper，因此本轮把 event artifact 推进到主链路。

实现：

- 更新 `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`。
- 更新 `code/web_mvp/backend/tests/test_app.py`。
- 新增 Web endpoint：`POST /desktop/mainline-asr-event-artifact-trial/sessions`。
- endpoint 复用现有 local ASR event path guard、event contract validation 和 `build_asr_live_events(...)`。
- 该 endpoint 创建的 session 使用 `ingest_mode=mainline_asr_event_artifact_trial`，并保留 `events_path`、`event_source`、`source_event_artifact_status`。
- `POST /desktop/mainline-trial-feedback-export-closures` 现在允许 `mainline_asr_event_artifact_trial` 作为 source trial，并在 closure preview 中回填 `source_trial_id` 和 `source_event_artifact_status`。
- 新增 `SOURCE_REPO_ROOT`，专门用于加载源码内 `tools/*.py` 模块；保留可被测试替换的 `REPO_ROOT` 用于工件路径沙箱，避免 test monkeypatch 后误从临时目录加载源码工具。
- 更新 `tools/mainline_usable_e2e_runner.py`。
- 当 CLI/runner 传入 `--asr-events-path` 时，主 session 现在创建为 `mainline_asr_event_artifact_trial`，后续 `/live/asr/sessions/{session_id}/events`、draft review、Copilot report preview、feedback/export closure 都围绕这个 artifact-backed session 执行。
- 保留辅助 `asr_event_handoff` 字段，用于继续证明 Web handoff API 本身可读 approved artifact。
- runner 对 feedback closure 的已知 422 detail 做结构化报告：合法但内容不足的事件工件不再让 runner 崩溃，而是报告 `blocked_by_candidate_report`。
- gap entries 新增 `asr_event_artifact_closure`，当 artifact-backed closure 完成时标记 `implemented_and_verified`。

TDD 验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_event_artifact_trial_closes_feedback_export_preview \
  -q -p no:cacheprovider
Result: failed because endpoint was missing, then failed because mutable REPO_ROOT was used to load source tools

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_event_artifact_trial_closes_feedback_export_preview \
  -q -p no:cacheprovider
Result: 1 passed, 2 warnings

RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py::test_runner_uses_asr_event_artifact_as_mainline_trial_source \
  -q -p no:cacheprovider
Result: failed because asr_event_artifact_closure gap was missing and runner still used blocked trial as the main source

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_mainline_usable_e2e_runner.py::test_runner_uses_asr_event_artifact_as_mainline_trial_source \
  -q -p no:cacheprovider
Result: 1 passed, 2 warnings

Focused regression:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_event_artifact_trial_closes_feedback_export_preview \
  tests/test_mainline_usable_e2e_runner.py \
  -q -p no:cacheprovider
Result: 15 passed, 2 warnings
```

主链路 smoke：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_mainline_closure_green_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
Result: exit 0
```

关键结论：

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
mainline_trial.trial_id=mainline_asr_event_artifact_trial
mainline_trial.trial_status=mainline_artifact_trial_session_created
live_asr.event_counts.transcript_final=4
live_asr.event_counts.suggestion_candidate_event=3
closure.source_trial_id=mainline_asr_event_artifact_trial
closure.source_event_artifact_status=local_asr_event_file_handoff_created
closure.closure_status=mainline_trial_feedback_export_preview_created
browser_smoke_status=passed
gap_summary.implemented_and_verified=8
```

边界结论：

- `api-review-001.mock.events.json` 只产生 1 个 suggestion candidate，因此 artifact-backed closure 会报告 `blocked_by_candidate_report`，这是正确的内容质量边界，不是 runner 崩溃。
- `m15_runner_artifact_mainline.events.json` 产生 3 个 suggestion candidate，可以完成 feedback/export preview closure。
- 所有路径仍限制在 approved `artifacts/tmp/asr_events/*.json` 和 `artifacts/tmp/asr_reports/*.json`。
- 不读取音频、不启动麦克风、不调用远程 ASR、不调用 LLM、不使用付费 provider、不读取 secrets。

下一步：

主线不应再把“artifact 接入 closure”当作未完成事项。剩余优先级现在是：

```text
1. ASR quality exit：修复/优化 FunASR synthetic smoke 或形成 Pivot/Stop 决策。
2. 真实采集验证：用户明确授权后跑 Mac system audio / mic health capture。
3. LLM 执行门：在安全配置和费用边界确定后，把 request draft 升级为可选真实 LLM suggestion card。
4. 产品体验：DEC-215 已补齐 `工件主线` 和 artifact-backed closure 的 UI 入口；后续只做展示密度和 blocker 可读性 refinement。
```

## DEC-215：PC Workbench 暴露 artifact-backed mainline trial / closure

日期：2026-07-04

状态：Accepted

背景：

DEC-214 已把 approved ASR event artifact 推进到 Web API 和 runner 主链路，但独立只读审查指出：PC workbench 仍只暴露 `mainline_asr_blocked_trial`，`闭环预览` 的前端 guard 也只允许 blocked trial。这意味着核心产品链路虽然在 API/CLI 可跑，但用户可见工作台仍不能一键跑 artifact-backed mainline。这个缺口会让“PC 端主流程跑通”停在工程自测层，不符合用户要求的主线产品可用化。

实现：

- 更新 `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`：
  - 新增按钮 `mainline-asr-event-artifact-trial-button`，显示为 `工件主线`。
- 更新 `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`：
  - 新增 `mainlineAsrEventArtifactTrialId=mainline_asr_event_artifact_trial`。
  - 新增 approved sample path `artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json`。
  - 新增 `loadMainlineAsrEventArtifactTrial()`，调用 `/desktop/mainline-asr-event-artifact-trial/sessions` 创建 artifact-backed mainline session。
  - 新增 `isMainlineTrialSession()`，允许 `mainline_asr_blocked_trial` 和 `mainline_asr_event_artifact_trial` 都进入 feedback/export closure。
  - 主线摘要面板显示 `source_event_artifact_status` 和 `events_path`。
  - 主线 closure 面板显示 `source_event_artifact_status`。
- 更新 `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`：
  - 给 `工件主线` 按钮提供相邻但可区分的主线操作样式。
- 更新 `code/web_mvp/e2e/browser_smoke.mjs`：
  - smoke 开始前在 approved `artifacts/tmp/asr_events` 写入中文技术会议 event artifact。
  - 浏览器中点击 `工件主线`，验证 DEC-214、artifact path、state/candidate/draft 链路。
  - 再点击 `闭环预览`，验证 artifact-backed closure 和 Markdown preview。
- 更新 `code/web_mvp/backend/tests/test_app.py`：
  - 静态资产测试要求 artifact button、endpoint、trial id、source status 和 `isMainlineTrialSession` 存在。

TDD 验证：

```text
RED:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: failed because mainline-asr-event-artifact-trial-button was missing

GREEN:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
Result: 1 passed, 2 warnings

Browser smoke:
node code/web_mvp/e2e/browser_smoke.mjs
Result: exit 0
Checked:
  - mainline ASR event artifact trial
  - mainline ASR event artifact feedback export closure
```

边界：

- UI 只读取 approved event artifact JSON。
- 不读取音频。
- 不请求麦克风权限。
- 不调用远程 ASR。
- 不调用 LLM。
- 不读取 `configs/local`。
- 不把 artifact preview 标成真实会议 Go evidence。

结论：

PC workbench 现在不再只支持 synthetic blocked trial；它已经具备用户可见的 artifact-backed mainline trial + feedback/export closure 入口。剩余产品主线不再是“把 artifact 接到 UI”，而是：

```text
1. ASR quality exit：继续修复或明确 Pivot/Stop。
2. 真实采集验证：用户明确授权后跑 Mac system audio / mic health capture。
3. LLM 执行门：从 request draft 升级为可选真实 provider 调用，默认仍 disabled。
```

## DEC-216：ASR quality follow-up 收敛为可诊断 blocked，而不是继续 normalizer 硬补

日期：2026-07-04

状态：Accepted

背景：

DEC-214/DEC-215 已把 approved ASR event artifact 接入 PC 主线 trial、UI 和 feedback/export closure。用户随后要求继续按最优建议推进，不要陷入无限评测。当前剩余主阻塞回到 ASR quality exit：`chunk20_hotword` RTF 已过线，但工程术语 normalized recall 仍未达到 DRV-044 阈值 `>=0.8`。本轮目标是收敛：能基于当前 transcript 证据修复的 deterministic near-miss 就修；看不见的实体不得从 golden script 反填。

实现：

- `tools/funasr_synthetic_smoke_single_result_builder.py`
  - `technical_entity_metrics` 新增 `expected_entities`、`raw_matched_entities`、`raw_missing_entities`、`normalized_matched_entities`、`normalized_missing_entities`。
  - 这让 DRV-046/DRV-032 阻塞能指出具体缺失实体，而不是只给 recall 数值。
- `tools/funasr_synthetic_smoke_result_evidence.py`
  - 新增可选 entity detail 一致性校验：若 smoke report 提供 matched/missing arrays，DRV-044 gate 会校验 recall 数值、matched/missing 覆盖关系和交集。
  - 这避免未来出现 `normalized_recall=1.0` 但 `normalized_missing_entities` 非空或漏填的矛盾诊断。
- `code/asr_runtime/scripts/transcript_normalizer.py`
  - 新增有上下文保护的 `字段 quest -> request_id`。
  - 新增 backlog/lag/告警上下文保护的 `auder -> order-worker`。
  - 修复 `redis clusterQPS` 为 `redis cluster QPS`。
- `data/asr_eval/glossaries/technical-terms.zh.json`
  - 新增 `paymentway`、`ure store`、`redi coasterbqp`、`redi coasterbqpqps`、`trcoutservice service` 等当前 FunASR transcript 中可观察 near-miss alias。
- 更新并重建 ignored ASR artifacts：
  - `artifacts/tmp/asr_reports/*funasr.batch-chunk20_hotword.transcript-report.json`
  - `artifacts/tmp/asr_reports/*funasr.batch-chunk20_hotword.smoke-report.json`
  - `artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json`
  - `artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json`

TDD 验证：

```text
RED single-result diagnostics:
PYTHONDONTWRITEBYTECODE=1 pytest -q tests/test_funasr_synthetic_smoke_single_result_builder.py
Result: 1 failed, 2 passed
Failure: KeyError: 'expected_entities'

GREEN single-result diagnostics:
PYTHONDONTWRITEBYTECODE=1 pytest -q tests/test_funasr_synthetic_smoke_single_result_builder.py
Result: 3 passed, 1 warning

RED normalizer near-miss:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code/asr_runtime pytest -q code/asr_runtime/tests/test_transcript_normalizer.py
Result: 1 failed, 7 passed

RED normalizer spacing:
Result: 1 failed, 8 passed

GREEN normalizer:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=code/asr_runtime pytest -q code/asr_runtime/tests/test_transcript_normalizer.py
Result: 9 passed, 1 warning

RED entity detail consistency:
PYTHONDONTWRITEBYTECODE=1 pytest -q \
  tests/test_funasr_synthetic_smoke_result_evidence.py::test_smoke_result_blocks_inconsistent_entity_detail_metrics
Result: 1 failed, 1 warning

GREEN entity detail consistency:
PYTHONDONTWRITEBYTECODE=1 pytest -q \
  tests/test_funasr_synthetic_smoke_result_evidence.py::test_smoke_result_blocks_inconsistent_entity_detail_metrics
Result: 1 passed, 1 warning

Regression:
PYTHONDONTWRITEBYTECODE=1 pytest -q \
  tests/test_funasr_synthetic_smoke_result_evidence.py \
  tests/test_funasr_synthetic_smoke_single_result_builder.py
Result: 14 passed, 1 warning
```

最终 ASR quality 证据：

```text
api-review-001: normalized_recall=1.0, missing=[]
architecture-review-001: normalized_recall=1.0, missing=[]
incident-review-001: normalized_recall=0.5, missing=[timeout, 监控阈值]
release-review-001: normalized_recall=0.75, missing=[staging]
non-engineering-control-001: candidate_cards=0

DRV-046 assembly_status=drv044_batch_evidence_blocked
DRV-046 counts_as_asr_quality_go_evidence=false
DRV-044 engineering_min_normalized_recall=0.5
DRV-032 decision_status=blocked_by_funasr_smoke_assembly_input_guard
DRV-032 quality_exit_status=not_exited
```

结论：

- 本轮把 `api-review-001` 和 `architecture-review-001` 修到阈值以上。
- `incident-review-001` 剩余 `timeout`、`监控阈值`，`release-review-001` 剩余 `staging`，当前 transcript 中没有可观察证据。
- 不允许为了过 gate 把这些实体硬编码进 normalizer；那会把评测脚本答案反填到 ASR 输出，不能算质量证据。
- 当前 ASR quality 仍未退出，真实麦克风会议仍 blocked。

主线回归：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
Result: passed
  asr-runtime-pytest: 81 passed
  asr-bakeoff-pytest: 18 passed
  root-pytest: 507 passed
  core-pytest: 34 passed
  web-backend-pytest: 336 passed

PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_followup_mainline_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
Result: exit 0
browser_smoke.browser_smoke_status=passed
overall_status=mainline_product_chain_exercised_with_expected_blockers
gap_summary.implemented_and_verified=8
gap_summary.blocked_by_asr_quality=1
```

边界：

- 本轮不读取私人录音、`.m4a`、Voice Memos、`configs/local/`、`data/local_runtime/` 或 `outputs/`。
- 不访问麦克风。
- 不调用远程 ASR/LLM。
- 不写真实导出文件。
- 不新增付费项。
- 敏感扫描未发现真实 API key 或 relay domain 被写入仓库。

下一步：

不再继续堆 deterministic normalizer。下一轮只做受控 ASR 输入/参数实验：固定 `chunk20_hotword` 为速度基线，围绕 `timeout`、`监控阈值`、`staging` 三个缺失实体检查 synthetic script/audio 是否清楚承载这些词，并尝试热词/参数/模型路径让实体真实出现在 transcript 中。若仍不能达到阈值，则进入产品决策：远程 ASR 成本/隐私审批，或显式 degraded pilot，只验证 timing/feedback，不宣称 ASR quality Go evidence。

## DEC-217：Release readiness reset 收敛当前状态和后续主线边界

日期：2026-07-05

状态：Accepted

背景：

用户指出项目执行已经超过 90 轮，感觉陷入 ASR/readiness/边界试探循环，并质疑是否忘记最初“中文技术会议实时 AI Copilot”的产品初心。为避免继续用过程产物掩盖真实发布缺口，本轮启动多 Agent 只读审查，并结合本地主线 runner、ASR quality gate、real mic readiness gate 重新复盘产品设计差距、代码真实状态、ASR 阻塞和发布路线。

决策：

当前状态正式定为：

```text
Local Shadow Preview / Engineering Demo ready: yes
Shadow Pilot ready: no
Production MVP ready: no
```

项目已有可演示的 synthetic/mock/replay/artifact-backed Copilot 产品链路，但没有达到原始 MVP。原始 MVP 仍是：

```text
real/authorized meeting audio
  -> qualified Chinese technical ASR
  -> EvidenceSpan
  -> realtime meeting state
  -> engineering gap card
  -> feedback/export
```

后续主线不得再把 readiness/preflight/approval/preview/wrapper-only 工作算作发布推进，除非它直接改变至少一个 release-decisive state：

```text
quality_exit_status
real_mic_shadow_readiness_status
user_can_start_real_mic_shadow_test_now
normalized technical entity recall
formal card/report evidence status
real meeting feedback useful/wrong/too_late/too_intrusive
```

如果连续两个任务都只新增 readiness/preflight/approval/preview/wrapper 且没有减少 blocker，第三个任务必须停止边界工作，并在 `ASR quality exit`、`explicit degraded pilot`、`product pivot` 中选择。

原因：

- PRD、feature map 和 minimum demo script 均明确产品不是转写工具，而是实时工程缺口 Copilot。
- 当前代码已经证明了 EvidenceSpan、state、suggestion candidate、LLM request draft、feedback/export preview 等产品链路，但输入仍是 synthetic/mock/replay/artifact。
- ASR quality gate 仍为 `quality_exit_status=not_exited`，真实麦克风 readiness 仍为 `blocked_not_ready_for_user_real_mic_shadow_test`。
- Tauri desktop bridge 和 ASR worker 当前仍是 no-op/no-execution 形态。
- LLM execution 当前仍 disabled/not_called，formal cards 当前 not_created。

替代方案：

- 继续补 readiness/report-only wrapper：拒绝，不能改变真实发布状态。
- 直接宣称 Beta 或真实会议可用：拒绝，缺少 ASR quality、real mic、formal card/report Go evidence。
- 立刻进入真实麦克风：拒绝，除非后续明确选择 degraded pilot 且记录不算 ASR quality Go evidence。
- 放弃产品：暂不采用；当前 Local Shadow Preview 仍证明了“不是只做转写”的架构价值。

影响范围：

- 新增 `docs/project-release-readiness-reset-2026-07-05.md` 作为复盘与路线短入口。
- 新增 `docs/superpowers/plans/2026-07-05-local-shadow-preview-release-path.md` 作为下一阶段执行计划。
- 更新 `docs/current-mainline-index.md`。
- 更新 `docs/requirements-traceability-matrix.md`。

成本/隐私影响：

- 不新增任何默认收费项。
- 默认仍不调用远程 ASR/LLM。
- 不读取真实用户音频、`.m4a`、`configs/local/`、`data/local_runtime/` 或 `outputs/`。
- 不访问麦克风。

验证方式：

本轮 fresh verification：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id release_reset_fresh_20260705 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr

Result:
overall_status=mainline_product_chain_exercised_with_expected_blockers
live_asr.transcript_final_count=4
live_asr.suggestion_candidate_count=3
live_asr.llm_request_draft_count=3
closure.closure_status=mainline_trial_feedback_export_preview_created
gap_summary.implemented_and_verified=8
gap_summary.blocked_by_asr_quality=1
```

```text
python3 tools/asr_quality_decision_gate.py \
  --funasr-smoke-assembly-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.batch-assembly-chunk20_hotword.json

Result:
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
counts_as_asr_quality_go_evidence=false
```

```text
python3 tools/real_mic_shadow_test_readiness_gate.py

Result:
readiness_status=blocked_not_ready_for_user_real_mic_shadow_test
asr_quality_exit_status=not_exited
```

关联文档：

- `docs/project-release-readiness-reset-2026-07-05.md`
- `docs/superpowers/plans/2026-07-05-local-shadow-preview-release-path.md`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`
- `docs/asr-quality-exit-followup-2026-07-04.md`

复审条件：

- ASR gate strict pass。
- 用户显式接受 degraded pilot。
- 真实麦克风 shadow test 完成并产出 feedback metrics。
- 项目决定 pivot 到远程 ASR 或会后证据化工程纪要。

## DEC-218：Local Shadow Preview release path 可见化并替代 readiness-wrapper 漂移

- 日期：2026-07-05
- 状态：Accepted

背景：

DEC-217 已把项目真实状态收敛为 `Local Shadow Preview / Engineering Demo ready`，而不是 `Shadow Pilot ready` 或 `Production MVP ready`。此前用户担心主线陷入 readiness/preflight/边界循环，且界面和 runner 没有把“当前可交付演示范围”和“仍然阻塞真实会议发布的状态”放在一个可见位置，容易继续误把 wrapper 工作当成发布进展。

决策：

新增唯一的 Local Shadow Preview release readiness path，并让它同时出现在：

- FastAPI：`GET /desktop/local-shadow-preview-release-readiness`
- Web 工作台：`local-shadow-preview-release-panel`
- Browser smoke：`local shadow preview release readiness`
- Mainline runner 顶层：`local_shadow_preview_release_readiness`

该路径必须固定展示：

```text
release_tier=local_shadow_preview
demo_preview_ready=true
shadow_pilot_ready=false
production_mvp_ready=false
asr_quality_exit_status=not_exited
real_mic_readiness_status=blocked_not_ready_for_user_real_mic_shadow_test
llm_execution_status=disabled_not_called
formal_card_status=not_created_in_current_mainline_preview
formal_report_status=preview_only_not_real_meeting_go_evidence
allowed_claim=local synthetic/replay/artifact Copilot preview
```

原因：

- 该实现把当前产品价值放回“实时 Copilot 主链路”而不是继续展示散落的 readiness 面板。
- 它能让用户打开工作台时立即看到：本地 preview 可演示，但 ASR quality、真实麦克风、LLM execution 和正式卡片/报告都没有达到真实会议发布标准。
- 它不新增 provider、不引入额外收费项、不触碰真实音频，也不把当前状态包装成 Beta 或 Production。

替代方案：

- 继续新增更细 readiness wrapper：拒绝，无法改变 release-decisive state，且会加重用户感知的循环。
- 直接进入真实麦克风 shadow test：拒绝，ASR quality exit 仍为 `not_exited`，real mic readiness 仍 blocked。
- 只在文档说明状态，不改 UI/runner：拒绝，不能形成可演示、可测试、可追踪的产品状态。

影响范围：

- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/index.html`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `tools/mainline_usable_e2e_runner.py`
- `tests/test_mainline_usable_e2e_runner.py`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`

成本/隐私影响：

- 不新增默认收费项。
- 不访问麦克风或系统音频。
- 不读取用户真实录音、`.m4a`、`configs/local/`、`data/local_runtime/` 或 `outputs/`。
- 不调用远程 ASR、LLM 或中转站。
- 只读取 approved local artifacts 和本地静态/测试资源。

验证方式：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_local_shadow_preview_release_readiness_reports_truthful_status \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_include_local_shadow_preview_release_summary \
  tests/test_mainline_usable_e2e_runner.py::test_runner_reports_local_shadow_preview_release_readiness \
  -q -p no:cacheprovider
```

```text
node code/web_mvp/e2e/browser_smoke.mjs
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id local_shadow_preview_release_readiness_20260705 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
```

关联文档：

- `docs/project-release-readiness-reset-2026-07-05.md`
- `docs/superpowers/plans/2026-07-05-local-shadow-preview-release-path.md`
- `docs/current-mainline-index.md`
- `docs/requirements-traceability-matrix.md`

复审条件：

- ASR quality exit 从 `not_exited` 变成 strict pass，或用户显式接受 degraded pilot。
- 真实麦克风 shadow test readiness gate 变为可启动。
- LLM execution 从 disabled/not_called 进入可审计的受控执行。
- Formal cards/report 从 preview/not-Go 变成真实会议 feedback-backed Go evidence。
