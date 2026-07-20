# Meeting Copilot 运行约束与交付边界

时间：2026-07-18

状态：Accepted operating constraints / Applies to active implementation and self-test

## 1. 目标

Meeting Copilot 的首个交付形态是可安装、可解压运行的 macOS 桌面客户端。核心会议数据和本地能力必须在用户电脑上闭环；除用户主动配置的 OpenAI-compatible LLM 请求外，不接入云同步、不上传原始音频、不接付费远程 ASR。

本文件约束后续开发、自测、打包、权限处理和 Web/客户端验收方式。它是运行边界，不替代产品 SDD、API 契约和 TDD。

## 当前执行目标与边界

当前目标是完成持续追加文档包所列的全部产品事项及自测：本文件约束运行、权限、凭据、数据、Web/客户端和发布边界；产品缺口与路线由 `post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md` 负责；逐项状态和验收出口由 `full-roadmap-execution-checklist-2026-07-18.md` 负责。

文档文字收口不代表这些约束已经全部由代码实现，也不代表 packaged client、Windows 或公开发布已经验收通过。当前每个实施批次必须引用本文件，在对应 NEXT 条目中补充代码、测试、运行证据和剩余边界，并持续推进到全量关闭或形成严格外部阻断。除本文件明确允许的本地模拟外，不得把模拟结果写成真实平台能力。

## 2. 本地优先数据边界

### 2.1 默认本地处理

- 麦克风采集、系统音频 adapter、录音分片、WAV 组装、FunASR 实时/文件转写、SQLite、历史、会后编辑和导出默认在本机完成。系统音频代码已接线，但真实 packaged TCC/PCM 主链仍必须单独验收。
- 原始音频默认不上传任何远程服务。
- 不建设账号、租户、云同步、多设备同步、远程录音备份或默认遥测。
- 不接入付费远程 ASR；远程 ASR 不属于当前产品主线。
- 只有用户配置的 LLM 中转站/Provider 可以接收为文字修正、实时理解、建议和会后整理所需的文字上下文；LLM 请求不应携带原始音频。
- LLM Provider、模型、Token、错误和估算成本必须在本地 session ledger 中记录，API Key 不写入日志、evidence、导出文件或 Git。

### 2.2 本地运行目录

生产客户端使用稳定的应用数据目录，例如：

```text
~/Library/Application Support/com.meetingcopilot.desktop/
  runtime-data/       SQLite、会议文字、录音和导出临时数据
  logs/               脱敏运行日志
  models/             可选本地模型包
  provider-config.json 仅保存非敏感 Provider metadata
```

API Key 生产环境继续使用 macOS Keychain；测试自动化可以使用仓库外、权限为 `0600` 的专用测试凭据注入，但不得提交到仓库、普通配置、截图或文档。

本地会议数据使用 owner-only 存储合同：POSIX/macOS 应用数据目录、backend/native capture 子目录为 `0700`，SQLite 及 WAL/SHM/journal、会议文字、录音、导入文件、ready 状态和脱敏日志为 `0600`；历史数据通过版本化启动迁移收紧。Windows 必须依赖并验收 per-user app-data ACL，POSIX mode 证据不能替代 Windows ACL 真机门禁。任何无法收紧的敏感运行文件必须 fail closed，不得继续报告采集或保存成功。

## 3. macOS 权限和自动化边界

### 3.1 能够做到的事情

- 固定最终应用安装路径，例如 `/Applications/Meeting Copilot.app`。
- 使用稳定 Bundle Identifier、稳定 Developer ID/Team ID 和稳定签名身份。
- 只在首次明确操作时请求麦克风权限；后续启动和重新打包更新尽量复用同一个应用身份。
- 在本机自动启动/停止 bundled backend、FunASR worker 和 native microphone helper。
- 通过本地 loopback token 与 packaged backend 通信，不要求用户为每个随机端口手动操作。
- 在不访问真实麦克风的情况下，使用公开授权音频、测试 WAV、模拟音频和 fake microphone 完成大部分自动化回归。
- 使用已经授权的固定客户端身份执行 packaged UI、录音、转写、编辑、导出和恢复测试。

