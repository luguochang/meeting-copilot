# Meeting Copilot Phase 0-2 后续产品缺口与路线讨论稿

时间：2026-07-18

状态：Active product implementation source of truth / NEXT-001 至 NEXT-023 全量执行中

适用范围：Phase 0-2 内部主链完成后的产品规划、Mac 生产化、Windows 扩展和 Web 定位讨论。

## 1. 文档目的

本文记录当前已经发现的问题、尚未完成的能力、实施优先级、验收出口和产品决策，并作为当前全量实现目标的需求范围来源。用户已经授权按推荐方案继续实现，不再要求逐项询问；但外部资质、硬件、真实自然多人样本和系统权限仍必须按真实边界验收，不得伪造结果。

每个实施批次必须先确认需求边界、产品形态、验收标准和实施顺序，再落实为 SDD、接口/存储契约、TDD 用例、代码和运行证据。可以按依赖顺序分批关闭，但不得把阶段通过或文档整理写成产品全量完成。

当前持久目标是把持续追加到本文档包中的需求和缺陷真正完成：逐项推进 `NEXT-001` 至 `NEXT-023` 以及关联运行约束的代码、测试、共享 Web/packaged UI、模拟全链和必要专项验收。只有每个条目均达到验收标准，或对确受外部资质、硬件、自然样本、Windows 真机或 macOS 权限阻断的部分形成可复现证据、明确阻断和下一步，才允许关闭总目标。任何 `Partial`、`Blocked` 或 `Not started` 不得因文档已写完整而改写为 Go。

## 2. 当前已经完成的范围

当前 Mac internal packaged client 已完成并取得同场真实证据：

- Tauri packaged app 和 bundled backend/FunASR runtime。
- macOS 原生麦克风采集。
- 本地中文 FunASR/Paraformer 实时识别。
- 连续追加 canonical transcript。
- 远端 LLM 文字修正和流式建议。
- 本地录音分片、WAV 导出和真实播放。
- 单一“结束并整理”流程。
- 纪要、方案、决策、待办、风险、问题和索引。
- 历史恢复、完整文字、Markdown/JSON 导出和删除。
- 原始音频默认不上传、远程 ASR 默认关闭。
- app/backend/resident FunASR/随机端口自然退出。

该结论是“Phase 0-2 Mac 内部产品主链 Go”，不是“所有平台、所有输入源和公开发布全部完成”。其中“主链 Go”只证明内部工程链路曾经贯通，不代表当前实时会议的信息组织、AI 状态可信度、多人会议可读性和失败恢复已经达到生产级体验。本文后续新增的 NEXT-013 至 NEXT-016 是进入产品化前必须关闭的主链缺口。

## 3. Web 与客户端当前关系

Web 和桌面客户端共用：

- 同一套 React 页面和交互状态。
- 同一套 FastAPI 业务接口。
- 同一套 SQLite/V2 持久化和事件模型。
- 同一套 transcript、AI suggestion、correction、minutes、approach、history、review 和 export 逻辑。

两者当前不一致的能力：

| 能力 | Mac 客户端 | Web | 当前边界 |
|---|---|---|---|
| 麦克风采集 | AVAudioEngine native helper | Browser `getUserMedia` | 实现和稳定性不同 |
| Provider 配置 | Tauri IPC + macOS Keychain | 页面不可保存/修改 Key | 桌面专属 |
| 自带 backend/ASR/model | packaged runtime | 依赖外部部署的 backend | 桌面专属 |
| 本地隐私边界 | backend/ASR/录音均在本机 | 取决于 backend 部署位置 | 远端 Web 会上传音频 |
| 设备选择 | 当前使用系统默认麦克风 | 可枚举浏览器音频输入设备 | 能力不同 |
| 系统音频 | 未正式接入 | 浏览器通常无法可靠采集 | 两端均未完成 |
| AI 建议/修正 | Provider 同步后可用 | backend 预配置后可用 | Web UI 不能管理凭据 |
| 历史/复盘/导出 | 支持 | 支持 | 业务层一致 |
| 离线能力 | 本地 runtime 可支持 | 取决于 Web backend | 不一致 |

当前建议定位仍是：桌面客户端承担完整会议采集；Web 保留为开发/备用麦克风入口和未来复盘协作入口。是否建设公开云端 Web 产品，等待后续产品决策。

## 4. 已发现但尚未完成的能力

### NEXT-001 Mac 系统音频正式接入

状态：Partial / 正式 ScreenCaptureKit helper、Rust/Tauri 接线和共享 UI 已实现；真实 packaged TCC/PCM 主链未关闭

问题：当前正式输入只允许 `microphone`。扬声器外放时可由麦克风回采远端声音，但用户佩戴耳机后，Zoom、飞书、腾讯会议等远端声音不会进入麦克风，核心会议内容可能缺失。

建议：使用 ScreenCaptureKit 将单路 system audio 接入现有认证 WebSocket、ASR、recording journal、WAV、history 和 review 主链。

首个验收出口：

- 用户可在会前选择“麦克风”或“系统音频”。
- 戴耳机播放远端会议声音时，system audio 仍产生实时文字、建议和本地录音。
- 权限拒绝必须明确失败，不得静默切换成麦克风。
- 原始音频继续只进入本地 backend。
- 结束、回放、导出、删除复用现有 V2 资产 owner。

### NEXT-002 麦克风与系统音频双轨混录

状态：Partial / 双轨生命周期、持久化、来源去重、回放 API、Rust coordinator 和共享 UI 已实现；真实 packaged 双轨同场未验收

问题：完整线上会议通常需要同时采集用户麦克风和远端系统音频。简单混成单轨会产生回声、重复识别和难以定位来源的问题。

建议：NEXT-001 稳定后再开放 `mixed`。两条输入必须有独立 track/source、epoch、sequence 和 timestamp，录音资产仍由 V2 单一 owner 管理。

首个验收出口：

- microphone 和 system audio 分轨持久化。
- ASR 不因同一声音被回采而重复追加文本。
- 回放可选择混合或单轨。
- 任一轨失败时页面明确显示，不伪装为完整会议。

## 2026-07-20 当前 PCM v2 与回归补记

本节只更新当前源码事实和证据边界，不把模拟、静态测试或旧 candidate 结果升级为 packaged Go。

- `native_pcm_v2` 已从原生 system-audio/microphone helper 贯通到 backend decoder、ASR event、录音 writer、SQLite `audio_chunks` 和共享 UI。每个 native frame 现在携带并严格校验 `track`、`capture_epoch`、`sequence`、`timestamp`；5 秒录音 chunk 会持久化 `source_sequence_start/end` 和 `source_timestamp_start_ms/end_ms`。浏览器 PCM 继续兼容无来源范围的 `NULL` 字段。
- SQLite 已增加独立 V3 additive migration `add_native_pcm_source_ranges`；部分、逆序、非法或重试冲突的来源范围 fail closed。该迁移已覆盖 schema、persistence、app callback、恢复和 native stream integration tests。
- 原生 microphone 与 system-audio helper 的 ready 语义统一为：loopback WebSocket 已认证、完整 4,800-sample PCM frame 产生并成功发送后才写 ready；`transport_ready`、`pcm_seen`、`audible_pcm_seen`、`first_pcm_rms`、`pcm_bytes_sent`、`pcm_protocol` 和 `capture_epoch` 分层返回。旧 ready payload 不再被接受。
- backend sidecar 关闭路径已修复为幂等 kill、正常/错误 WebSocket 结束先完成业务收尾再发送 close；测试级 degradation controller 在每个测试前后复位，避免一个故障测试污染后续 LLM/ASR 测试。sidecar/diarization focused `30 passed`，backend 受管 Python 3.13 full `1247 passed, 1 skipped, 1 warning`。
- desktop Rust full 为 `73 passed, 2 ignored`，共享 `frontend_v2` 为 `210 passed`，typecheck/lint/production build 通过；Rust 测试使用仓库外 `artifacts/tmp/cargo-target`。
- 当前真实 ScreenCaptureKit 预检：`CGPreflightScreenCaptureAccess=true`，但 `CGGetActiveDisplayList` 返回 `0`，当前 helper 无可捕获 display 并以 `content_unavailable` fail closed。该结果是可复现的机器显示会话阻断，不是权限通过、静音通过或产品采集通过；机器恢复 active display 后，必须从当前源码重建同一 candidate，再跑 helper、Tauri/backend/ASR/recording/shared UI 三层门禁。
- 当前 `NEXT-001`、`NEXT-002` 仍为 `Partial`；源码接线和自动化契约显著前进，但真实 packaged 同场 PCM、ASR、录音、回放和 UI 证据尚未形成。全量 `NEXT-001..023` 目标继续 `Active`。

### NEXT-003 多人说话人分离与身份标签

状态：Partial / speaker 数据、API、稳定标签、重命名和共享 UI 已实现；真实多人 diarization 未验收

问题：当前 transcript 能保留连续发言，但没有正式 diarization/speaker label，无法可靠表示“谁说了什么”。这会削弱多人会议复盘、行动项归属和决策审计价值。

