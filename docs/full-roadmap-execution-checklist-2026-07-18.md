# Meeting Copilot 全量路线执行清单

时间：2026-07-18

状态：Active full-roadmap execution / 逐项实现与验收中

当前持久目标：以本清单、`post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md`、`runtime-operating-constraints.md` 与 `docs/decision-log.md` 为不可缩减的执行范围，完成 `NEXT-001` 至 `NEXT-023` 的实现、自测、共享 Web/packaged UI、必要平台验收、脱敏证据和状态回写。

明确边界：文档文字完整只是执行基线，不是目标完成。总目标只有在所有 NEXT 条目达到关闭证据，或确受外部资质、硬件、真实样本、Windows 真机或系统权限阻断的条目形成严格的可复现阻断结论后才可关闭。Web、fake microphone、fake LLM、spike 或历史 evidence 不能冒充 packaged client、真实输入源、自然多人质量、Windows 或公开发布 Go。

## 关闭规则

- `Implemented`：代码已合入当前工作树，focused/full tests 通过，并有对应运行证据。
- `Partial`：部分能力可用，但仍有产品或平台缺口，不能写成完成。
- `Blocked`：阻断原因来自外部系统/资质/用户授权/硬件/发布环境，必须有可复现证据和替代路径。
- `Not started`：尚未实现或只有 spike/设计，不得用 mock 结果关闭。

## 当前基线

- Web 与 packaged client 共用 `frontend_v2` React/TypeScript UI、API schema、事件和 reducer；平台差异通过 adapter 表达。
- 2026-07-19 当前源码 frontend full：`24 files / 199 tests passed`；typecheck、lint 和 production build 通过。构建产物主 JS 约 `504 KB`，后续做按需加载，但这不是当前主链阻断。
- 2026-07-19 当前源码 backend 正式 `.venv`（Python 3.13）full：`1218 passed, 1 upstream warning`。系统 Python 3.14 不满足项目 `<3.14` 约束，不计作正式回归环境。
- desktop Rust：`70 passed, 2 ignored`，`cargo check --locked --offline` 通过；误生成在源码树的 `target/` 已移动到约定的 `artifacts/tmp`，后续必须显式使用外部 `CARGO_TARGET_DIR`。系统 stable toolchain 缺 `rustfmt`，本轮已改用历史受控 `rustfmt 1.9.0-stable` 完成 `cargo fmt --all -- --check`。
- Mac system-audio 静态、Swift helper 构建、协议和 Tauri 接线专项：`19 passed, 1 warning`。
- fake audio/fake LLM 范围隔离合同：`4 passed`；只作为无人值守非验收主链证据，不计入真实发布 Go。
- packaged r2 backend/ASR 进程：曾经真实运行；当前客户端最终能力仍需在本轮改动后重新 smoke。
- 当前工作树含历史未提交改动；任何实现必须保留并适配已有改动，不得回滚用户或其他 Agent 修改。
- 以上测试数字和 packaged 记录是既有基线证据，不是当前 dirty tree 的回归结论；每个实施批次都必须重新运行对应测试并绑定当次代码和 evidence。
- 2026-07-19 runtime identity gate：workbench focused `12/12` 通过；临时端口 `8791` 的 clean runtime start/status/stop 通过，受管 identity 为 `0600`，进程已回收。旧/外来进程、伪装 health contract 和 foreign PID 均 fail closed，不自动 kill。该结果只证明当前 clean runtime 身份绑定，不关闭任何实时 ASR、真实 Provider、TCC、packaged UI 或发布门禁。
- 2026-07-19 r6 后当前工作树回归：paragraph correction 已增加负责人、日期、比例、决策极性和技术实体事实保真门禁，并允许中文分数与等价百分比规范化；focused `77 passed`，backend full `1141 passed, 1 warning`。上一完整跨栈回归仍为 root `516 passed, 1 skipped, 2 warnings`、frontend `19 files / 151 tests passed` 且 typecheck/lint/production build 通过、desktop Rust `65 passed, 2 ignored` 且 `cargo check --locked --offline`/rustfmt check 通过。root 唯一 skip 仍是未注入真实 NEXT-022 packaged file-ASR candidate/evidence 的显式门禁；这些源码增量尚未打入 r6。

## 全量清单