### 3.2 不能绕过的事情

- 文件夹读写授权不能代替麦克风 TCC 权限，也不能代替 Keychain 权限。
- 不能读取、提取、记录、回显或自动填写用户的 Mac 登录密码。
- 不能使用 `sudo`、脚本化 SecurityAgent、关闭 TCC、放宽 Keychain ACL 或模拟用户点击来绕过系统授权。
- 不能保证 ad-hoc 构建在改变路径、Bundle ID、签名身份或应用主体后永远不重新弹授权。
- 系统重置权限、用户手动移动/重命名应用、换签名身份、卸载重装或重大系统策略变化后，可能再次需要用户本人确认。
- 不申请 Full Disk Access 作为普通运行前提；只申请实现功能所必需的最小权限。未来 system audio 需要 Screen Recording 权限时，必须独立告知并单独验收。

### 3.3 减少重复授权的发布策略

1. 开发阶段固定一个最终验收安装路径和一个应用身份，不在多个临时目录反复启动不同副本。
2. 公开发布使用稳定 Developer ID Application、Team ID、Hardened Runtime、Notarization 和 stapled package。
3. 更新采用同 Bundle ID、同签名团队和同应用路径，先做升级安装验证，再进行功能回归。
4. Provider metadata 可以在启动时读取；读取 Keychain secret 和真实 Provider probe 仍是用户明确连接动作。连接成功后的 session 内不重复询问。
5. 自动化自测优先使用测试凭据注入和模拟音频；只有专门的 packaged real-mic gate 才访问真实麦克风，并且该 gate 不在每次普通代码修改后执行。

## 4. Web 与桌面客户端边界

### 4.1 共享部分

Web 和客户端必须共享：

- 同一套 React/TypeScript 页面组件。
- 同一套 API schema、事件、状态 reducer 和错误分类。
- 同一套会议文字、LLM 增量分析、会后文档、编辑、历史和导出业务契约。
- 同一套本地 backend API 语义；任何平台差异必须进入 adapter/capability，不得复制一套业务实现。

### 4.2 平台差异

| 能力 | 本地桌面客户端 | 本地 Web 开发/备用入口 |
|---|---|---|
| 麦克风 | native AVAudioEngine adapter | browser `getUserMedia` |
| 系统音频 | ScreenCaptureKit adapter 已实现，真实 TCC 主链待验收 | 浏览器能力受限，不承诺完整采集 |
| backend | app 自带 bundled backend，随机 loopback 端口 | 依赖外部启动的本地 backend |
| FunASR | app 自带实时模型和可选文件模型包 | 依赖 backend 的本地模型状态 |
| Provider 凭据 | Tauri IPC + macOS Keychain | 不直接管理 Keychain secret |
| 本地数据 | app data directory | 取决于本地 backend/data directory |
| 发布真相 | 必须通过 packaged client gate | 不能替代 packaged client gate |

结论：不是维护两套 UI。Web 先用于快速开发、组件测试和本地交互验证；每个涉及采集、打包、权限、数据路径或导出的功能，最终必须在 packaged client 上验收。Web 通过不能直接宣称客户端通过。

### 4.3 Web 开发顺序

推荐顺序：

1. 在本地 Web 开发服务器实现共享 React UI、状态契约和组件测试。
2. 使用本地 backend、模拟音频和测试 Provider 完成接口/E2E 自测。
3. 构建 packaged client，验证 bundled backend、native adapter、本地数据目录和权限状态。
4. 对真实麦克风、Keychain 和安装升级只执行有明确证据目标的 packaged gate，不让它们成为每一轮开发的人工阻塞。

禁止：先把 Web 做成一套能跑的产品，再为客户端复制第二套页面或绕过共享 API。

## 5. 自测和无人值守运行策略

### 5.1 默认无需用户配合的测试