建议分阶段实施：

1. 先支持用户手动修改/合并说话人标签。
2. 再评估本地中文 speaker embedding 和 clustering，例如 3D-Speaker/CAM++。
3. 用户确认的身份映射与自动聚类结果分层保存。
4. 原始文字和说话人推断分开持久化，错误聚类不得改写原始 transcript。

用户体验边界：

- 单人会议允许隐藏重复的说话人标签，保持文字简洁。
- 多人会议至少显示“说话人 1/2/3”，不得把多人发言伪装成一个连续独白。
- 用户可以在会中或会后把“说话人 1”重命名为真实姓名；同一会议内立即回填，刷新后保留。
- 低置信度和交叉发言显示“说话人未确定”，不得猜测真实身份。
- 未来 microphone/system audio 双轨输入可先提供“我/远端”来源标签；同一远端音轨内的多人仍需要 diarization，不能只依赖输入轨道。
- 每个自然段保留 speaker、起止时间和录音位置，点击时间可回听对应音频。

首个验收出口：至少支持 Speaker 1/2/3 自动分段、手动重命名、刷新后保留和会议导出。

### NEXT-004 自然多人中文技术会议质量验收

状态：Mainline engineering proven / Natural-meeting quality not accepted

问题：当前 r2 同场证据使用中文 TTS 经扬声器外放后由真实麦克风采集，证明了真实采集和工程主链，但不能替代自然多人、口音、噪声、打断和交叉发言质量验收。

建议：固定当前 FunASR，不重新开启无边界模型横评。建立少量、可重复、经同意的自然中文技术会议验收场景。

验收指标至少包括：

- 句末停顿到 final 的延迟。
- 技术词、数字、英文缩写和中英混说准确性。
- 中文标点、断句和长句拆分。
- 噪声、远讲、轻声和交叉说话。
- transcript 时间轴与录音位置对齐。
- LLM 修正是否保持事实，不得补写未说过的内容。

### NEXT-005 实时 AI 延迟与调用效率

状态：Partial / r6 真实 relay 功能主链已贯通；TTFT 约 19.6s，实时 SLO 未关闭；r6 后源码已补失败/repair 逐次记账

当前事实：首条建议在首个 final 后约 19.7 秒提交；三次 correction 在会中执行，但唯一改变文本的 revision 在会议结束后约 4.1 秒可见。

建议目标：

- final：句末停顿后 1-3 秒。
- correction revision：final 后 3-6 秒。
- suggestion draft：final 后 5-10 秒。
- suggestion committed：10-15 秒。
- 复盘页面：结束后立即可进入。
- minutes/approach/index：后台并行，不阻塞文字和录音回放。

候选优化：

- correction completion token 按输入长度动态限制，替代固定 4096。
- correction 和 suggestion 使用独立并发 lane；建议不等待完整修正。
- 连续短 final 使用 2-4 秒语义窗口合并，减少碎片调用。
- 保留 suggestion SSE 流式增量显示。
- 增加 TTFT、排队、完整耗时、取消、重试和 token 指标。

### NEXT-006 长会议稳定性与恢复

状态：Partial resilience implemented / Production soak pending

已实现：录音 journal、分片持久化、native 自动重连、durable jobs、退出回收。

剩余风险：真实 native microphone 曾在约 271 秒出现 socket disconnect；自动重连改善了体验，但没有证明长会议根因完全关闭。

建议验收场景：

- 1 小时和 3 小时连续会议。
- 网络切换和短时断网。
- 麦克风设备切换。
- Mac 睡眠/唤醒。
- backend/ASR worker 异常退出。
- 磁盘不足和写入失败。
- LLM 超时、限流和中转站不可用。
- app 异常退出后恢复未结束会议和已保存录音。

### NEXT-007 本地可观测性与诊断包

状态：Partial logs/metrics exist / Product diagnostic workflow pending

建议：增加用户可触发的一键脱敏诊断包，包含版本、运行时状态、ASR RTF、LLM TTFT、队列深度、dropped frame、录音缺口、错误类别和进程生命周期。

诊断包不得包含 API Key、Authorization、Mac 密码、完整 transcript、原始音频、原始 prompt/response 或用户私有路径。

### NEXT-008 Provider 启动体验与稳定凭据身份

状态：Secure explicit sync implemented / Production identity pending

当前行为：metadata 保存在 `provider-config.json`，API Key 保存在 Keychain；app 重启后需要显式“连接 AI”把凭据同步到新 backend runtime。多个 app 实例不会共享进程内配置。

建议：

- 开发和验收只使用一个固定安装路径。
- 取得稳定 Developer ID/Team ID 后重新验证 Keychain 一次授权行为。
- 稳定签名成立后，再评估后台自动同步；不得在 ad-hoc 多身份阶段自动触发系统授权。
- 页面明确区分“已保存”“已同步”“探测成功”和“远端请求失败”。

### NEXT-009 Mac 公开发布工程

状态：Internal ad-hoc package Go / Public release No-Go

未完成项：

- Apple Developer Program 和 Developer ID Application。
- Hardened Runtime 和最小 entitlements。
- Notarization 和 stapling。
- Gatekeeper 验证。
- 干净 Mac 安装、升级、卸载和权限复用。
- 自动更新和失败回滚。
- SQLite schema migration、备份和兼容策略。
- FunASR model、FFmpeg 和其他依赖的不可变版本、许可证和再分发批准。

### NEXT-010 Windows 客户端

状态：Not implemented for native product capture / No real-machine acceptance

可复用：React、FastAPI、SQLite、V2 pipeline、AI、review、history、export 和大部分 Tauri shell。

需要单独实现：

- WASAPI microphone capture。
- WASAPI loopback system audio。
- Windows Credential Manager。
- Windows 进程树、休眠和设备切换处理。
- WebView2 录音播放验证。
- 签名、安装、升级、卸载和真机测试。

Windows 不需要重写整个产品，但需要新的 platform adapter 和独立发布验收。

### NEXT-011 Web 产品定位与隐私架构

状态：Undecided

待讨论的可选定位：

- A. 仅作为本地开发和浏览器麦克风 fallback。
- B. 作为历史记录、复盘和导出的只读/协作页面。
- C. 作为完整云端实时会议产品。

如果选择 C，必须新增账号、租户、认证、加密、数据保留、删除、审计、配额、计费和隐私告知；浏览器音频将进入远端 backend，不再符合当前“原始音频默认本地”的产品边界。

当前建议优先 A/B，不立即建设 C。

### NEXT-012 数据治理、录音告知和删除策略

状态：Partial / 分类删除、保留策略、审计和共享 UI 已实现；完整 packaged UI/政策验收仍待关闭

待确认：

- 默认保留周期和用户可配置保留周期。
- 录音、文字、AI 派生物是否允许分别删除。
- 导出是否包含录音或只包含结构化内容。
- 多人会议的录音告知和用户确认责任。
- 诊断、崩溃报告和遥测是否默认关闭。
- 未来云同步时的加密、地域和账户删除要求。

### NEXT-013 AI 配置和真实麦克风会前检查闭环

状态：Partial / Provider 保存并连接、浏览器/Native RMS 事件已实现；Native 独立 2-3 秒真实采样门禁仍待完成

当前真实问题：

- Provider 配置“保存”只保存 metadata，不自动关闭抽屉；“测试连接”成功后局部显示“AI 已连接”，外层仍可能显示“AI 待连接”。最近一次真实 probe 已返回 200，因此这是状态一致性缺陷，不是中转站必然不可用。
- 当前桌面“检查麦克风”只确认 native helper 存在；浏览器模式只确认取得 live audio track。两者都没有采样音频、计算 RMS 或展示输入电平，“麦克风可用”不能证明真的收到声音。

产品决策：

1. 正常配置入口使用一个主动作“保存并连接”，依次完成保存、同步运行时和真实连接探测。
2. 三步全部成功后自动关闭抽屉，全站统一显示“AI 已连接 · 模型名”；失败时保留抽屉并定位到保存、凭据、协议、模型权限或网络阶段。
3. “已保存”“已同步”“连接成功”可以保留在诊断详情中，但不能要求普通用户理解三个内部状态。
4. 会前麦克风检查必须真实采集 2-3 秒，实时显示输入电平，并明确区分权限拒绝、设备不可用、已获得权限但没有声音、正常收到声音。
5. 会议进行中保留小型输入电平和来源状态，用户不打开设置也能确认仍在收音。
6. 读取 Keychain 仍只允许由明确用户动作触发，不提取 Mac 密码，不把 API Key 写入普通文件、日志、截图或文档。

验收标准：

- 有效配置点击一次后 5 秒内得到明确成功或可操作失败，成功后抽屉关闭，页面状态与 backend health 一致。
- 重开设置、切换页面和开始会议后状态不得退回错误的“AI 待连接”。
- 对麦克风说话时电平在 300ms 内可见变化；静音设备不得显示“正常收到声音”。
- 会前检查失败不影响用户选择“仅录音/转写继续”，但必须明确告知 AI 或音频缺失能力。