| ID | 范围 | 当前状态 | 关闭证据 |
|---|---|---|---|
| NEXT-001 | Mac system audio 单源 | Partial / Swift ScreenCaptureKit helper、Rust supervisor、Tauri command 和共享 UI 已接线；真实 packaged TCC/PCM 主链待验收 | ScreenCaptureKit packaged capture、ASR、录音、回放和权限拒绝测试 |
| NEXT-002 | microphone + system audio 双轨 | Partial / 双轨生命周期、独立持久化、来源去重、单轨/混合回放 API、Rust coordinator 和共享 UI 已实现；真实 packaged 双轨同场待验收 | 双 track 持久化、去重、混合/单轨回放和任一轨失败 UI |
| NEXT-003 | 多人 speaker label/diarization | Partial / 真实本地 FunASR VAD/CAM++ worker、PCM 旁路、speaker revision/persistence/UI/export 已实现；受控同人/异人样本真实模型 smoke 通过，packaged 模型硬化与自然多人验收未关闭 | 受控模型安全打包、packaged inference、自然多人 Speaker 1/2/3、重命名/刷新/导出和低置信度验收 |
| NEXT-004 | 自然多人中文质量 | Partial / 受控音频不能替代自然会议 | 经同意自然场景、术语/口音/串音/延迟报告 |
| NEXT-005 | 实时 AI 延迟和成本 | Partial / direct Provider bake-off 找到 `gpt-5.4-mini` 实时候选：3/3 结构有效、median TTFT `1465.528ms`、P95 total `4956.132ms`；双模型路由源码已实现，但尚未进入新 packaged 全链 | 新 candidate 的 TTFT、final、修正、建议 SLO 和 Token/cost evidence |
| NEXT-006 | 1-3 小时稳定性恢复 | Partial / r13 packaged 60s backend-crash 连续音频、ASR 重连和录音续写门禁通过；完整 fault matrix、1h/3h 与人工专项未关闭 | 1h/3h、断网、睡眠、进程崩溃、磁盘失败和恢复报告 |
| NEXT-007 | 脱敏诊断包 | Partial / 脱敏 bundle 与阶段指标已实现；r3 packaged API 下载和内容审计通过，UI 专项待验收 | 一键诊断包无 secret/transcript/audio，包含阶段指标 |
| NEXT-008 | Provider/Keychain 稳定身份 | Partial / ad-hoc 有边界 | 固定签名身份升级、一次授权复用和失败提示 |
| NEXT-009 | Mac 公开发布工程 | Partial / 严格 inside-out 签名、Hardened Runtime/entitlements 验证、schema migration 和 public runner 接线已实现；Public release No-Go | Developer ID 实签、公证/stapling、Gatekeeper、再分发审批、干净 Mac 安装升级卸载 |
| NEXT-010 | Windows native client | Not started | WASAPI、Credential Manager、安装/升级/真机 gate；未实现不得宣称支持 |
| NEXT-011 | Web 定位与隐私架构 | Implemented / Web 明确为 loopback-only 共享 UI 开发、备用和复盘入口；HTTP/SSE/ASR WebSocket 非 loopback fail closed | local-only base tests、root contract、开发服务仅绑定 loopback |
| NEXT-012 | 数据治理和录音告知 | Partial / 分类删除、保留策略、审计和共享 UI 已实现；r3 packaged API 四类删除通过，UI 专项待验收 | 保留/删除/告知/导出/诊断策略和测试 |
| NEXT-013 | Provider 保存连接、真实 mic preflight | Partial | 保存并连接闭环、状态一致、RMS 电平和静音检测 |
| NEXT-014 | 自然段、全文和历史回看 | Partial / durable paragraph、完整全文和共享 UI 已实现；连续语音 packaged gate 待验收 | durable paragraph model/API、30-60s continuous speech、历史滚动锁定、无重复段落 |
| NEXT-015 | LLM-first 增量理解 | Partial / r6 packaged 真实 Provider API 主链已产生主题、状态增量和追问；真实 UI、重复可靠性和性能门禁未关闭 | batch debounce、structured state delta/evidence/idempotency、follow-up/UI，无关键词语义 fallback |
| NEXT-016 | 修正幂等和失败隔离 | Partial / reservation、逐 attempt 预算、validation 分类、一次结构修复、任务隔离和段落事实保真门禁已实现；r6 真实修正终态为 no_change，当前源码尚未打入新 candidate，变化 diff/UI/失败成本专项未关闭 | 重试不冲突、真实 diff 状态、post jobs 不级联阻断 |
| NEXT-017 | 可编辑会后文档和 user_final | Partial / 五类 user_final、版本与 r3 packaged API 验证通过；共享 UI/断网草稿专项待关闭 | 所有文档 kind 的编辑、自动保存、版本、断网草稿、AI 不覆盖用户稿 |
| NEXT-018 | 会议命名和历史管理 | Partial / 命名、搜索、游标分页和状态筛选已实现；packaged 长历史待验收 | 会前/会后命名、AI 建议锁定、搜索/分页/状态筛选 |
| NEXT-019 | 会后产物独立重试 | Partial / 三类独立 retry API/UI 与 r3 packaged API 保稿验证通过；失败 UI 专项待验收 | minutes/approach/index 独立 retry endpoint/UI，现有事实仍可见 |
| NEXT-020 | Markdown/DOCX/JSON 最终稿导出 | Partial / packaged API 三格式通过；shared UI 已实际点击并解析 Markdown/DOCX/JSON，但最终 packaged UI 与失败恢复仍待验收 | user_final 一致、DOCX 本地生成、packaged UI、导出错误/重试 |
| NEXT-021 | 录音导入面板和后台任务 | Partial / 面板与 durable job 已实现；NEXT-022 本机 packaged runtime 已内部 Go，导入 UI/失败恢复专项待验收 | 格式/大小/阶段/后台任务/失败恢复/历史入口 |
| NEXT-022 | packaged 文件 ASR runtime/models | Partial / r13 包内 runtime/models/converter ready，packaged authenticated API `37/37` 与 direct-backend WAV/M4A/MP3 真实模型通过；最终 packaged UI/Rust 文件选择、公开再分发、签名公证和 clean Mac 仍未关闭 | 干净 Mac、manifest 路径、模型能力状态、WAV/M4A/MP3 和 packaged UI import |
| NEXT-023 | 诊断“重新读取状态” | Partial / shared UI 已验证连续读取只访问本地 snapshot、不触发 Provider probe，并可导出脱敏诊断包；packaged UI gate 待验收 | 无普通同步词、packaged 诊断不重复调用/计费/上传 |

## 2026-07-18 当前实现批次记录