- React 单元、组件、状态 reducer、API schema、错误映射和编辑器测试。
- backend API、SQLite migration、durable job、AI fake gateway、导出和失败恢复测试。
- 模拟音频/公开授权测试音频到本地 ASR、实时文字、LLM fake/provider、会后复盘、编辑和导出的完整测试。
- packaged client 启动、backend/worker supervisor、随机端口、应用退出、数据目录、历史恢复和无密钥本地模式测试。
- DOCX/Markdown/JSON 导出和重新加载测试。
- 所有无人值守 packaged runner 必须使用 `-ApplePersistenceIgnoreState YES -NSQuitAlwaysKeepsWindows NO`，避免 AppKit crash-history state restoration modal 在 Tauri setup 前等待人工点击；这只影响自动化启动，不得冒充真实崩溃恢复体验已验收。
- packaged gate 开始前必须检测并报告同 Bundle ID 的残留 App/backend/worker；不得把系统转发给旧实例的 reopen 事件误判为当前候选启动成功，也不得静默终止与本次候选无关的用户进程。

### 5.2 需要系统一次性授权的测试

- 固定签名/固定路径下的真实麦克风权限和 native capture gate。
- 固定应用身份下的 Keychain 读取、Provider 同步和真实 relay probe gate。
- system audio 的 Screen Recording 权限 gate；代码实现和无权限协议测试不能替代真实 TCC 允许/拒绝验收。

这些测试完成后，后续普通代码修改不应自动触发新的权限请求；如果身份或路径改变导致系统再次请求，测试报告必须明确说明是 macOS 身份变化，而不是把它包装成产品功能失败。

### 5.3 失败处理

- backend 未启动：显示“本地会议服务未启动”，提供重新启动/诊断入口。
- FunASR 实时模型缺失：显示“实时转写组件未安装”。
- FunASR 文件模型缺失：显示“文件导入组件未安装”，不能显示“网络离线”。
- Provider 未连接：本地录音和转写可以继续，AI 任务进入可恢复等待。
- Provider 超时或失败：保留原始文字、录音和用户编辑稿，单独标记 AI 任务失败。
- 任何失败都必须有阶段、错误类别、是否自动重试和下一步动作。

### 5.4 真实 Provider 运行约束

- OpenAI-compatible 只保证基础线协议，不能假设每个中转站都稳定支持 structured output、streaming、usage 或同一延迟特征；能力差异必须通过 adapter/capability 和明确降级状态表达。
- 实时 intelligence 必须先经过严格 JSON、paragraph revision 和逐字 evidence 校验再提交；结构校验失败最多允许一次受控修复，修复后仍失败必须保留原文并显示 AI 失败，不能回退到关键词语义结果。
- LLM 文字修正必须通过事实保真门禁后才能更新 canonical paragraph；负责人、日期、比例数值、决策极性或技术实体变化属于 `semantic_safety` 失败，不能通过二次 repair 或相似度较高而放行。中文分数与数值等价格式可以规范化，但必须先做精确比例换算。
- 每次真实调用和受控修复都必须进入本地 Token/cost ledger；API key、Authorization、完整 Provider URL、prompt 原文和模型原始响应不得进入普通日志、截图、诊断包或可提交 evidence。
- 真实 Provider API 主链通过不等于 UI 或实时性能通过。当前 r6 观测 TTFT 约 `19.6s`、provider total 约 `23.5s`，必须继续标记为 SLO 未达标。

## 6. 打包和发布门禁

每次可交付包必须验证：

- 不依赖仓库路径、开发虚拟环境、用户 ModelScope 缓存或本机全局 ffmpeg。
- runtime manifest 能解析 bundled backend、FunASR worker、实时模型和文件模型包。
- 无开发环境的干净 Mac 可以启动、开始会议、录音、实时转写、结束复盘、编辑和导出。
- 安装包启动失败不会留下孤儿 backend/ASR/native mic 进程。
- 升级同一安装身份后，数据目录、历史、Provider metadata、Keychain identity 和权限行为符合预期。
- packaged client 的 UI 截图、网络请求、错误日志和导出文件都不包含 API Key、Authorization、Mac 密码或完整敏感测试凭据。