### NEXT-014 用户可读的实时全文、自然段和历史回看

状态：Partial / durable paragraph 数据模型、正式 API、完整全文和共享 UI 已实现；连续语音 packaged/UI 门禁仍待关闭

当前真实问题：

- server VAD 在约 900ms 静音时 final，并在连续讲话达到 15 秒时强制切段。当前 UI 基本把 ASR 技术检查点直接显示成段落，完整表达会从中间断开。
- 用户看到多张碎片卡和不断变化的状态，不容易理解刚才说过什么；长会议中向上翻历史时还可能被自动滚动拉回最新位置。
- “15 秒内部持久化检查点”“当前正在识别的 partial”“用户可读自然段”目前没有形成清晰的三层模型。

目标数据模型：

1. `asr_checkpoint`：内部追加式识别和崩溃恢复单元，可继续受 VAD/最长时长约束，不直接决定页面段落。
2. `active_utterance`：当前正在讲话的临时文字，只在固定区域原位更新，不反复追加为历史卡片。
3. `semantic_paragraph`：一个或多个 checkpoint 经停顿、标点、说话人和 AI 语义完整性合并后的用户可读段落，是页面、AI 分析和导出的主要单位。
4. 原始 checkpoint、自然段映射、AI 修订版本和 speaker 推断分别持久化，任何 AI 处理失败都不得丢失原始文字。

实时页面体验：

- 中间主区始终保留本次会议从开始到当前的完整自然段，不等待会议结束才提供全文。
- 当前发言显示在列表底部的“正在听”行；形成稳定自然段后原位转为正文，不产生重复内容。
- 用户位于底部时自动跟随最新内容；用户主动向上翻历史后立即停止抢滚，显示“有 N 段新内容 / 回到最新”。
- 较早内容可以使用列表虚拟化降低内存，但不得合并成无法定位、无法选择或无法回听的大段文本。
- 支持按时间和说话人浏览；每段可点击时间跳到对应录音。搜索和段落书签作为后续增强，不阻塞首轮修复。
- AI 修正更新同一自然段，不新增一张重复卡；视觉上保留稳定位置，避免内容跳动。

自然段形成策略：

- 工程层只使用停顿、时间、speaker 变化和队列压力决定何时形成“待理解批次”，不使用业务关键词判断决定、风险或待办。
- 连续短 checkpoint 先合并；15 秒强制 checkpoint 只作为内部安全边界，不强制页面换段。
- AI 增量分析返回段落边界调整建议；只能合并/拆分明确目标 ID，不能重写未授权历史。
- 超长连续发言允许形成少量可读自然段，但不得每 15 秒机械生成一张卡。

验收标准：

- 连续讲话 30-60 秒时，内部可以产生多个 checkpoint，但页面按语义形成少量自然段，不出现半句话被强制做成独立卡片的固定 15 秒节奏。
- 30 分钟会议的全部确认文字在会中可回看；向上滚动查看 10 分钟前内容时，新识别结果不会抢走滚动位置。
- partial、原始 final、AI 修订和最终自然段不重复显示同一内容。
- 结束会议前后使用同一份 canonical 全文，不在结束时突然替换成另一套文字。
- 多人场景满足 NEXT-003 的 speaker、时间和录音定位边界。

### NEXT-015 LLM-first 实时会议理解和增量状态引擎

状态：Partial / r6 packaged 真实 Provider 已产生结构化增量和追问；真实 UI、重复可靠性和实时性能仍待关闭

产品决策：

决定、待办、风险、开放问题、主题变化和追问建议属于会议语义，不再由“决定/负责/风险是”等关键词或正则表达式作为正式产品判断。后续以大模型增量理解为唯一正式语义来源；本地确定性代码只负责批次调度、去重、幂等、JSON Schema 校验、证据存在性校验、权限、成本上限和失败恢复，不得用规则生成看似智能的正式结果。

目标处理流程：

```text
local ASR checkpoints
  -> paragraph assembler
  -> stabilized paragraph batch
  -> realtime intelligence job (fast OpenAI-compatible model)
       -> corrected paragraph patches
       -> topic/state delta
       -> decision/action/risk/open-question changes
       -> at most one timely follow-up suggestion
  -> evidence validation + idempotent commit
  -> transcript and AI panels update independently
```

触发与并发设计：

1. partial 不调用模型；只有稳定自然段或等待达到上限的新内容才进入智能批次。
2. 新自然段后 debounce 约 1.5-2.5 秒，快速连续发言合并为一个批次；即使持续讲话，待处理内容最长约 6-8 秒必须入队，保证实时性。
3. 同一会议最多一个实时智能任务执行，后续新内容在队列中合并，不为每个 15 秒 checkpoint 堆积重复请求。
4. ASR、录音和页面追加不等待模型；模型慢或失败时，原始文字仍继续出现。
5. 一个实时智能响应优先同时返回修正和结构化状态增量，减少重复输入和多次计费；Provider 不支持可靠结构化输出时，才按能力档案拆为 correction/suggestion 两条 lane。
6. 会后复盘使用独立后台任务和可选高质量模型，不阻塞实时会议，也不反向改变已确认原话。

每次增量输入只包含：

- 本批新增自然段及稳定 ID、时间和 speaker。
- 前 2-3 个自然段作为只读上下文。
- 有长度上限的当前主题摘要和仍未闭环状态。
- 用户词表、项目术语和会议目标。
- 固定系统约束；Provider 支持时使用 prompt caching。

禁止每次发送完整会议全文。长会议通过滚动状态摘要保持上下文；摘要本身带版本号，必要时异步压缩，原始 transcript 始终是事实源。

结构化输出至少包含：

```text
paragraph_revisions[]: target_id, corrected_text, change_count
topic_update: title, summary, operation
state_changes[]: type, operation, item_id, content, owner, deadline,
                 status, evidence_segment_ids, evidence_quote, confidence
follow_up: question | null, reason, evidence_segment_ids, urgency
```

状态更新采用 `add | update | resolve | noop`，不能每轮重复追加相同决定或风险。每个正式项目必须引用仍然存在的会议证据；没有 evidence ID、引用与原文不符、目标版本过期或结构校验失败时拒绝提交，不能由本地关键词结果顶替。

“AI 建议追问”设计：

- 当前标题先使用诚实的“AI 建议追问”，不宣称已经做到全局“最值得”。
- 模型基于当前未闭环状态、会议目标和最近发言判断是否此刻值得打断；允许返回 `null`，避免为了展示而制造无价值建议。
- 建议必须包含依据片段和简短原因。界面只显示问题，旁边帮助图标悬浮后显示“依据哪段原话、为什么现在提示、由哪个模型批次产生”。
- 后续若增加全局排序，再使用模型对阻塞程度、紧迫性、不确定性、可行动性、重复度和时效性评分，并记录评分依据。

决定、待办、风险和问题面板：

- 每类标题右侧使用帮助图标，悬浮说明“由 AI 根据会议上下文增量识别，仍建议由参会人确认”。
- 每个项目可查看证据原话、时间、speaker、识别置信度和最近更新时间。
- AI 可以自动追加、更新和标记已解决，但不能静默删除历史；错误结果允许用户纠正、合并或驳回，并保留审计记录。
- 模型不可用时显示“AI 分析已暂停”，不得回退到关键词规则并继续生成正式结果。

Token 和成本设计：

- 不发送重复 partial，不重复发送全文，只发送增量、短上下文和有界滚动状态。
- 使用 evidence/version hash 去重；相同批次重试复用同一 job identity，不重复提交结果。
- 输出 token 上限按新增文本长度和允许状态条数动态计算，不再为短修正固定预留 4096 token。
- 实时任务默认路由到低延迟、低成本的国产 OpenAI-compatible 模型；会后复盘可路由到更强模型。只配置一个模型时自动复用同一模型。
- 记录每类任务的 TTFT、总耗时、输入/输出 token、重试、超时和每会议估算成本；达到用户配置的预算上限时先降低分析频率，不停止本地录音和转写。
- 模型忙时合并尚未处理的增量，不并发追赶历史；会议结束后允许后台补齐未完成状态。

目标 SLO：

- 原始文字：句末停顿后 1-3 秒可见。
- AI 修正：稳定自然段后 3-6 秒更新，超时则保留原文并继续会议。
- AI 状态和追问草稿：新增自然段后 5-10 秒可见。
- 实时调用不得阻塞下一段 ASR、录音持久化、滚动浏览和结束会议。

### NEXT-016 AI 修正可靠性、状态可信度和失败隔离

状态：Partial / 幂等、证据校验、一次结构修复和失败隔离已实现；真实 changed diff、完整状态 UI 和失败成本专项仍待关闭

当前真实问题：