- NEXT-012：正式接入 `DataGovernanceService`；实现设置、删除 job、审计和四类删除 API，保留策略支持默认手动与 30/90/365 天；分类删除与 speaker 数据联动专项通过。
- NEXT-001：正式 Swift ScreenCaptureKit helper、Rust supervisor、动态 Tauri capability、runtime bundle manifest 与共享会前音源选择已接线；Web 不展示系统音频，系统音频权限失败不回退麦克风。
- NEXT-003：SQLite speaker mapping、稳定 `Speaker 1/2/3`、ASR final 字段透传、GET/PATCH API、会中/会后重命名、刷新保留、低置信度提示和三格式导出字段已完成。真实多人 diarization 质量不由这些工程测试代替。
- NEXT-005/NEXT-007：持久化有界 SLO、provider 首 token/完成阶段、重试/取消、Token/cost 与去标识化诊断包已接入正式 app；无样本返回 `no_data`，诊断不包含会议内容和 secret。
- NEXT-020：Markdown、DOCX、JSON 均使用最新 `user_final`；DOCX 在本地内存生成，JSON 保留原始/AI/用户版本、speaker、时间和证据；错误分类与文件名安全已固定。
- 无人值守可见浏览器主链已真实执行到页面：fake microphone 音频经 WebSocket、SQLite、durable jobs、显式 mock OpenAI-compatible gateway 后产生两段实时文字、一次 AI 修正、实时追问、minutes/approach/index、录音、完整文字和历史回开。最终 `passed_non_acceptance`，首 partial `834ms`、首 final `1050ms`、首建议/修正 `3179ms`，浏览器 runtime/console/network/HTTP 5xx 均为 0；报告只保留 meeting hash 和音频文件名。
- 首次运行暴露 CDP 截图命令无超时会留下持续录音会议；控制器已增加每命令 15 秒超时、截图 fallback、socket pending reject 和 finally 强制结束仍在录音会议，复跑后约 7 秒完整结束并清理 Chrome/backend/gateway。
- 当前代码批次已完成 backend full、frontend full、Rust、Mac system-audio 专项、fake scope contract 和无人值守浏览器主链回归。尚未重建的新 packaged candidate、真实 Screen Recording/TCC、自然多人中文、1h/3h soak、签名公证和 Windows 真机均未被这些结果关闭。
- 2026-07-19 独立回归修复了 Rust supervisor 测试夹具读取空 PID 文件的竞态：PID 先写临时文件再原子发布；失败用例连续 20 次通过，Rust 全量为 `47 passed, 1 ignored`，`cargo check --locked` 与 fmt check 通过。当前同一代码批次 backend full 为 `1120 passed`，root full 为 `466 passed, 1 skipped`，frontend 为 `18 files / 146 tests passed` 且 typecheck、lint、build 通过。

## 当前执行与验收门禁

以下复选框纳入当前总目标。能够在本机和当前代码范围内完成的必须实现并勾选；依赖外部资质、硬件、真实自然样本或异平台真机的，必须记录可复现阻断、已有替代证据、所需外部输入和解锁后的验收命令。

- [ ] 本地录音/ASR/SQLite/编辑/导出闭环，不接云同步和远程 ASR。
- [ ] LLM 是唯一必要远程文字 Provider；请求不含原始音频，Key 不进入日志/evidence/export。
- [ ] 固定 `/Applications/Meeting Copilot.app`、Bundle ID、Developer ID/Team ID 和数据目录。
- [ ] Web 只做共享 UI/契约开发和备用入口；packaged client 是最终发布真相。
- [ ] 普通自动化使用模拟音频/fake mic/fake LLM；真实权限只做专项 packaged gate。
- [ ] 不使用文件夹权限、SecurityAgent、Mac 密码提取或 TCC/Keychain 绕过。
- [ ] 无开发仓库、无 ModelScope 缓存、无全局 ffmpeg 的干净 Mac 能识别缺失组件并给出可操作提示。

## 每轮执行顺序

1. 先更新本清单状态和改动范围。
2. 先写 RED 测试或失败复现，再实现最小闭环。
3. 运行模块 focused tests，再运行 frontend/backend full tests。
4. 启动本地 Web 和 packaged client，跑按钮、模拟音频、fake LLM、导入、编辑、重试、导出和恢复链路。
5. 只在专项门禁需要时使用真实麦克风/Keychain；不要求用户持续守在电脑旁。
6. 生成脱敏 evidence、截图和日志摘要，更新决策日志和本清单。
7. 未关闭项必须列出阻断原因、复现命令、替代方案和下一步，不能标记为完成。

## 关联文档

- `docs/post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md`
- `docs/runtime-operating-constraints.md`
- `docs/decision-log.md#dec-426`
- `docs/decision-log.md#dec-428`
- `docs/decision-log.md#dec-429`
- `docs/decision-log.md#dec-430`
- `docs/decision-log.md#dec-431`

## 文档基线检查（已完成，不代表总目标完成）

- [x] 三份主文档均明确自己的职责：产品缺口与路线、运行约束、逐项验收清单。
- [x] NEXT-001 至 NEXT-023 均有唯一状态、验收出口和剩余边界；`Implemented`、`Partial`、`Blocked`、`Not started` 不混用。
- [x] 已实现代码、测试证据、模拟验证、packaged gate 和外部阻断被分开记录；mock/spike 不替代真实产品门禁。
- [x] Web/packaged 共享 UI 与平台 adapter 的关系、Mac/Windows 边界、远程 LLM 与本地 ASR 的成本边界已写清楚。
- [x] 文档明确“文档基线完成”与“全量产品目标完成”不同；当前任务继续按 NEXT 依赖顺序实现和验收，不再另行缩小立项范围。
- [x] 长会 soak、真实多人质量、Windows 真机和公开发布等无法由普通模拟关闭的条目，已要求保留真实门禁或严格外部阻断，不得跳过。

## 本轮文档一致性检查记录

检查时间：2026-07-18

检查命令和结果：

- `rg -o 'NEXT-[0-9]{3}'` 对路线讨论稿和全量清单分别去重，均得到完整的 `NEXT-001` 至 `NEXT-023`。
- 交叉引用检查通过：路线讨论稿、运行约束、全量清单和决策日志互相可定位；`DEC-431` 是当前范围决策，`DEC-428`、`DEC-429`、`DEC-430` 仅作为被覆盖的历史记录关联。
- 状态检查通过：清单条目只使用 `Implemented`、`Partial`、`Blocked`、`Not started` 四种基础状态；限定词只补充证据边界，不改变基础状态。
- 范围检查通过：文档明确当前目标是实现文档所列事项，且未把真实麦克风、系统音频、Windows、签名公证、自然多人中文质量或公开发布写成已完成。
- 本检查只验证文档一致性，不能关闭任何仍为 `Partial`、`Blocked` 或 `Not started` 的产品条目。

## 2026-07-19 NEXT-022 与产品 UI 证据补记

本节是晚于上方 2026-07-18 基线的最新证据补记，只更新所列门禁，不缩减 `NEXT-001` 至 `NEXT-023` 的总范围。