## 7. 关联决策

- `DEC-420`：Provider 未同步是可恢复等待。
- `DEC-421`：保存 metadata 不得读取既有 Keychain secret。
- `DEC-422`：Provider 401 必须区分密钥来源与接口协议。
- `DEC-425`：会后 AI 结果进入用户最终稿、可重试、可命名和可导出。
- `DEC-426`：本地优先交付、稳定应用身份和 Web/客户端共享契约。
- `DEC-428`、`DEC-429`、`DEC-430`：历史上曾把目标错误收窄为只整理文档包；均已由 DEC-431 覆盖。
- `DEC-431`：完成持续追加文档的含义是完成其中全部 NEXT 事项及自测、证据和真实阻断审计，文档整理不能替代产品交付。
- `DEC-455`：系统 TCC/Keychain 不得绕过；backend 和 desktop native runtime 的本地数据统一 owner-only，并迁移历史文件权限。

## 8. 长会议恢复与音频资产约束

1. backend/ASR/App 故障后的恢复不能以 health endpoint 单独判定。自动化至少同时检查新进程身份、post-fault ASR ready、post-fault audio growth、持久化序号不回退、录音 chunk/duration 不回退以及最终 WAV assembled。
2. 已存在的 `audio.manifest.json` 是该录音的本地时间线事实源。SQLite 中的毫秒时间可以用于索引和展示，但后台 export/recovery 不得用近似值重写已有 manifest，否则会破坏 chunk replay 幂等性。
3. writer setup、journal replay、WAV export、混音和删除对同一 meeting 的音频目录必须使用同一资产锁；数据库 lease 只解决 capture ownership，不替代文件系统临界区。
4. writer setup 在取得 lease 后失败，必须按 exact `lease_owner + capture_generation` CAS 释放本次 lease，并 abort 已创建的 ASR session。回滚失败必须产生脱敏错误类和函数来源；不得记录异常自由文本、绝对路径、文字稿、prompt、Provider URL 或 secret。
5. stale recovery/rollback 不得中断更新 generation。普通 retry 可以等待正在运行的 export，但不得形成无界 300ms 连接风暴或把 resident ASR 永久留在 busy。
6. 长会正式门禁仍要求完整 short fault matrix 后再执行 1h 和 3h；60 秒单故障 GREEN 只能推进对应子门禁，不能关闭 `NEXT-006` 或全量目标。
7. Python backend 的正式开发/测试环境以 `code/web_mvp/backend/pyproject.toml` 与 `uv.lock` 为准，当前要求 Python `>=3.11,<3.14`。系统 Python 版本或缺包导致的结果必须明确标为环境无效，不能伪装产品回归，也不能通过向全局 Python随意安装依赖解决。

## 9. 持久目标不可缩减约束

1. 当前持久目标不是“完成一次自测”“修复当前红灯”“重建一个候选包”或“把文档写完整”，而是完整实现和验证三份权威文档中的 `NEXT-001` 至 `NEXT-023`。
2. 每一轮 focused/full test、shared UI、packaged API、真实 Provider、真实麦克风、system audio、Windows 或公开发布结果只是对应条目的证据，不得覆盖或替换总目标。
3. fake/synthetic/shared-Web/API-only 证据必须保留 `non_acceptance` 范围；不能用它关闭真实 TCC、自然多人中文、packaged UI、Windows 真机、签名公证或 clean-machine 门禁。
4. 外部条件只能在形成可复现 blocker 包后记为严格阻断。blocker 包至少包含缺失输入、已有替代证据、解锁条件、验收命令和预期产物；仅在 Markdown 中写“需要用户/证书/真机”不足以关闭条目。
5. 任何新建的小目标、测试计划或修复批次都只能作为本目标的子步骤，不能把持久目标缩小。目标只有在全部条目获得实现与匹配范围的证据，或得到严格外部阻断结论后才能标记完成。