- 当前 correction job 会调用 legacy 全局 runner，并把历史 `transcript_revision` 再次重放到 V2。片段已经修订后 evidence hash 改变，后续任务重放旧 revision 会触发 `V2TranscriptRevisionConflict`。
- 最近一次真实会议执行了多次 correction LLM 调用，但只有少量片段形成有效 revision，后续多个任务发生版本冲突。这不是单纯提示词问题。
- 前端只根据 `segment.revision > 1` 显示“AI 已校正”，没有证明文本真的发生变化，也没有展示改动。
- 会后 minutes/approach/index 当前会等待全部 correction job；任一旧 correction 终态失败可能级联阻断已有 canonical transcript 的复盘任务。

修复边界：

1. 实时智能任务按 job/evidence scope 处理，只能修改任务声明的目标自然段；历史上下文只读。
2. 相同 revision ID 和相同 evidence version 的重试视为幂等成功；同一目标版本禁止重复创建有效任务。
3. 提交前验证目标版本；过期任务标记 superseded，不作为产品错误，不回放全部历史 revision。
4. 页面状态区分“等待检查、AI 处理中、已检查无需修改、修正 N 处、修正失败已保留原文”。只有文字实际变化才显示“修正 N 处”。
5. 点击或悬浮修正状态可查看紧凑 before/after diff；默认正文只显示当前 canonical 版本。
6. 会后任务只等待仍活跃的实时任务到有界 deadline。存在 canonical transcript 时，旧修正失败不得阻断纪要、方案或索引；输出记录 `correction_degraded=true` 并允许后续重试。
7. 错误必须区分 ASR、Provider 认证、Provider 超时/限流、结构校验、修订冲突、建议生成和会后任务。用户看到阶段化中文和恢复动作，日志保留脱敏错误类与 job ID。
8. 任一 AI lane 失败都不得停止本地录音、实时原文追加、历史回看和结束会议。

验收标准：

- 同一自然段重复执行、进程恢复和网络重试不会产生重复修订或 revision conflict。
- “AI 已修正”必须有真实文本差异和可查看证据；无改动显示“已检查，无需修改”。
- 人为让实时模型超时后，原文、录音和下一段 ASR 继续；恢复 Provider 后只补处理缺失批次。
- 任一 correction job 终态失败时，已有完整文字的会议仍能生成复盘，并明确标记文字修正降级。
- 页面不再使用无阶段信息的统一“AI 处理失败”。

### NEXT-017 可编辑会后文档、版本和自动保存

状态：Partial / 五类 user_final、持久化、版本和自动保存已实现；共享 packaged UI、冲突和断网草稿仍待验收

当前真实问题：

- 会后复盘使用只读 Markdown 渲染，会议纪要、方案与风险、决策与待办和完整文字都不能直接编辑。
- 当前 AI 生成结果被当作最终展示结果，没有“AI 初稿”和“用户最终稿”的版本边界。
- 用户修改文字后，没有记录哪些内容由用户改过，也没有阻止后续 AI 生成覆盖人工修改。

产品决策：

1. 会后页面改成可继续工作的会议文档，而不是一次性报告。主区域提供“复盘文档、决策与待办、风险与建议、完整文字、录音”五个清晰工作区。
2. 复盘文档默认阅读模式，点击编辑后进入编辑模式；支持撤销/重做、自动保存、保存状态和失败恢复。编辑器使用成熟的结构化文档模型，不手写脆弱的 `contenteditable`。
3. AI 生成内容保存为 `ai_generated` 版本，用户修改保存为 `user_final` 版本。AI 重新生成只能产生新草稿或差异，不能直接覆盖用户已经修改的内容。
4. 决策、待办、风险和建议使用结构化字段编辑，而不是只编辑一段 Markdown。每项保留内容、状态、负责人/截止时间、证据、speaker 和时间。
5. 完整文字采用三层数据：原始 ASR、AI 修正版、用户最终文字。用户编辑最终文字，原始 ASR 永远保留；用户修正后必须标记派生复盘处于旧版本。
6. 用户编辑不增加远程费用；自动保存只写本地 backend/SQLite。AI 重新整理必须是用户明确触发的动作。

建议持久化模型：

```text
review_document(meeting_id, document_kind, source_revision, content_json,
                ai_version, user_version, updated_at, dirty_state)
review_document_revisions(document_id, revision, author, patch, created_at)
```

最小接口契约：

- `PATCH /v2/meetings/{meeting_id}/documents/{document_kind}`：带 `expected_revision` 的乐观并发保存。
- `GET /v2/meetings/{meeting_id}/documents/{document_kind}/revisions`：查看历史版本。
- `POST /v2/meetings/{meeting_id}/documents/{document_kind}/regenerate`：生成新 AI 草稿，不覆盖用户稿。
- transcript 段落编辑必须使用独立用户版本，不修改原始 ASR 事件。

验收标准：

- 用户可以编辑复盘正文、决策、待办、风险、建议和完整文字；刷新页面后内容仍然存在。
- 断网/服务暂时不可用时，已输入的未保存内容保留在本地草稿，并在恢复后提示继续保存。
- AI 重新生成不会覆盖用户已修改部分，能查看差异并选择替换、合并或放弃。
- 用户修改会议文字后，页面明确提示当前复盘基于旧文字，可以手动“基于最新文字重新整理”。

### NEXT-018 会议命名、历史搜索和长期历史管理

状态：Partial / 命名、搜索、游标分页和状态筛选已实现；packaged 长历史和完整 UI 仍待验收

当前真实问题：

- 数据库已有 `meetings.title` 字段，但前端创建会议没有传入标题，近期会议大量显示“未命名会议”。
- 历史列表当前只取前 8 条，无法长期管理会议。

产品决策：

1. 会前开始会议时提供可选“会议名称”。
2. 用户未填写时先使用稳定兜底名称，例如“2026年7月18日 15:20 的会议”，不得显示空白或“未命名会议”。
3. 稳定识别到前几段内容后，可以由实时 LLM 生成简短主题名称；一旦用户手工编辑过标题，设置 `title_source=user`，AI 不得覆盖。
4. 导入录音默认使用文件名作为初始标题，但用户可以立即修改。
5. 历史列表增加搜索、分页/加载更多、按时间排序和按状态筛选；列表显示文字、录音、复盘和失败状态。

最小接口契约：

- `PATCH /v2/meetings/{meeting_id}`：更新 title，带长度、空白和路径安全校验。
- snapshot/history 返回 `title_source` 和 `updated_at_ms`。

验收标准：

- 新建会议、导入录音和从历史打开的会议都有可读名称。
- 用户在会中或会后修改标题后，页面顶部、历史列表、导出文件名和刷新结果一致。
- 用户改过标题后，后续 AI 主题分析不会把标题改回去。
- 超过 8 条历史后仍然可以找到旧会议，不因前端截断而消失。

### NEXT-019 会后任务独立重试和失败隔离

状态：Partial / 三类独立 retry API/UI 和保稿行为已实现；packaged 失败交互专项仍待验收

当前真实问题：

- 会议纪要、方案/风险和索引是独立后端任务，但页面没有按产物提供“重新生成”入口。
- 会议纪要失败后，决策、待办和风险页面依赖纪要解析，导致已有会议文字和实时事实无法继续使用。
- 用户看不到失败阶段、错误原因、重试次数和可执行恢复动作。

产品决策：

1. 每个产物独立显示 `等待、生成中、已完成、部分完成、失败、可重试`。
2. 失败只影响对应产物，不隐藏文字、录音和其他已经完成的事实。
3. “重新生成会议纪要”“重新生成方案与风险”“重新生成索引”分别创建新的幂等任务，使用当前 canonical transcript revision。
4. 已有用户编辑稿时，重新生成只产生 AI 草稿，不能覆盖 user_final。
5. 如果 Provider 未连接、超时、限流、返回结构错误或本地服务不可用，页面展示阶段化原因和对应动作。
6. 决策/待办/风险优先读取实时 AI 结构化事实和用户最终稿，不再只依赖 minutes Markdown 是否生成成功。

验收标准：

- 纪要失败时，用户仍能查看完整文字、录音、会中事实和方案卡，并能单独重试纪要。
- 任何一个重试任务不会重复扣费或重复追加同一条事实。
- 重试成功后只更新对应产物，其他用户编辑内容保持不变。
- 页面明确显示“文字和录音已保留”，不把一个 AI 任务失败描述成整场会议失败。

### NEXT-020 Markdown、DOCX、JSON 用户最终稿导出

状态：Partial / Markdown、DOCX、JSON user_final 导出已实现并有 packaged API 证据；UI 下载和失败重试专项仍待验收

产品决策：

1. 导出默认使用用户最终稿，而不是未经用户确认的 AI 初稿。
2. 菜单提供 Markdown（`.md`）、Word 文档（`.docx`）和 JSON 数据（高级）三种格式。
3. DOCX 使用本地 backend 生成，不上传会议内容到额外服务；文件包含会议名称、日期、时长、复盘、决策、待办、风险、问题、完整文字和 speaker/时间信息。
4. 录音作为独立 WAV 文件导出，不默认嵌入 DOCX，避免生成过大的文档。
5. 文件名使用经过清洗的会议名称和日期，例如 `支付服务上线方案讨论-2026-07-18.docx`。