- `NEXT-022` 资源包直接 backend 工程证据：**Passed，但尚非 packaged app/Rust 内部 Go**。旧 smoke 用 app 内 runtime、离线 Paraformer、VAD、标点模型和转换器完成 WAV、M4A、MP3 三项导入并落库；但它直接启动资源包 backend，没有经过 Tauri/Rust supervisor，且三项“未使用 fake/远程/全局 FFmpeg”字段当时仍为静态声明。主证据仍保留为 `artifacts/tmp/next022-packaged-file-asr-smoke-20260719-r2/evidence.json`，只证明该受控资源包在本机执行过真实模型，不能单独关闭 NEXT-022。
- 本轮性能和体积观测：三个文件 ASR 模型冷启动约 `74s`，模型执行 RTF 约 `0.46`；app 逻辑体积 `4,498,694,100 bytes`，约 `4.50GB`。这些数值是当前本机工程 smoke 观测，不是自然多人质量、长期性能 SLO、clean Mac 或公开发布证明。
- `NEXT-022` 外部门禁：**Blocked / Public release No-Go**。受控 model pack 在当前内部 manifest 中按上游 Apache-2.0 标记为可打包，但模型版本仍来自 mutable `master`，发布级不可变来源和模型/依赖再分发审批尚未闭环；Developer ID 稳定签名、公证/stapling、Gatekeeper 和独立 clean Mac 安装运行也未通过。此前“packaged models absent”不再是本机内部工程阻断，但不能据此关闭这些外部门禁。
- `NEXT-009` 保持 **Partial / Public release No-Go**：严格 signer/public runner 已实现，但 Developer ID identity、公证/stapling、Gatekeeper、再分发审批和 clean Mac 仍未完成；不得把 NEXT-022 的内部 packaged smoke 写成 Mac 公开发布 Go。
- 共享前端已修复 `375px` 下“录音”tab 与导出按钮 overlap。in-app Browser 报告 `artifacts/evidence/v2-product-smoke-iab/report.json` 为 `verdict=go`，其中 `exportOverlapWidth=0`、录音 tab 全宽可见、无横向溢出、`browser_error_count=0`；frontend full 仍为 `18 files / 146 tests passed`。
- 对抗审计新增内部关闭条件：清除所有宿主 ASR 路径覆盖、从实际来源推导 no-fake/no-remote/no-global-FFmpeg、验证 sealed component hash、通过 Tauri/Rust supervisor 与完整 packaged 导入。完成并重建 r2 前，NEXT-022 保持 `Partial`；即使内部 Go，仍不蕴含公开发布 Go或全量路线完成。

## 2026-07-19 r3 packaged 主链证据补记

- 正式候选：`artifacts/tmp/tauri_runtime_package/full-roadmap-candidate-20260719-r3/Meeting Copilot.app`。package evidence 为 `go_internal_controlled_smoke_not_public_release`，App binary SHA-256 为 `b27ddb29f29af9b422ccdc0842e142b01750a78785f05f5b565e649929e13f83`；包内 file ASR runtime、offline/VAD/punctuation models 和 converter 均为 ready。ad-hoc 签名只用于本机工程 smoke，不计作稳定签名或公开发布。
- Rust 启动性能修复后，shared runtime 和大模型目录启动期只做 fail-closed metadata shape、required file 和 manifest mirror 校验；launcher、worker、converter 等小文件继续做 SHA-256。Rust full 为 `60 passed, 2 ignored`，真实 4.5GB runtime shape preflight 为 `6.22s`。
- 前两次 supervisor No-Go 证据保留在 `artifacts/tmp/packaged_runtime_supervisor_smoke/full-roadmap-candidate-20260719-r3-supervisor*/evidence.json`：旧同 Bundle ID 实例和 AppKit crash-history state restoration modal 使新实例未进入 setup。所有 packaged 自动化 runner 现统一使用 `-ApplePersistenceIgnoreState YES -NSQuitAlwaysKeepsWindows NO`，对应 RED/GREEN 合同为 `tests/test_packaged_app_launch_policy.py`；相关 focused 组为 `26 passed`。
- 修复后 Tauri/Rust supervisor gate：`artifacts/tmp/packaged_runtime_supervisor_smoke/full-roadmap-candidate-20260719-r3-supervisor-r3/evidence.json`，`18.604s`，真实 bundled FunASR 产生非空中文 final；App、backend、worker 和端口自然清理，未强制终止。
- hardened 三格式 gate：`artifacts/tmp/next022-hardened-direct-r3/evidence.json`，`79.97s`，package association、App binary 和 runtime manifest hash 均 verified；WAV/M4A/MP3 均由包内真实模型成功转写并持久化，`fake_asr_used=false`、`remote_asr_used=false`、`global_ffmpeg_used=false`。
- 全路线 packaged authenticated API gate：`artifacts/tmp/full_roadmap_packaged_acceptance/full-roadmap-candidate-20260719-r3-acceptance/report.json`，`37/37` required checks 通过，耗时 `17.304s`。它覆盖本地 ASR、AI 修正/追问、复盘三产物、独立重试、五类 user_final、三格式导出、历史回开、录音 Range、诊断脱敏和四类删除；明确使用本地 fake OpenAI-compatible gateway，因此不计作真实 LLM 或 UI 证据。
- packaged fake-AI 主链：`artifacts/tmp/packaged_ai_mainline_smoke/full-roadmap-candidate-20260719-r3-ai/evidence.json`，`20.813s`，业务编排和自然进程清理通过；不计作真实 relay、真实多人质量、UI、TCC 或公开发布证据。
- 结论：NEXT-022 达到本机 **Internal packaged engineering Go**，但总条目仍为 `Partial`。mutable `master` 模型来源、FFmpeg/模型公开再分发审计、Developer ID、Hardened Runtime、公证/stapling、Gatekeeper 和独立 clean Mac 仍为真实外部门禁；NEXT-009 继续 Public release No-Go。