## 10. 工作树与共享 UI 入口约束（DEC-445）

1. 当前唯一实现主线是 `/Users/chase/Documents/面试/meeting-copilot-phase0-clean`。`/Users/chase/Documents/面试/meeting-copilot` 是旧工作树，不得作为当前候选、发布包或主线功能验收的来源。
2. `http://127.0.0.1:8767/workbench` 当前可能由旧工作树启动；看到该地址不等于看到 clean 主线。验收报告必须记录服务进程的工作目录、端口、commit/工作树和数据目录。
3. 旧工作树可以提供视觉参考和品牌资产，但不得直接覆盖 clean 前端目录。必须将视觉层增量移植到完整主线，保留本地 API 安全、Provider、会前检查、导入、原生音频、speaker、编辑、重试和导出能力。
4. Web 和 macOS 客户端使用同一套 React/TypeScript UI。允许的差异只在平台 adapter；不得复制两套业务页面，也不得把旧 Web 页面包装成客户端最终实现。
5. 视觉层改动完成后，应在未占用的新端口启动 clean 服务，与旧 `8767` 并排复核；旧服务不因本轮对照而被停止，除非用户明确要求。

## 11. 多 Agent 中断与证据连续性约束（DEC-446）

1. Agent 必须接收唯一主线绝对路径、明确文件所有权、测试命令、证据边界和“不得宣称全量完成”约束；并行写入范围应互斥。
2. Agent 的最后一条回复不是交付真相。代码、测试和 evidence 必须先写入共享工作树；主线程按 diff、当前文件、测试和运行证据独立复核后才能接纳。
3. Agent 出现 `502`、`not_found`、中断或缺失总结时，不得假定成果丢失，也不得凭记忆重做并覆盖已有改动。先检查其文件所有权范围和落盘时间，再复跑对应 RED/GREEN；只有确有缺口才恢复或新建自包含 Agent。
4. 任何 Agent 的测试数字都必须由主线程或独立验证 Agent至少复核 focused gate；跨模块改动还必须进入最终 backend/frontend/root/Rust/package 回归。
5. clean workbench 启动器必须验证本地服务的版本化诊断合同、共享 UI 资产和受管理进程绑定。仅 `/health=ok` 不足以认定当前主线；外来或旧进程必须拒绝复用且不得自动 kill。

## 12. Workbench runtime identity 约束（DEC-450）

1. clean workbench 的可用性判断必须同时验证 health service contract、application schema diagnostic contract、共享 `frontend_v2/dist/index.html` asset hash、受管 PID、process start marker、目标端口和当前源码 fingerprint；`/health=ok` 单独通过不构成运行时身份证明。
2. runtime identity 文件权限固定为 `0600`，内容只允许是脱敏的进程、端口、源码/资产指纹和受管启动事实；禁止写入 API Key、Authorization、Keychain password、用户文字稿、录音内容或完整环境变量。
3. 目标端口被旧/外来进程占用且 identity 不匹配时必须 fail closed，禁止自动 kill、复用或把外来页面当作 clean 主线。只有启动器明确拥有的 stale child 才能按记录清理。
4. `stop` 必须先验证受管 identity；foreign PID、缺失 identity 或不匹配记录只能报告阻断，不得发送终止信号。
5. runtime identity gate 只证明当前本地服务身份与源码/资产绑定，不替代真实 ASR、Provider、麦克风、Screen Recording/TCC、packaged UI、自然多人、签名公证、Windows 或公开发布门禁。
6. 每次运行证据必须记录工作树、端口、受管数据目录、identity 状态和测试范围。`8767` 若来自旧工作树，只能作为对照，不能作为 clean 主线证据。

## 13. macOS app principal 与内部签名包约束（DEC-451）