验收标准：

- 导出的 Markdown 和 DOCX 与页面当前用户最终稿一致。
- 重新编辑后再次导出，旧导出文件不被静默覆盖。
- 导出失败说明是文档生成、权限、磁盘还是下载阶段，并允许重试。
- JSON 保留原始 ASR、AI 修正版、用户最终稿、证据和审计信息，供后续迁移使用。

### NEXT-021 录音导入面板、格式提示、进度和后台任务

状态：Partial / 导入面板、durable 后台任务、阶段进度和 packaged 本地模型已实现；共享 UI、离开页面恢复和失败恢复仍待验收

当前真实问题：

- 当前页面直接打开文件选择器，只检查空文件和 500MB 大小，没有解释格式、处理步骤、数据边界或预计状态。
- 导入请求包含上传、转码、离线批量 ASR 和会议创建等阶段，但 UI 只显示一条“正在导入并转写录音”。
- “offline”内部错误可能被用户理解为网络断开。

产品决策：

1. 点击“导入录音”先打开导入面板，说明支持格式、大小限制、本地处理边界和处理阶段；选择文件后再确认开始。
2. 首发 UI 只宣传经过 packaged runtime 验收的格式：WAV、MP3、M4A、AAC、FLAC、MP4、MOV。其他格式在能力确认前不在选择器中宣传。
3. 单文件默认上限保持 500MB，并在选择文件后立即显示文件名和大小。
4. 导入改为后台 durable job，阶段至少包括：文件读取、标准化转换、本地中文转写、文字校正、会后整理。
5. 用户可以离开导入页面；历史列表显示导入进度、失败阶段和“继续查看/重试”。
6. 失败文案使用“本地文件转写组件未安装/本地转写失败/格式无法解析”等用户语言，不直接展示 `offline batch path` 等内部英文。
7. 录音文件默认只在本地处理和保存，导入不新增远程 ASR 费用；AI 整理仍遵循当前 Provider 成本策略。

建议交互：

```text
导入录音
  -> 选择文件 / 拖拽文件
  -> 显示格式、大小、会议名称
  -> 开始导入
  -> 读取 -> 转换 -> 本地转写 -> AI整理
  -> 打开会议复盘
```

验收标准：

- 用户在选择文件前就知道支持格式和 500MB 限制。
- 导入过程中页面不假装空闲，也不只显示“离线”。
- 本地 backend 或文件转写组件不可用时，明确显示具体组件和修复动作。
- 导入失败不留下半个历史会议；如果已经保存原始录音，用户可以从失败任务恢复或删除。

### NEXT-022 安装包离线文件转写运行时和模型打包

状态：Partial / r6 packaged 文件 ASR runtime/models/converter 和 direct-backend 三格式真实执行已通过；UI/Rust 同进程、clean Mac、供应链和公开发布仍未关闭

当前真实问题：

- 当前安装包只带实时 FunASR 模型；`batch_transcribe.py` 仍使用开发仓库虚拟环境和用户模型缓存路径。
- 当前 packaged backend 仍然运行，不应把“离线批量模型未打包”显示成“服务离线”。
- 离线 Paraformer、VAD 和中文标点模型合计约 2GB 级别，纳入安装包会增加体积，需要明确发布策略。

产品决策：

1. 生产运行时必须通过环境注入或 runtime manifest 解析 bundled backend Python、FunASR Python、worker 和模型路径，不允许使用开发仓库绝对路径或用户缓存路径。
2. 首发默认采用“本地文件转写组件”方案，不接付费远程 ASR。
3. 离线模型可以作为安装包内可选资源或首次启动时下载的本地模型包，但必须显示体积、校验 SHA-256、版本和安装状态；没有安装时功能应明确为“文件导入组件未安装”，不能伪装离线。
4. 如果采用首次下载模型包，下载只用于本地模型资源，不上传用户音频；安装完成后导入仍在本地执行。
5. 公开发布前必须完成干净 Mac 无开发仓库、无用户模型缓存情况下的导入验收。

验收标准：

- 干净 Mac 安装包可以独立判断实时 ASR 和文件 ASR 的能力状态。
- 安装包不依赖 `/Users/.../Documents/...`、开发虚拟环境或外部 ModelScope 缓存。
- 文件导入从选择到复盘完整跑通，包含 WAV、M4A、MP3 至少三种格式。
- 缺少可选模型时显示可安装入口和准确原因，不显示“网络离线”。

### NEXT-023 诊断中的“立即同步”改为“重新读取状态”

状态：Partial / “重新读取状态”文案和诊断控件已改；完整页面验收和重复调用回归仍待完成

当前真实问题：

- 当前按钮实际只重新请求当前会议 snapshot，且页面已有定时刷新；它不是云同步、文件同步或 AI 同步。

产品决策：

1. 普通用户页面不展示“立即同步”。
2. 诊断抽屉中的按钮改名为“重新读取状态”，tooltip 说明“重新从本地会议服务读取当前状态”。
3. “最后同步”改成“最后读取”；连接状态改成用户语言。
4. 未来如果建设云同步，单独设计云账户、同步范围、冲突处理和隐私提示，不复用该按钮。

验收标准：

- 用户能理解按钮只刷新本地状态，不会误以为数据上传云端。
- 诊断刷新不会重复创建会议、重复调用 AI 或重复上传录音。
- 普通页面不再出现没有产品定义的“同步”动作。

## 5. 当前建议优先级

### P0-A：先修通用户每天直接感知的实时主链

1. NEXT-014 用户可读实时全文、自然段、历史回看和稳定滚动。
2. NEXT-015 用 LLM 增量理解替换关键词语义提取，统一修正、状态和追问。
3. NEXT-016 修正任务幂等、可信状态和失败隔离。
4. NEXT-013 Provider 配置闭环和真实麦克风电平检查。
5. NEXT-005 实时模型延迟、流式反馈、Token 和 SLO 指标。

P0-A 完成前不再把当前客户端描述为生产级实时会议产品，也不以新的 ASR/provider 横评替代主链修复。

### P0-B：会后可交付与导入闭环

1. NEXT-019 会后任务独立重试和失败隔离。
2. NEXT-017 可编辑会后文档和用户最终稿版本。
3. NEXT-018 会议命名、历史搜索和长期历史管理。
4. NEXT-020 Markdown、DOCX、JSON 用户最终稿导出。
5. NEXT-021 录音导入面板、进度和本地后台任务。
6. NEXT-022 安装包离线文件转写运行时和模型打包。
7. NEXT-023 诊断术语和刷新动作收敛。

### P0-C：证明产品覆盖真实多人会议

1. NEXT-001 Mac system audio 单源正式接入。
2. NEXT-003 多人 speaker label、手动纠正和后续本地 diarization。
3. NEXT-004 自然多人中文技术会议质量验收。
4. NEXT-006 先完成 1 小时、再完成 3 小时稳定性与恢复。

### P1：Mac internal alpha 到可分发候选

1. NEXT-007 脱敏诊断包和运行指标。
2. NEXT-008 稳定签名身份下的 Provider/Keychain 体验。
3. NEXT-009 Developer ID、公证、clean Mac 和供应链。

### P2：跨平台扩展

1. NEXT-010 Windows WASAPI microphone。
2. Windows WASAPI loopback system audio。
3. Windows 凭据、安装和真机发布门禁。

### P3：产品形态扩展

1. NEXT-011 决定 Web 是本地 fallback、复盘协作还是云端产品。
2. NEXT-012 数据治理、录音告知和云同步边界。
3. NEXT-002 双轨 mixed capture。

## 6. 当前决策和后续确认点

### 6.1 本轮已经确认，不再阻塞实施拆分

1. 第一产品主线继续是 macOS 桌面客户端；Windows 和移动端不阻塞当前实时体验修复。
2. ASR 默认本地免费执行，原始音频默认只在本机保存；主要可变费用只来自用户配置的 OpenAI-compatible LLM 中转站。
3. 决定、待办、风险、开放问题、主题和追问全部改为 LLM-first 增量理解，不以关键词/正则作为正式语义结果。
4. 实时模型优先低延迟、低成本国产兼容模型，会后模型允许更强；只有一个模型时复用同一配置。
5. 本次会议的完整文字在会中必须持续可回看；内部 15 秒 checkpoint 不得成为固定用户段落。
6. 多人会议必须进入产品规划：先提供 speaker 标签和人工修正，再接本地 diarization；无法确定身份时不得猜姓名。
7. 实时 AI 失败必须保留原文和录音，不能阻断下一段识别和会后复盘。
8. 会后 AI 产物不是最终稿，必须支持用户编辑、保存、版本和导出；AI 重新生成不能覆盖用户修改。
9. 会议名称是正式产品字段；用户标题优先，AI 主题名只能作为未手工命名时的建议。
10. 最终交付以本地优先 packaged client 为真相；Web 只作为共享 UI/接口的开发和备用入口，不维护第二套前端业务实现。
11. 普通自测优先使用模拟音频、fake microphone、fake LLM 和仓库外测试凭据；真实麦克风、Keychain 和未来系统音频权限只执行有明确证据目标的 packaged gate。
12. P0-A 按本文默认方案进入后续 SDD/TDD 拆分，不需要用户再逐项选择技术实现。