## 2026-07-19 r6 真实 relay 与当前候选补记

本节是当前最新代码和 candidate 证据，覆盖上方涉及 r3/r5 的时间快照，但不缩减 `NEXT-001` 至 `NEXT-023`。

- r5 真实 relay 保留为明确 No-Go：`artifacts/tmp/packaged_real_provider_mainline_smoke/full-roadmap-candidate-20260719-r5-real-provider-architecture/evidence.json`。真实会后 minutes/approach 调用成功，但 realtime intelligence 因结构校验失败没有产生修正终态、增量状态、追问或 provider trace，不能计作真实实时主链。
- 根因修复进入正式代码：实时 intelligence 输出合同补齐字段类型、枚举、null 和逐字 evidence 约束；动态输出预算由最低 `256` 提高到 `768`、最高 `4096`；首次严格解析失败时只允许一次带独立幂等键的结构修复，第二次仍失败则保留原文并明确失败。focused realtime/app 回归为 `52 passed, 2 warnings`。
- r6 candidate：`artifacts/tmp/tauri_runtime_package/full-roadmap-candidate-20260719-r6/Meeting Copilot.app`，App binary SHA-256 为 `1b6b16ad6fffd7a3771d9c49aed4397cfe23bc2fda9dd36f63f1f05922cb8ef3`，逻辑体积 `4,498,939,548 bytes`。包内 realtime/file ASR runtime、offline/VAD/punctuation models 和 converter 均 ready，WAV/M4A/MP3 均 package-ready；ad-hoc 签名和 unresolved redistribution 仍不是公开发布资格。
- r6 真实 relay 主链：`artifacts/tmp/packaged_real_provider_mainline_smoke/full-roadmap-candidate-20260719-r6-real-provider-architecture/evidence.json`，`status=go_packaged_real_remote_llm_mainline_not_ui_not_public_release`。真实 bundled FunASR 产生 1 个 final，真实 `responses` Provider 产生主题、决定、待办、开放问题、追问和 `meeting.intelligence.applied`，修正达到 `no_change` 终态，会后产物完成；共 3 次调用、`3125` tokens，App/backend/FunASR/端口全部自然清理。
- r6 实时性能仍为 No-Go：本次 provider TTFT `19,563.405ms`、provider total `23,459.874ms`，明显高于当前实时 SLO。该结果关闭“真实协议能否贯通”的疑问，但不能关闭 `NEXT-005`；应继续通过更快模型、能力感知结构化输出、短上下文和异步低打扰展示优化。
- r6 supervisor：`artifacts/tmp/packaged_runtime_supervisor_smoke/full-roadmap-candidate-20260719-r6-supervisor/evidence.json`，`24.722s`，bundled backend/FunASR 非空中文 final 和自然清理通过；不包含真实麦克风、Screen Recording/TCC 或 UI 操作。
- r6 受控 packaged authenticated API acceptance：`artifacts/tmp/full_roadmap_packaged_acceptance/full-roadmap-candidate-20260719-r6-acceptance/report.json`，`37/37` 通过，`26.352s`；它使用明确 fake OpenAI-compatible gateway，只证明 API/编排、user_final、重试、导出、历史、诊断和删除，不计作真实 LLM 或 UI 证据。
- r6 packaged fake-AI mainline：`artifacts/tmp/packaged_ai_mainline_smoke/full-roadmap-candidate-20260719-r6-ai/evidence.json`，`25.695s`，用于确定性回归，不计作真实 Provider、真实 TCC、自然多人或公开发布证据。
- r6 文件 ASR direct-backend gate：`artifacts/tmp/next022-hardened-direct-r6/evidence.json`，`81.818s`。同一 r6 App binary/package evidence 下，WAV/M4A/MP3 均使用包内离线模型成功转写并持久化，`fake_asr_used=false`、`remote_asr_used=false`、`global_ffmpeg_used=false`；claim scope 明确为 `app_resource_bundle + direct_backend_api`，`rust_supervisor=false`、`tauri_supervisor=false`，因此不替代 UI/Rust 同进程导入或公开发布门禁。
- 受控三格式 fixture 升级为 `modelscope-asr-example-three-formats-20260719-v2`：WAV 沿用本机上游模型示例并精确匹配既有 SHA-256；M4A/MP3 由 r6 包内 FFmpeg 确定性转换，两次转换哈希一致。未下载新音频；该样本只证明真实 worker/plumbing，不计作中文会议质量 benchmark。
- r6 后当前源码可靠性补强尚未重新打包：每个 Provider attempt 返回 usage 后立即进入 ledger，repair 前重新执行预算门禁；validation error 区分 `structural`、`evidence`、`stale` 和 `semantic_safety`，只有结构/截断允许一次 repair。相关 focused 为 `54 passed, 2 warnings`，backend full 为 `1139 passed, 1 warning`。浏览器真实麦克风 acceptance 测试的后台竞态也已修正为同时等待录音组装和 `evaluation_summary`，连续 10 次通过。上述源码增量不得倒算为 r6 packaged evidence，进入下一候选时必须重跑 packaged gates。
- 总目标继续为 `Active`。真实 packaged UI、双轨 TCC、自然多人中文、1h/3h soak、稳定 Keychain 身份、Developer ID/公证/clean Mac、Windows 真机和其他清单未关闭项仍必须继续执行或形成严格外部阻断。

## 2026-07-19 r13 后端崩溃恢复证据补记

本节只推进 `NEXT-006` 的一个自动化子门禁，不改变 `NEXT-001` 至 `NEXT-023` 的完整目标，也不把短时故障测试写成总目标完成。