1. `.app` 是主签名 principal；`Contents/MacOS/...` 主可执行文件必须进入 inventory 和最终一致性验证，但不得作为独立 signing step，否则会被 app principal 的最终签名覆盖并产生错误的 entitlement 结论。
2. 主 app 和固定 native microphone helper 只允许最小 `com.apple.security.device.audio-input=true`；其他 nested Mach-O 禁止 entitlement。所有目标必须启用 Hardened Runtime，签名和验证禁止 `--deep`。
3. 内部 ad-hoc 包的 `go_internal_controlled_smoke_not_public_release` 只证明包资源、逐目标签名和本地控制清单可验证；不证明 packaged supervisor/API/UI、真实麦克风/TCC、system audio、真实 Provider、自然多人、clean Mac、Windows 或公开发布。
4. 当前候选包可包含本地模型和 FFmpeg，但其 provenance/再分发授权未闭环时不得发布或上传；默认仍保持本地优先、无音频上传、无动态下载和无默认 telemetry。
5. 签名 evidence 只能记录状态、数量、hash 和边界，不得记录 signing identity 私密材料、Keychain password、API key、Authorization、用户内容或音频。

## 14. Native PCM v2、sidecar 收尾与真实显示门禁

1. 原生 microphone 与 system-audio transport 必须使用 native_pcm_v2 envelope，并在 backend 严格校验 track、capture epoch、单调 sequence、timestamp、sample rate、payload 长度和 Float32 有限值；native source 不得退回浏览器裸 PCM 语义。
2. 录音 writer、SQLite audio_chunks 和 transcript/event identity 必须保留 native source range；四个范围字段要么全部为空（浏览器兼容），要么全部存在且非负、顺序合法，重试内容不一致必须失败。
3. ready 文件不是“进程已启动”标志。只有 loopback WebSocket 已认证、完整 PCM frame 产生并成功发送后，才允许写 ready；transport_ready、pcm_seen、audible_pcm_seen、asr_ready 必须独立展示和记账。
4. sidecar 进程终止必须幂等并可 reap；正常 END 和错误终止都要先完成录音/持久化/sidecar 收尾，再向 WebSocket 发送终止帧和 close，避免客户端提前退出留下线程、子进程或半完成会话。
5. 本机真实 ScreenCaptureKit 门禁必须分别记录授权事实与显示会话事实。CGPreflightScreenCaptureAccess=true 但 active display 为 0 时只能记录 content_unavailable 阻断，不得写入“已采集”“静音采集成功”或 fake PCM evidence；恢复 active display 后必须用同一 candidate 重跑三层 packaged gate。
6. 测试必须使用项目声明的 code/web_mvp/backend/.venv Python >=3.11,<3.14 和仓库外 Rust target。系统 Python 缺依赖、旧端口、旧工作树或旧 candidate 的结果不得倒算当前主线通过。

## 15. Web Provider 配置与存储约束（DEC-457）

1. Web 与 macOS 客户端必须复用同一个 Provider 设置业务组件、字段合同和真实 probe；Web/桌面差异只允许存在于本地 API 与 Tauri/Keychain adapter，不得复制第二套 LLM 业务逻辑。
2. 本地 Web runtime 的 Provider 配置保存于当前受管 `data_dir/settings/provider.json`；目录必须为 `0700`，配置文件必须为 `0600`，写入必须原子完成。Web 不是云端账户系统，也不上传原始音频。
3. `/providers/config` 只返回 base URL、模型、协议、连接状态和 `api_key_present`；任何响应、日志、diagnostic bundle、前端状态和截图都不得包含 API Key 或 Authorization。
4. Web runtime 启动时可从受控文件恢复 Provider 到进程内配置；保存/删除必须立即影响实时建议、实时修正和会后 LLM 作业。Provider probe 仍走统一 `/providers/llm/probe`，并记录 token/成本边界。
5. 没有 Tauri 时不得显示“请在桌面客户端配置 AI”。没有受控数据目录时必须返回明确的存储不可用错误；不能显示“AI 已连接”或用 mock/fallback 伪造连接成功。