### 6.2 公开发布前再确认，不阻塞 P0-A

1. 首个公开版本是否必须包含 system audio，还是先发布明确标注 microphone-only 的有限 alpha。
2. Web 最终只做本地 fallback/复盘协作，还是建设会上传音频的远端 SaaS。
3. 账号、云同步、多设备历史和企业租户是否进入首发。
4. 录音和文字的默认保留周期以及分别删除策略。
5. 自动更新、完全离线 LLM 和企业合规能力是否进入首发。
6. 用户对实时 AI 的默认每会议预算或月度预算上限。

这些问题暂不需要用户现在确认；到对应发布阶段再形成独立决策，不允许阻塞当前实时主链修复。

## 7. 后续 SDD/TDD 执行规则

每个确认实施的 NEXT 项必须先完成：

1. 产品目标、非目标和用户场景。
2. 平台边界、权限、隐私和成本边界。
3. 事件、API、存储和错误状态契约。
4. 可测量的成功标准和 No-Go 条件。
5. 失败恢复、删除、迁移和兼容策略。
6. RED 测试或可重复验证入口。
7. 最小实现、focused GREEN、完整回归和真实页面验收。
8. 脱敏 evidence 和 decision-log 收口。

禁止再次出现：以 mock 页面代替真实主链、用多个旧实例拼接证据、为了通过而放宽质量门禁、把可行性 spike 写成正式产品功能、或在未确认产品方向前大规模重构。

## 8. 当前明确不立即执行的事项

- 不重新启动大范围 ASR/provider bake-off。
- 不接付费远程 ASR。
- 不把 API Key 改为仓库或普通 `.env` 明文存储。
- 不绕过 Keychain、TCC 或系统权限。
- 不在 system audio 未接入前展示假 `mixed` 选项。
- 不宣称 Web 与客户端功能完全一致。
- 不宣称 Windows 已支持原生会议采集。
- 不宣称 internal ad-hoc app 已具备公开发布条件。
- 不在 P0-A 的 SDD、接口、存储迁移和 TDD 契约完成前开始大规模重构。
- 不通过文件夹权限、脚本化 SecurityAgent、Mac 密码提取或放宽 TCC/Keychain ACL 绕过系统授权。
- 不把 Web 通过、旧端口通过或开发环境通过写成 packaged client 发布通过。

## 9. 现有证据和关联文档

- `docs/p0-p2-completion-report-2026-07-17.md`
- `docs/current-mainline-index.md`
- `docs/runtime-operating-constraints.md`
- `docs/full-roadmap-execution-checklist-2026-07-18.md`
- `docs/decision-log.md`：DEC-416、DEC-419、DEC-420、DEC-421、DEC-422、DEC-423、DEC-424、DEC-425、DEC-426、DEC-427、DEC-428、DEC-429、DEC-430、DEC-431
- `artifacts/tmp/local-alpha/packaged_ui_mainline/phase0-2-current-20260718-r2/evidence.json`

## 10. 变更记录

- 2026-07-18：用户确认新增 NEXT-017 至 NEXT-023。正式确定会后产物采用 AI 初稿/用户最终稿双层模型，支持编辑、自动保存、独立重试、会议命名、Markdown/DOCX/JSON 导出；录音导入改为本地后台任务并补齐格式/进度提示；安装包必须补齐离线文件转写运行时和模型；诊断“立即同步”改为“重新读取状态”。建立条目时尚未开始实现，当前状态以各 NEXT 条目和全量清单为准。
- 2026-07-18：用户确认本地优先压缩包交付、共享 Web/客户端 UI 契约、固定 macOS 应用身份减少重复授权、普通自测不依赖用户反复守在电脑旁，以及不接云同步/远程 ASR的运行约束；新增 `docs/runtime-operating-constraints.md` 和 DEC-426。
- 2026-07-18：曾把用户所说“完成持续追加的文档”错误理解为只整理 Markdown 文档，并据此新增 DEC-428 至 DEC-430。用户再次明确其含义是完成文档中持续追加的全部事项及自测；DEC-431 恢复全量执行范围，DEC-428 至 DEC-430 仅作为被覆盖的历史误判保留。
- 2026-07-18：根据真实用户试用新增 NEXT-013 至 NEXT-016。记录 Provider 状态不一致、伪麦克风检查、15 秒技术分片直接展示、历史回看受干扰、修正任务冲突、AI 状态失真和错误级联；确认用 LLM 增量理解替换关键词语义提取，并记录 Token、延迟、多人 speaker 和用户视角验收边界。建立条目时尚未开始实现，当前已开始部分实现，准确状态以各 NEXT 条目和全量清单为准。
- 2026-07-18：建立首版讨论稿，记录 Phase 0-2 之后的系统音频、多人说话人、自然中文质量、AI 延迟、长会议、诊断、Mac 发布、Windows、Web 定位和数据治理缺口。未启动实现。
- 2026-07-18：历史上新增 DEC-428、DEC-429、DEC-430，将目标收窄为只关闭文档包；该解释已被用户明确否定，并由 DEC-431 覆盖。
- 2026-07-18：新增 DEC-431。当前唯一目标是完成本文档包列出的全部事项及对应自测、证据和真实阻断审计，不能以文档一致性检查代替代码与产品验收。

## 11. 2026-07-19 最新工程证据与发布边界

本节追加记录晚于前文状态快照的证据；`NEXT-001` 至 `NEXT-023` 仍是不可缩减范围。

### 11.1 NEXT-022 资源包直接 backend 证据与审计校准

- 本机 packaged 本地文件 ASR 已对 WAV、M4A、MP3 完成真实模型 smoke，三项导入任务均成功并持久化。证据为 `artifacts/tmp/next022-packaged-file-asr-smoke-20260719-r2/evidence.json`，`status=passed`、总耗时 `137.139s`。
- 旧报告写入 `fake_asr_used=false`、`remote_asr_used=false`、`global_ffmpeg_used=false`；对抗审计发现这些字段当时是静态声明，且 runner 未清除全部宿主 ASR 覆盖变量，因此只能保留为待加固观测，不能作为独立范围证明。
- 本轮观测到三个模型冷启动约 `74s`、模型执行 RTF 约 `0.46`；app 逻辑体积约 `4.50GB`（`4,498,694,100 bytes`）。这组数字用于记录当前工程代价和后续优化基线，不构成模型质量 benchmark 或发布 SLO 结论。
- 前文“packaged 文件 ASR 模型完全缺失”已不准确，但旧 smoke 直接启动 app 资源包 backend，没有经过 Tauri/Rust supervisor。当前准确状态为 `Partial`：真实模型和三格式资源存在且执行过，防环境劫持、sealed hash、Tauri supervisor、完整 packaged 导入和 clean Mac 尚未全部关闭。

### 11.2 外部公开发布门禁仍为 No-Go

- 内部受控 model-pack manifest 已依据上游 Apache-2.0 标记可打包，但当前模型版本标识仍是 mutable `master`；公开发布要求的不可变上游 revision、完整模型/依赖再分发审批仍未闭环。内部 manifest 的 `redistribution_status=approved` 不等于发布级供应链审批全部完成。
- Developer ID 稳定签名、Hardened Runtime 最小权限、公证/stapling、Gatekeeper 和独立 clean Mac 安装/升级/运行验收仍未通过。
- `NEXT-009` 继续保持 **Public release No-Go**。NEXT-022 的本机 packaged 工程 smoke 只能关闭内部工程门禁，不能写成签名包、可再分发包、clean Mac 或公开发布 Go。

### 11.2 r3 packaged 内部工程闭环

2026-07-19 使用同一 `full-roadmap-candidate-20260719-r3` 完成以下闭环：

1. package inventory、App binary 和 runtime manifest 固定哈希；大目录启动 shape preflight `6.22s`。
2. Tauri/Rust supervisor 启动 bundled backend 和 FunASR，实时中文 final、认证 bootstrap、进程和端口自然清理均通过，耗时 `18.604s`。
3. hardened direct backend gate 把打包 evidence 与当前 App 绑定后完成 WAV/M4A/MP3 三格式真实本地模型转写，耗时 `79.97s`；未使用 fake ASR、远程 ASR 或全局 FFmpeg。
4. packaged authenticated API gate 的 `37/37` 项通过；可编辑最终稿、独立重试、三格式导出、历史、录音、诊断和删除均由当前包实际执行。
5. packaged fake-AI 主链在 `20.813s` 内完成实时理解、追问、复盘和录音保存；该证据只证明 OpenAI-compatible 编排，不替代真实中转站模型质量和延迟。