- r12 失败证据：`artifacts/tmp/next006-real-sut/next006-r12-continuous-backend-recovery/report.json`。backend supervisor 拉起新进程，但恢复后的 writer 因音频清单起始时间被后台 export 以 SQLite 毫秒值重写，重放既有 chunk 时产生 `ValueError`；setup failure 又未释放新 capture lease，形成约 `300ms` 一次的 `recording_resume_failed` 重试风暴。该次只有 `11.745s/60s` 音频送达，backend recovery gate 失败。
- 修复边界：已有 `audio.manifest.json` 的 `started_at_ms` 成为恢复/导出的时间线事实源；writer setup 与后台 export 共用每会议资产锁；setup 失败按 `lease_owner + capture_generation` CAS 精确中断并释放本次租约，旧回调不能误伤新 generation；ASR 运行日志只增加 `module.function` 级脱敏错误来源，不记录异常文本、路径、文字稿、prompt 或 secret。
- 源码验证：锁定 Python 3.13/`uv.lock` 环境 backend full 为 `1153 passed, 1 warning`；新增测试覆盖 1ms 时间线偏差、路由真实回调接线、失败租约释放、立即 retry 和 stale abort fencing。Python 3.14 系统解释器缺少项目依赖的 `imageio-ffmpeg`，不再作为正式测试环境。
- r13 candidate：`artifacts/tmp/tauri_runtime_package/full-roadmap-candidate-20260719-r13/Meeting Copilot.app`；App binary SHA-256 为 `49105d37e9b340f36958413295ecdb724a1f07f9fb9ee5c3c8b03a76e9feeaf3`。package evidence 为 `go_internal_controlled_smoke_not_public_release`，WAV/M4A/MP3 文件 ASR 组件 ready；ad-hoc 签名、公开再分发、Developer ID、公证和 clean Mac 仍未关闭。
- r13 packaged 恢复证据：`artifacts/tmp/next006-real-sut/next006-r13-continuous-backend-recovery/report.json`。backend crash 在 `5.934s` 内恢复；出现新的 post-fault `asr_ready`，post-fault 音频继续增长；总送音 `53.95s/60s`，最终录音 `53.104s`、`11` chunks、`1,699,378 bytes`，状态 `ready/assembled`；meeting end 返回 200，SQLite `quick_check=ok`，App/backend/ASR worker 全部退出。日志中没有 `recording_setup_failed`、setup rollback failure 或 `asr.stream.aborted`。
- r13 仍不是 NEXT-006 完成证据：本次未配置 Provider，3 个 AI job 保持 `retry_wait`，因此 queue/sqlite automated aggregate 按合同未通过；provider disconnect/429/5xx、disk-write、ASR worker crash、App crash、1h/3h、sleep/wake 和真实麦克风设备切换仍须分别执行。总清单与 `NEXT-006` 均保持 `Partial`。

## 2026-07-19 r13 共享 UI 主链证据补记

- UI RED：第一次 fake-audio/fake-LLM 页面主链在实时文字、修正和追问后，录音 tab 等待旧的 `/audio/content` URL，实际双轨兼容实现已经请求 `/audio/tracks/microphone/content?epoch=0` 并收到 `206`；第二次在正确 Provider base URL 下复现了这个 E2E 合同过时问题。两次均保留 error evidence，不把失败藏掉。
- UI 修复：新增 `code/web_mvp/e2e/meeting_audio_url_contract.mjs`，统一接受会议整体、麦克风轨、系统音频轨和混合轨的本地 `/v2/meetings/.../audio/.../content` URL，拒绝跨域、未知轨道、非 content 路径；product smoke 与 real mainline 的等待、断言和媒体取消 allowlist 已统一使用该合同。
- r13 UI GREEN：`artifacts/tmp/browser_live_mic/r13-full-ui-mainline-r3/report.json`，截图目录同级。可见 Chrome 实际点击会前 preflight/开始会议/结束会议和 tab，fake browser audio -> shared frontend -> WebSocket -> scripted ASR -> fake OpenAI-compatible LLM -> correction/follow-up -> review/history 全链通过；首文字 `515ms`、首 final `1,024ms`、首建议/修正 `2,955ms`，两段文字、1 个真实 revision、录音 `5,096ms`/2 chunks、三类 review jobs、历史重开均通过；runtime exception、console error、network failure、HTTP 5xx 为 0。
- 该报告明确 `passed_non_acceptance`、`acceptance_eligible=false`、fake ASR/fake LLM，不替代真实麦克风、真实 FunASR、真实 relay、TCC、自然多人中文或公开发布。它为 `NEXT-013..021/023` 提供共享 UI 主链证据，但各条仍按各自真实门禁保持 `Partial`。

## 2026-07-19 `8767` 工作树与 UI 合并核查补记（DEC-445）

- [x] 已确认 `8767` 由旧工作树 `/Users/chase/Documents/面试/meeting-copilot/code/web_mvp/backend` 的进程提供，不是 `/Users/chase/Documents/面试/meeting-copilot-phase0-clean`。
- [x] 已确认旧 `frontend_v2` 的视觉改版不能整目录覆盖 clean 主线；旧目录约 36 个源文件，clean 主线约 60 个源文件，直接覆盖会删除本地 API base、Provider、会前检查、导入、原生音频、speaker revision、会后编辑和导出等功能。
- [x] 将旧 UI 的品牌资源、导航视觉结构、窄侧栏布局、motion-aware scrolling 和可复用 CSS token 增量移植到 clean 主线；该移植已通过 frontend `24` 个测试文件、`197/197`、typecheck、lint 和 production build。
- [ ] 在新端口完成 clean 主线的 `1440x900`、`1280x800`、`390x844` 可见浏览器对照；clean backend `8788` 与 clean Vite `5188` 已启动并完成空状态 DOM 核查，但完整三视口证据和最终 candidate/package UI gate 仍待补齐。
- [ ] 将 clean UI 移植后的 focused/full frontend、共享 Web E2E 和 packaged client UI 证据分别写回对应 `NEXT` 条目；旧 `8767` 页面及其 mock/历史数据不得作为验收证据。

本节只记录工作树和 UI 合并门禁，不缩减 `NEXT-001` 至 `NEXT-023`，也不把旧 `8767` 的可见状态解释为 clean 主线或客户端已完成。