本轮同时发现并保留两个真实启动问题：旧同 Bundle ID 客户端会干扰新候选实例；AppKit 在异常退出历史下可能先弹 state-restoration modal，使 setup 无人值守阻塞。自动化 runner 已统一关闭状态恢复提示，但真实产品的崩溃恢复体验仍需 NEXT-006/NEXT-009 的安装态专项验证。

结论只提升 NEXT-022 为 **Internal packaged engineering Go / overall Partial**。公开再分发、稳定签名、公证、clean Mac、真实 TCC、真实 relay、自然多人中文和其他 NEXT 条目没有被本证据关闭。

### 11.3 375px 产品 UI 回归

- 已修复 `375px` 下“录音”tab 与导出按钮 overlap。in-app Browser 报告 `artifacts/evidence/v2-product-smoke-iab/report.json` 的 `verdict=go`；`exportOverlapWidth=0`、录音 tab 全宽可见、无横向溢出，`browser_error_count=0`。
- frontend full 为 `18 files / 146 tests passed`。该结果关闭本次共享前端响应式回归，不替代 packaged 原生权限、供应链或公开发布门禁。

统一状态口径：只有防环境劫持、sealed hash、Tauri/Rust supervisor 与指定 packaged 链路同场通过后，才能写 **Internal engineering Go**；**Public release No-Go/Blocked** 仍由签名、公证、不可变来源、再分发审批和 clean Mac 决定。任何资源包 direct-backend 证据或内部 Go 都不得向外推导为 NEXT-009 或全量产品发布 Go。

## 12. 2026-07-19 r6 真实 Provider 主链与性能结论

- r5 真实 relay 不是成功证据。其会后 minutes/approach 真实调用成功，但实时 intelligence 因 `IntelligenceResponseValidationError` 终止，没有修正终态、实时状态、追问或 TTFT trace，最终为 `no_go_packaged_real_remote_llm_mainline`。
- 失败复现确认原 prompt 只给字段名，未把 `urgency` 等枚举和空值规则写完整；同一公开合成技术会议曾返回 `follow_up.urgency=null`。原动态输出预算最低仅 `256`，也不足以稳健容纳中文修正、状态和追问。
- 正式修复补齐类型、枚举、null、逐字 evidence 和 paragraph ID 规则；动态输出预算改为 `768..4096`；严格解析失败只允许一次受控结构修复，仍失败则保留原文并明确失败，不使用关键词语义结果作为 fallback。
- r6 candidate App binary SHA-256 为 `1b6b16ad6fffd7a3771d9c49aed4397cfe23bc2fda9dd36f63f1f05922cb8ef3`。真实证据位于 `artifacts/tmp/packaged_real_provider_mainline_smoke/full-roadmap-candidate-20260719-r6-real-provider-architecture/evidence.json`，状态为 `go_packaged_real_remote_llm_mainline_not_ui_not_public_release`。
- r6 同一 session 内完成 bundled FunASR final、真实 `responses` relay、主题/决定/待办/开放问题/追问、修正 `no_change` 终态、会后 minutes/approach/index 和全部进程清理；3 次调用共 `3125` tokens，原始音频未上传，evidence 不含 API key、完整 URL、原始音频或会议正文。
- 本次 TTFT `19.563s`、provider total `23.460s`，因此产品价值判断是：**能力主链可实现，当前模型/中转站组合的实时体验仍不达标**。`NEXT-005` 必须继续优化，不能用“最终返回了结果”代替实时可用。
- 该证据是 packaged authenticated backend API 主链，不是 Tauri UI 点击、真实 microphone、Screen Recording/TCC、自然多人、长会或公开发布证明。`NEXT-001` 至 `NEXT-023` 总目标继续 Active。
- r6 后源码继续补齐逐 attempt usage 记账、repair 前预算复检以及 validation 分类；evidence/stale/safety 不再触发二次付费修复。段落级 LLM 修正现已复用 transcript correction 的事实保真门禁：负责人、日期、比例数值、决策极性和技术实体发生变化时归类为 `semantic_safety` 并保留原文；`千分之一` 与 `0.1%`、`万分之五` 与 `0.05%` 等等价格式仍允许通过。focused `77 passed`，当前 backend full `1141 passed, 1 warning`；root `516 passed, 1 skipped, 2 warnings`，frontend `151 passed`，Rust `65 passed, 2 ignored` 沿用上一轮完整回归。以上均是当前源码证据，尚未重新打入 r6，必须由下一 candidate 的 packaged gates 接棒。
- r6 同一 binary 的 packaged file-ASR direct-backend gate 也已完成：`artifacts/tmp/next022-hardened-direct-r6/evidence.json`，`81.818s`，WAV/M4A/MP3 均由包内真实模型执行并持久化，未使用 fake/remote/global FFmpeg。它明确不是 Tauri/Rust supervisor 或 UI 导入证据，不能关闭 clean Mac、供应链、签名公证和公开发布。

## 13. 2026-07-19 r13 连续录音与后端崩溃恢复结论

### 13.1 用户价值结论

实时会议的录音、ASR 和 AI 并行链路不能因 backend 短暂崩溃而永久停在“正在重试”。恢复验收必须同时证明：新 backend 进程启动、ASR 建立新 ready session、崩溃后的音频继续进入、已有 transcript/任务/录音块不回退、会议结束后 WAV 可组装。只探测 `/health=200` 不构成用户主链恢复。

### 13.2 r12 RED 与源码修复

- r12 暴露两个确定缺陷：background export 将已有音频 manifest 的 `started_at_ms` 改写为存在约 1ms 舍入差异的 SQLite 值；writer replay 因同一 chunk 的 `captured_at_ms` 不一致而失败。第一次 setup failure 已取得的新租约没有回滚，后续连接全部被旧租约拒绝。
- 音频 journal 现在拥有既有录音时间线；export 只能读取，不能用数据库近似值覆盖。export 与 writer setup 使用同一 per-meeting asset lock，避免并发改写 manifest/chunk 临时文件。
- setup rollback 使用 exact `lease_owner + capture_generation` fence；只释放本次失败 capture，stale callback 不得中断已经开始的新 capture。识别 resident session 同时 abort，不留下 permanently busy worker。
- pipeline trace 从 ASR 业务异常域隔离：生产 observation 的顺序错误只丢弃该指标并记录脱敏 stage/error class，不能终止 WebSocket、录音或 ASR。

### 13.3 r13 GREEN 与边界

- r13 package：`artifacts/tmp/tauri_runtime_package/full-roadmap-candidate-20260719-r13/evidence.json`；binary SHA-256 `49105d37e9b340f36958413295ecdb724a1f07f9fb9ee5c3c8b03a76e9feeaf3`。
- r13 short gate：`artifacts/tmp/next006-real-sut/next006-r13-continuous-backend-recovery/report.json`。RTO `5.934s`，post-fault ASR ready 与 audio growth 均通过，送音覆盖 `53.95s/60s`，录音 `53.104s`、11 chunks、1,699,378 bytes，最终 ready/assembled；meeting end 200，SQLite quick check ok，进程清理通过。
- 这关闭的是 `NEXT-006/backend-crash short gate`，不是 NEXT-006 本身。未配置 Provider 导致 AI jobs 按设计进入可恢复等待；完整 Provider fault matrix、disk/ASR/App crash、1h/3h、sleep/wake 和 device switch 继续开放。
- backend full 在项目锁定 Python 3.13 环境为 `1153 passed`。该结果是当前源码基线；系统 Python 3.14 不满足 `pyproject.toml` 的 `<3.14` 约束，不得混作正式验收解释器。

### 13.4 共享 UI 主链验收

- fake-audio/fake-LLM 的 visible Chrome 主链在第二次尝试因 E2E 误把单轨回放写成旧的整体 URL 而 RED；现场 API 已证明轨道 URL 返回 `206`。修复后的 URL contract 同时覆盖整体/轨道/混合回放，避免测试约束反过来误判当前产品。
- r13 UI GREEN 报告：`artifacts/tmp/browser_live_mic/r13-full-ui-mainline-r3/report.json`；截图在同目录。页面从 preflight、开始会议、实时 partial/final、AI correction/follow-up、结束并整理、四个 review tab、录音播放、完整文字到历史回开均实际操作通过。首文字 `515ms`、首 final `1,024ms`、首建议/修正 `2,955ms`，无浏览器 runtime/console/network/HTTP 5xx 错误。
- 该证据验证共享 React UI 与本地服务的可见业务闭环，不冒充真实 ASR/relay 或公开发布；真实 mic/real FunASR/real relay 的 release gate 继续独立执行。

## 14. 2026-07-19 全量目标对抗审计与方向校准

本节是当前工作目录的严格审计快照，不是新一轮可行性评估，也不缩减 `NEXT-001` 至 `NEXT-023`。多 Agent 按“实现、匹配范围证据、外部阻断”三层重新核对后，得到：`1` 项已按当前定位闭合、`17` 项已有代码但只具备局部或非验收证据、`3` 项仍有实质未实现、`2` 项主要受外部条件影响。该分类说明下一步应补什么，不表示只有一项功能可用。