## 2026-07-19 Workbench runtime identity 补记（DEC-450）

- [x] 启动器已从“端口可访问/`/health=ok`”升级为版本化 runtime identity 验证：health、application schema、共享前端资产 hash、受管 PID、process start marker、端口绑定和当前源码 fingerprint 必须一致。
- [x] runtime identity 文件权限固定为 `0600`；证据不包含 Provider secret、Authorization、Keychain password、文字稿、音频或完整环境变量。
- [x] focused tests `12/12` 通过；临时端口 `8791` 的 start/status/stop 真实集成通过，进程自然回收。
- [x] 旧/外来进程、缺失 managed launch record、伪装 health 响应和 foreign PID 均被拒绝；停止动作不会自动终止外来进程。
- [ ] 该 gate 仍未关闭真实麦克风、系统音频/TCC、真实 Provider、自然多人中文、packaged UI、签名公证、Windows 或公开发布门禁。

本补记的准确范围是“当前 clean workbench runtime 身份可验证”，不是“完整产品主链或公开发布已完成”。

## 2026-07-19 多 Agent 中断恢复补记（DEC-446）

- [x] 并行 Agent 使用唯一主线绝对路径、互斥文件所有权、明确测试命令和证据边界；主线程不因 Agent 最终摘要缺失而宣称完成。
- [x] 遇到 `502`、`not_found`、中断或缺失总结时，以共享工作树中已落盘的代码、测试和 evidence 为准，先检查归属文件与修改时间，再独立复跑 focused RED/GREEN。
- [x] 独立复测通过后才接纳 Agent 成果；跨模块改动仍必须进入相应 backend/frontend/root/Rust/package 回归。缺失摘要不等于代码丢失，也不等于实现完成。
- [x] 已保留所有现有用户/其他 Agent 修改；本轮文档审计不回滚、不覆盖代码，不记录或泄露任何凭据。

## 2026-07-19 macOS r15-r5 内部签名包补记（DEC-451）

- [x] 主可执行文件已从独立 signing step 改为 `main-app` principal 的附属验证对象；实际主 app 与主可执行文件的 entitlement 一致性已覆盖。
- [x] `native-mic` 继续只绑定固定路径和最小 `audio-input` entitlement；其他 nested Mach-O 继续零 entitlement；签名无 `--deep`。
- [x] strict signer focused `22/22`、`ruff`、r15-r5 Tauri build 和 334 个 Mach-O strict signing/verification 已通过。
- [x] r15-r5 资源封装、sealed runtime manifest、inside-out 签名和逐目严格验证已通过；这只关闭签名合同子门禁，不代表 runtime 可运行。
- [ ] r15-r6 packaged supervisor 仍为 No-Go：当前机器没有可用 codesigning identity；ad-hoc Hardened Runtime 下 Python 原生扩展因无共同 Team ID 被 library validation 拒绝。详见 `DEC-452` 和 `artifacts/tmp/packaged_runtime_supervisor_smoke/full-roadmap-candidate-20260719-r15-r6-mainline/evidence.json`。
- [ ] packaged API/UI 主链、真实麦克风/TCC、system audio、真实 Provider、自然多人中文、clean Mac、Windows、Developer ID/notary/Gatekeeper 和模型/FFmpeg 再分发仍未关闭。

本节只关闭当前内部 ad-hoc 包签名与资源准备子门禁，不把候选包解释为产品完成或公开发布包。

## 2026-07-19 r15-r6 packaged runtime blocker（DEC-452）

- [x] 已复核 `.app`、bundled Python 和 `pydantic_core` 的实际签名元数据；均为 `Signature=adhoc`、`TeamIdentifier=not set`，不是单个扩展漏签。
- [x] 已复核当前机器 `security find-identity -v -p codesigning` 为 `0 valid identities found`。
- [x] 已保存 supervisor No-Go evidence 和脱敏首个 backend import 错误；失败发生在 FastAPI/Pydantic 导入阶段，尚未进入 ASR、Provider 或业务 API。
- [ ] 安装可用 Apple/Developer ID identity 后，以共同 Team ID 重签并在 Hardened Runtime 下复跑 packaged supervisor；未满足前不得把 r15-r6 当作客户端交付包。
- [x] 明确禁止用 `disable-library-validation`、关闭 Hardened Runtime、`--deep` 或伪造 Team ID 规避该阻断。

## 2026-07-19 clean Workbench 真实麦克风补记（DEC-454）

- [x] 旧 `8792` 页面曾返回 HTTP 200，但 runtime identity 为 `managed_pid_missing`，不能作为 clean 主线证据；未自动终止外来进程。
- [x] 新 clean runtime 使用 `8793`、受管 PID、独立数据目录和 `provider_mode=safe` 启动；health、application schema、asset provenance、源码 fingerprint、PID/端口/start marker 全部 verified。
- [x] 浏览器真实麦克风 preflight 通过，系统复用固定来源授权；一次检测返回 RMS `0.3%`，状态为“正常收到声音，麦克风可用”。
- [x] clean 真实麦克风录音即使没有形成 final 文字也安全收口：`60.8s`、`13` chunks、`1,945,710` bytes、`assembled=true`、track=`microphone`。证据截图和状态在 `artifacts/evidence/real-mic-workbench-20260719-clean-8793/`。
- [x] 上一场真实麦克风短流程曾产生 `1` 个 final，但中文识别出现明显串词、断句和语义错误；该事实保留在 `artifacts/evidence/real-mic-workbench-20260719/01-live-real-mic.png`，因此自然中文质量仍是 No-Go，不能把录音成功写成 ASR 质量通过。
- [x] 真实无 AI 配置的会后页面原先把 `ProviderRuntimeNotConfiguredDeferred` 显示为“正在重试”；共享 UI 已改为“等待配置 AI”，明确文字/录音已保存和配置后的继续路径；没有 final 文字时也不再假报“正在生成”。新增回归分别覆盖两种状态，frontend full `199 passed`。
- [ ] 真实 Provider 配置、录音期间 AI 修正/建议、自然多人质量和 packaged microphone/TCC 仍未关闭；本节不替代这些门禁。