1. `NEXT-011` 已按本地优先定位闭合：Web 是 loopback-only 的共享 UI 开发、备用和复盘入口，不是云 SaaS；HTTP、SSE 和 ASR WebSocket 使用同一本地 base，显式远端/LAN 地址 fail closed。
2. `NEXT-003` 状态从“真实 diarization 待验收”纠正为“自动 diarization 未实现”。现有 speaker 字段、稳定标签、人工重命名和导出只是兼容数据面，测试夹具中的 `cluster-a/b/c` 不能冒充音频推理。
3. `NEXT-009` 不能整体归为 Apple 外部阻断。Developer ID、公证和 clean Mac 依赖外部条件，但 Hardened Runtime/最小 entitlements、迁移备份兼容和更新/回滚边界仍有本机可实现工作。
4. `NEXT-010` 的 Windows WASAPI、loopback、Credential Manager、进程监督和安装 adapter 尚未实现；当前 JSON fixture 不是 Windows 真机证据。
5. `NEXT-005` 已新增 direct Provider bake-off：`gpt-5.4-mini` 3/3 结构有效，median TTFT `1465.528ms`、P95 total `4956.132ms`，适合作为实时 lane；`gpt-5.6-sol` 保留为通用/会后 lane。双模型配置和 Python runtime 路由已进入当前源码，但必须在新 packaged candidate 上重新证明端到端性能。
6. `NEXT-014/017/020/023` 已有比原文更新的 shared UI 证据：连续语义文字无重复、五类用户最终稿可编辑、三格式实际下载、诊断连续读取不触发 Provider probe。报告均明确 `non_acceptance`，剩余项是 packaged client 和失败专项，不再写成“完全未做”。
7. r13 `37/37` packaged authenticated API 使用明确 fake OpenAI-compatible gateway，只证明当前包内 API/编排；不能外推为真实 LLM、UI、TCC、自然多人或公开发布 Go。
8. 当前源码回归基线：frontend `181/181`、backend 正式 Python 3.13 `.venv` `1162/1162`、desktop Rust `70 passed / 2 ignored` 且 `cargo check` 通过。stable Rust toolchain 缺 `rustfmt`，因此 fmt gate仍开放；系统 Python 3.14 的缺依赖结果不计作正式回归。

执行顺序保持主线优先：先基于当前源码重建 r14 并跑 packaged 主链、真实 Provider、IPC 和文件导入；再完成当前 Mac 可执行的 TCC/稳定性门禁；并行实现自动 diarization 与 Mac 发布的本地工程缺口。自然多人样本、稳定 Developer ID、公证、clean Mac 和 Windows 真机必须形成严格 blocker 包，不得用 mock 替代，也不得因此停止其他可实施工作。

## 15. `8767` 旧工作树与 clean UI 合并边界（DEC-445）

本轮核查确认，用户打开的 `http://127.0.0.1:8767/workbench` 由旧工作树 `/Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend` 提供；它不是当前唯一主线 `/Users/chase/Documents/面试/meeting-copilot-phase0-clean`。因此旧页面的视觉改版没有“自动出现在”当前主线中，继续在 `8767` 上操作也不能验证 clean candidate。

旧 `frontend_v2` 的品牌、窄侧栏导航、布局和动效改版是可复用视觉层，但旧目录与 clean 目录存在大规模业务差异。旧目录约 36 个源文件，clean 主线约 60 个源文件；clean 额外包含本地 API base 安全、Provider 设置、会前检查、会议命名、录音导入、原生音源、speaker revision、可编辑会后文档、独立重试和 Markdown/DOCX/JSON 导出。后续必须增量移植视觉层，禁止整目录覆盖。

产品形态仍是“一套共享 React/TypeScript UI + 平台 adapter”：Web 用于本地开发和交互验证，macOS 客户端用于采集、权限、打包和最终验收。视觉移植完成后，clean 服务必须使用新端口与 `8767` 并排复核；旧端口、旧 mock 数据和旧页面不计入当前产品或发布证据。该事项只推进 UI 合并，不缩减 `NEXT-001` 至 `NEXT-023`，也不表示功能或客户端已经全部完成。

## 16. 2026-07-19 当前源码收敛结论

本节覆盖第 14 节中已经过时的 `NEXT-003/009` 判断，但不删除历史审计过程。当前严格矩阵为：`NEXT-011` 已按 loopback-only 共享 Web 定位实现，`NEXT-010` 仍为 Not started，其余 21 项均为 Partial。Partial 表示已有真实代码或匹配范围证据，但尚缺最终产品/平台门禁，不等于“功能全无”，也不得写成发布完成。

1. `NEXT-003` 已有真实本地 FunASR VAD/CAM++ worker、ASR PCM 旁路、speaker turn/revision 持久化、人工重命名、共享 UI 与导出。新增受控清单固定 VAD `v2.0.4` 和 CAM++ `v1.0.0` 的 allowlisted hashes；真实 worker 对两个同人中文样本和一个不同人样本得到两个连续 speaker turn，前两个合并、第三个分离，且无下载、stdout JSONL 纯净。证据为 `artifacts/evidence/diarization-controlled-real-model-smoke-20260719.json`。它明确不替代 packaged app、自然多人、重叠/口音/噪声、长会议或公开发布验收。
2. `NEXT-009` 已实现 schema v2 正式 bootstrap、迁移备份/回滚、严格 inside-out 非 `--deep` 签名和 Hardened Runtime/identity/entitlements 验证，public release runner 已改为调用该严格 signer。Developer ID 实际身份、公证凭据、stapling、Gatekeeper、再分发审批、clean Mac 和升级/卸载仍未执行，因此准确状态是 `Partial / Public release No-Go`。

## 17. 2026-07-19 本地数据最小权限与无人值守边界（DEC-455）

1. macOS 麦克风、Screen Recording、Keychain 和签名身份不能由 Codex、终端或产品代码强制绕过。自动化采用固定应用身份、一次显式授权、仓库外 `0600` 测试配置和模拟/公开音频减少人工参与；不得修改 TCC、脚本化 SecurityAgent、放宽 Keychain ACL 或关闭 Hardened Runtime。
2. 审计发现历史 packaged app-data 中应用目录、SQLite、会议准备、录音和 native ready/log 存在 `0755/0644`。当前源码已引入 Python/Rust 共享私有存储合同，目录 `0700`、文件 `0600`，backend 对历史数据树执行一次版本化迁移；本机实际收紧 `115` 个目录和 `757` 个文件。
3. backend full 为 `1222 passed, 1 warning`，Rust full 为 `71 passed, 2 ignored`，根级 packaged/audio contract 为 `43 passed`，根级完整回归为 `617 passed, 1 skipped, 1 warning`。本轮关闭 `NEXT-012` 的 POSIX 本地数据最小权限子门禁，但 Windows ACL、真实 TCC、Developer ID、公证和 clean-machine 仍未关闭。
4. 最新 scripted ASR + fake LLM 浏览器主链 `artifacts/tmp/browser_live_mic/r15-mainline-final-20260719-rerun2/report.json` 为 `passed_non_acceptance`：首文字 `511ms`、首 final `713ms`、首建议/修正 `2952ms`，实时到会后主链通过；它不改变真实 ASR、真实 Provider、packaged client 和发布边界。
3. SQLite V2 fingerprint 现在覆盖基础 DDL、additive columns、实体表重建、标题/数据回填、索引和 seed。唯一已知预发布 fingerprint 只有在完整表/列/索引/约束与回填不变量通过只读审计后才可升级；缺 `suggestions.feedback_at_ms` 或 `idx_deletion_jobs_idempotency` 均保持旧 fingerprint 并 fail closed。
4. diarization 子进程在全后端回归中暴露真实关闭竞态：父线程持锁等待子进程时，stderr drain 线程无法取得同一锁，日志管道写满后形成死锁；`finish()` 也会在 worker 尚未报告 ready/unavailable 时过早 kill。现已改为在总超时内等待启动结论、阻塞式 process wait 不持状态锁、强杀后 reap；原失败用例连续 10 轮通过，backend full 为 `1218 passed, 1 warning`。
5. frontend 当前为 `197/197`、typecheck、lint、production build 通过；desktop Rust 为 `70 passed, 2 ignored`、cargo check 和受控 rustfmt 通过。root suite 在允许 loopback/Swift cache 的环境中只剩一次命中 public runner Agent 中间态的失败，严格 signer focused 随后为 `26/26`；最终 root 数字必须在并行改动全部停止后重新记录。

下一执行顺序保持不变：先完成 packager 的 symlink/metadata/incomplete-model fail-closed 加固，随后用当前受控 VAD/CAM++ 清单构建新 candidate；再跑 packaged supervisor、文件 ASR、diarization、共享 UI、真实 Provider 和本机权限专项。自然多人、Windows、Developer ID/公证、再分发和 clean machine 继续形成独立真实门禁，不得用本节内部 smoke 替代。