## 2026-07-19 当前源码模拟浏览器主链补记（DEC-453）

- [x] 使用隔离的 `8782` scripted Chinese ASR、`18776` 本地 fake OpenAI-compatible gateway 和可见 Chrome，完成开始会议、preflight、实时 partial/final、实时建议、实时修正、结束会议、minutes/approach/index、录音读取、全文回看和历史重开。
- [x] 证据：`artifacts/tmp/browser_live_mic/r15-mainline-20260719-rerun/report.json`；截图、snapshot、events、traces 和 recording samples 同目录保存。
- [x] 结果为 `passed_non_acceptance`，首文字 `1623ms`、首 final `2026ms`、首建议/修正 `4052ms`；2 段 transcript、1 次 revision、review jobs 全部 succeeded、录音 HTTP `200`、浏览器 console/network/5xx 均为 0。
- [x] ASR 和 LLM 均明确 `is_mock=true`，gateway 为 local；该结果不计作真实麦克风、真实 ASR、真实 Provider、packaged client 或公开发布证据。
- [x] fake backend 启动器现在强制 `LLM_GATEWAY_IS_MOCK=true`，防止本地 fake gateway 在证据中被误标为真实 Provider；root `617 passed, 1 skipped, 1 warning`，focused fake/packaged contract `19 passed`。

## 2026-07-19 owner-only 本地存储与最新模拟主链补记（DEC-455）

- [x] 审计发现 packaged app-data、runtime-data、SQLite、会议准备、历史录音和 native capture ready/log 曾以 `0755/0644` 创建；该状态不符合本地优先的数据治理边界，不能留给公开发布阶段再处理。
- [x] Python shared storage 已统一私有目录 `0700`、私有文件 `0600`，并对历史 data tree 执行一次版本化权限迁移；SQLite 主文件及 WAL/SHM/journal、会议准备 JSON、实时录音 chunk/manifest/WAV、导入文件和 legacy JSON 写入均进入同一合同。
- [x] Tauri/Rust shared private storage 已接入 bundled backend data/log、native microphone 和 system audio 的 runtime 目录、ready 状态和日志；状态文件无法收紧时 fail closed，不继续报告采集成功。
- [x] 本机 packaged app-data 实际迁移共收紧 `115` 个目录、`757` 个文件；抽查私有目录均为 `0700`、私有文件均为 `0600`，未读取或记录用户音频、文字稿、API Key 或 Authorization。
- [x] backend full `1222 passed, 1 warning`；storage focused `135 passed`；Rust full `71 passed, 2 ignored`；根级 packaged/audio contract `43 passed`；根级完整回归 `617 passed, 1 skipped, 1 warning`；Ruff、compileall 和 `git diff --check` 通过。
- [x] 当前源码已在受管 `8794` 启动，runtime identity 的 loopback、health、schema、共享 UI asset hash、PID、port、process start marker 和源码 fingerprint 全部 verified；浏览器首屏按钮可用、无横向溢出、error/warning 为 0。旧 `8792/8793` 未被自动终止或冒充当前候选。
- [x] 最新 non-acceptance 浏览器主链为 `artifacts/tmp/browser_live_mic/r15-mainline-final-20260719-rerun2/report.json`：首文字 `511ms`、首 final `713ms`、首建议/修正 `2952ms`，实时文字、修正、建议、录音、复盘和历史重开全部通过，浏览器错误为 0。
- [ ] 该模拟报告仍使用 scripted ASR 和 fake LLM，不能关闭真实中文 ASR、真实 relay、packaged client 或发布门禁；POSIX mode 也不能替代 Windows ACL 真机证据。`NEXT-012`、`NEXT-008/009/010` 和完整路线继续保持原有 Partial/No-Go/Not started 边界。

## 2026-07-20 native PCM v2、关闭竞态与真实显示阻断补记

本节是当前工作树的最新回归和门禁事实，不覆盖三份文档中仍未完成的 NEXT 项。

- [x] native PCM v2 已接入 helper -> Rust/Tauri loopback -> backend decoder -> ASR event -> V2 recording writer -> SQLite `audio_chunks`；SQLite V3 migration `add_native_pcm_source_ranges` 持久化 native sequence/timestamp 范围，并对 partial/reversed/conflicting range fail closed。
- [x] microphone 与 system-audio ready 文件均要求 authenticated loopback transport、完整 PCM frame 和成功 binary send；旧协议、错 epoch、未传输 PCM、字节不足和身份不匹配均拒绝。focused native/system/audio-gate `25 passed`，Swift helpers 已分别编译。
- [x] backend sidecar kill 幂等、正常/错误 WebSocket 先收尾再 close、测试级 degradation reset 已实现；sidecar/diarization focused `30 passed`，backend 正式 `code/web_mvp/backend/.venv` Python 3.13 full `1247 passed, 1 skipped, 1 warning`。
- [x] desktop Rust full `73 passed, 2 ignored`；frontend full `210 passed`，typecheck、lint、production build 通过。Rust target 使用外部 `artifacts/tmp/cargo-target`。
- [x] 当前源码 helper 的无请求权限真实预检已执行：`CGPreflightScreenCaptureAccess=true`，但 `CGGetActiveDisplayList=0`，helper 返回 `content_unavailable`，未伪造 PCM 或 ready evidence。
- [ ] `NEXT-001/002` 的真实 packaged 三层门禁仍未关闭。机器恢复 active display 后必须重建当前 candidate，并提交同一 binary/helper hash 绑定的 helper、Tauri/backend/ASR/recording 和 shared UI evidence；旧 r15-r6 包不能代表当前源码。
- [ ] 本轮尚未关闭自然多人中文质量、真实 Provider 当前 candidate SLO、完整 fault matrix/1h/3h、Developer ID/公证/clean Mac、Windows native client 或完整 packaged UI 证据。总目标保持 `Active`。
