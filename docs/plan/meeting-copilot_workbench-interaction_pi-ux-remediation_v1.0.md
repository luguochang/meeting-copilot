# Meeting Copilot 会中工作台交互与 Pi 教练体验整改方案 v1.0

> 状态：执行中，作为 UI/交互实施与验收的唯一主清单
> 日期：2026-09-30
> 目标分支：`feat/pi-realtime-coach-agent-loop`
> 适用范围：网页版和 Tauri 共用的 `frontend_v2`，以及为交互闭环所需的 Pi/持久化接口
> 不在本轮范围：修改 ASR 模型、VAD、切段、本地精修算法和远端 transcript correction 算法

### 当前执行记录（更新于 2026-09-30）

已经落地并有自动化覆盖：

- 采集 controller 提升到应用层；会中进入笔记、离线能力或历史会议不会因页面卸载而停止采集。
- 左侧增加真实活动/暂停/重连/待恢复会议入口；会议列表不再把所有后端 `phase=live` 都写成“正在录音”。
- 会前检查、AI 设置、录音导入、运行诊断、删除会议数据和保留策略统一 `Escape`、scrim、焦点圈定和焦点恢复合同。
- 录音导入提交中、删除中、设置保存中禁止误关闭；AI 设置和保留策略存在未保存修改时必须显式放弃。
- Answer 与 Pi 以 `answer_id` 组成线程；Pi 支持结构化深度补充、证据、revision 和用户纠偏，失败/静默不能覆盖成功内容。
- 小屏使用“会议文字 / 实时教练”切换；增加 skip links、44px 图标命中区和 reduced-motion 降级。
- Provider 保存/测试分离，移除配置主流程中的赞助商和外部推广链接。
- 会后失败原因不再在多个内容区重复为红色告警；危险删除操作移入可键盘关闭的会议行菜单。
- 会议列表的搜索、筛选、排序和滚动位置写入当前浏览器会话；进入详情后通过页面返回或浏览器返回均可恢复。
- 定义 `MeetingSessionState` 和状态优先级；后端仍为 live 但浏览器已无 MediaStream 时明确进入 `recoverable`，只显示“恢复录音”和“直接结束并整理”。
- 会中头部 7 项技术状态收敛为一个“采集健康”摘要，点击进入诊断；主界面不再平铺录音、ASR、精修、说话人、LLM 和任务内部状态。
- 从 Pi/会议事实跳到左侧证据后显示“返回实时教练”，原 Answer 线程、Pi revision 和右栏阅读位置保持挂载。
- 刷新后识别出的 `recoverable` 会议会提升为应用级“会议待恢复”入口；进入笔记或离线能力后仍可一键返回，不再依赖会议页组件是否仍挂载。
- 小屏“会议文字 / 实时教练”切换写入浏览器历史；浏览器返回恢复上一个会中视图，不再直接离开会议。
- AI 工作区 Tab、Ask AI 线程、所选 Answer 和 Pi revision 按 meeting ID 写入浏览器会话状态；离开页面再返回时恢复阅读上下文。
- 头部删除与“采集健康”重复的独立诊断图标；诊断统一从采集健康摘要进入。
- Pi 反馈原因、普通建议反馈、会后导出和文字选择工具栏补齐 Escape、点击外部关闭及焦点恢复合同。

仍未达到最终 DoD：

- 真实 Provider 五分钟多问题回放和两次绑定 Answer 的 `user_request` 回放已经执行但性能验收失败；自动化已证明 revision/旧版保留，真实 Provider 尚未产生成功 revision。
- 补充交互审计中的应用级活动会议发现、首页防重复创建、移动端主导航、Answer/Pi 首屏优先和稳定空态已经关闭；这只代表交互整改通过，不代表 Provider/Pi 质量验收通过。
- 全尺寸交互已完成浏览器验收，最终桌面/中等宽度/移动端截图已落盘并在本文登记。
- 未完成项继续保留为未勾选，不能用自动化通过数量替代真实会议验收。

### 本轮验证证据（2026-09-29）

- 独立验收服务：`http://127.0.0.1:8991/workbench`。
- 合成会议：`ui_review_synthetic_20260929`，仅用于 UI 状态和交互验收，不含录音和真实会议数据。
- 前端全量：34 个测试文件、361 个测试全部通过（补充交互整改最终基线）。
- 前端 TypeScript typecheck、ESLint、生产构建和 `git diff --check` 全部通过。
- 后端 Pi/coach/answer-ready/user-request 重点回归：142 通过；Pi sidecar：50 通过。
- ASR runtime 当前工作树回归：127 通过；FunASR sidecar、resident、correction API、correction worker 和 V2 持久化聚焦回归：230 通过。
- 1440x1000：双栏为约 652/556px，页面宽度 1440px，无横向溢出。
- 1280x800：双栏为约 570/478px，无控件越界。
- 980x900：切换为“会议文字 / 实时教练”单视图，无横向溢出。
- 390x844、375x667：单视图，恢复录音和直接结束按钮均为 44px 高，无横向溢出。
- 最终截图：`artifacts/ui-review/2026-09-29-interaction-remediation/workbench-1440x1000.png`。
- 最终截图：`artifacts/ui-review/2026-09-29-interaction-remediation/workbench-1280x800.png`。
- 最终截图：`artifacts/ui-review/2026-09-29-interaction-remediation/workbench-980x900.png`。
- 最终截图：`artifacts/ui-review/2026-09-29-interaction-remediation/workbench-390x844.png`。
- 最终截图：`artifacts/ui-review/2026-09-29-interaction-remediation/workbench-375x667.png`。
- 五个视口均已验证按钮重叠数为 0；390px 和 375px 视口的页面 `scrollWidth` 分别等于 390px 和 375px。
- 明/暗侧栏均完成 1440px 视觉检查；现有产品只支持侧栏主题切换，不把主工作区误写成已支持全局暗色模式。
- Provider 设置真实 UI 验证：测试和保存分离；配置修改后测试禁用；关闭时出现“继续编辑 / 放弃修改”；焦点恢复到设置触发按钮。
- 会议行菜单真实 UI 验证：点击外部/Escape 关闭，Escape 后焦点回到原行菜单按钮；未执行任何删除操作。
- 浏览器 console：0 条 error/warn。
- 服务健康检查使用 `/health`，`8991` 当前返回 `status=ok`；`/healthz` 不属于本项目路由。
- capture freshness 已复用录音 lease 落地统一服务端摘要：`active / recoverable / inactive`、活动轨道数、最近 heartbeat 和 lease 截止时间；过期 lease 不再被快照或列表误报为正在录音。
- 真实 Provider 回放会议：`accept_ui_provider_loop_20260929_01`；证据目录：`artifacts/tmp/ui-remediation-real-provider-20260929-01`。
- 真实 Provider 路径确实经过 WebSocket、FunASR、Fast Answer、Pi sidecar、harness/loop、持久化和会后整理；UI 投影仅 1-3ms，因此右栏慢的主因不是 React 渲染。
- 该回放性能验收失败：Fast Answer TTFT 1912/2264ms，完成 6353/12905ms；Pi Provider 两次约 9.3s 后超时，本地 reflex 约 7.8/8.0s，Pi E2E P50 8008ms、P95/最大值 22250ms，Provider circuit 最终为 open。
- 上述失败说明当前 `gpt-5.5` 网关不满足 Pi P95 8 秒门槛；不得通过增大 timeout 或把本地 fallback 当成 Provider 成功来勾选 DoD。
- 补充交互浏览器证据目录：`artifacts/ui-review/2026-09-29-interaction-remediation-round2`。
- 补充截图：`home-1440x1000.png`、`live-980x900.png`、`home-390x844.png`、`live-coach-375x667.png`、`home-340x720.png`。
- 390px、375px 和 340px 视口均保留固定底部主导航；按钮高度均为 56px，无横向溢出，滚动到底后最后一项内容不被导航遮挡。
- 真实浏览器已验证“会议记录 -> 笔记 -> 会议记录”和“会议文字 -> 实时教练 -> 浏览器返回 -> 会议文字”的可逆路径，返回按钮可见，console 为 0 error / 0 warn。
- `answer-pi-1440x1000.png` 和 `answer-pi-375x667.png` 使用真实 Provider 回放会议的已保存数据做会中 UI 投影，只证明 Fast Answer/Pi 线程布局和内容顺序；该轮 Pi Provider 超时、没有成功补充，不能作为 Pi Provider 成功证据。
- ASR 固定音频 SHA-256：`d06ebabb8f9a41b404fc2b0ba9e4aba369860228a1128bb660d04271bfc2ca16`。同一段 16kHz/mono/PCM16 WAV 已在 `origin/main@5cd0ed5` 与当前 Pi 分支、相同本地 refiner 配置下比对，canonical transcript 逐字一致；本轮交互整改未修改 ASR runtime、FunASR、VAD、切段或 transcript correction 实现。

### Provider 传输整改与复验（2026-09-30）

- 根因修复：绑定旧 Answer 的 `user_request` 现在按 `input_transcript_seq` 截止上下文，后续问题不会再挤掉原 Answer 证据；自动化覆盖 Fast Answer、自动 Pi、用户纠偏、revision 1 -> 2 和 `supersedes_decision_id`。
- SDK 事实：`@earendil-works/pi-ai@0.84.2` 的 `completeSimple()` 仍委托 `streamSimple()`，底层 Responses 固定发送 `stream: true`，不是非流式 Provider API。
- 已新增仅用于 `deep_answer` 的 Responses 非流式适配器；Pi `Agent`、session、tool execution、terminal action 和 loop 保持不变，10 秒 realtime candidate lane 继续使用原 SDK streaming。
- Provider 端合同压缩为 `headline + say_this_addition + 两个 key_points + confidence`；两个 key point 固定表示“遗漏判断”和“下一步”，host 再展开为稳定 `coaching_package`、绑定证据和 legacy 字段，避免让模型重复生成同义内容。
- Deep Agent 请求由约 5450 字符降到 2348 字符，工具 schema 由约 971 字符降到 826 字符；输出上限 280 token；自定义网关请求不再携带无必要的 `session_id` / `x-client-request-id` affinity 头。
- Pi bridge：54 项全部通过；后端全量：1802 项通过、1 项跳过；前端：34 个文件、361 项通过；TypeScript、ESLint、生产构建和 `git diff --check` 通过。
- 第一次真实非流式回放：`job_cfc0c8ec006e917b19552e6a`，约 19.68 秒后 `pi_timeout`，无 revision，旧成功内容未被覆盖。
- 压缩合同后的最后一次真实回放：`job_0e348dd7ed517344c68eca36`，20.008 秒命中 `agent_deadline_exceeded`，仍无 terminal tool result；会议已恢复为 `ended`，原 `ended_at_ms=1790693124659`。
- 结论：当前阻塞已不再是 React、Answer 证据绑定、流式 SSE、session affinity 或明显的 schema/Prompt 膨胀；`codexai.club + gpt-5.5` 对完整深度工具调用的服务端完成时延仍不满足 8 秒产品门槛。不得再增加 timeout、重复消耗测试额度或把 timeout/fallback 记为 Pi 成功。
- 上述 20 秒仅为根因定位期间最后一次受控实验，不是交付配置；代码中的 deep job 和 Provider hard deadline 已恢复为 8 秒，Pi bridge 总上限为 10 秒。超时只记录失败并保留上一成功版本，不能让右栏继续等待 20 秒。该调整未再次调用真实 Provider。
- 受控备份：`artifacts/tmp/ui-remediation-real-provider-20260929-03-user-revision/pre-non-streaming-responses-retry.sqlite3` 和 `pre-compact-non-streaming-retry.sqlite3`；均为本地验收数据，不提交仓库。
- 用户约束：在用户再次明确允许前，持续禁止扬声器外放、真实麦克风自动采集和 loopback 验收；允许固定音频、代码测试、数据库回放、静默浏览器测试和 Provider API。当前轮没有启动麦克风或播放声音。
- 2026-09-30 静默复验：`8991` 使用当前源码和原验收数据目录重新启动，runtime identity 全项通过；桌面返回、AI 设置的独立“测试连接 / 保存配置”、Escape 关闭和焦点恢复正常；375x667 下 `scrollWidth=innerWidth=375`，会议文字/实时教练切换可被浏览器返回恢复，console 为 0 error / 0 warn。复验未点击“立即开始录音”、未请求麦克风权限、未播放声音，也未调用真实 Provider。
- 最终静默质量门：Pi bridge 54 项、前端 34 个文件/361 项、后端 1802 项（1 项跳过）全部通过；Ruff、ESLint、TypeScript、生产构建、Pi smoke 和 `git diff --check` 通过。构建仅保留既有的约 683KB 主 bundle 警告。

## 1. 结论

当前问题不是单个按钮、卡片或配色问题，而是以下四层问题叠加：

1. 会话生命周期放在页面组件内，导航离开会议页可能销毁录音控制器。
2. “会议状态”“本机采集状态”“后台处理状态”混用，产生“录音已保存、会议进行中、开始录音、结束并整理”同时出现的矛盾界面。
3. Answer、Pi decision、历史和运行状态没有按问题线程组织，用户看到的是不断变化的单卡，而不是可追溯的教练过程。
4. Pi SDK 已经接入 harness/loop，但产品合同只允许输出简短的“边界 / 风险 / 追问”，随后又被压平成一个段落，实际能力被 UI 和 schema 同时限制。

本轮必须按“会话安全 -> 状态统一 -> 信息架构 -> Pi 内容 -> 视觉与无障碍”的顺序整改，不能先换样式再补状态。

## 2. 与已有文档的关系

- `meeting-copilot_answer-copilot_pi-redesign_v1.0.md` 继续作为 Fast Answer / Pi 双 lane、性能和 ASR 保护线的架构基线。
- `meeting-copilot_answer-copilot_pi-acceptance_20260927.md` 继续保存历史验收事实。
- `pi-agent-delivery.md` 继续说明 Pi SDK、harness、loop、Provider 和代码边界。
- 本文覆盖并替代上述文档中较简单的前端 Checklist，成为交互、UI、状态呈现和 Pi 用户体验的主验收文档。

## 3. 审计依据

### 3.1 真实页面

本次审计实际检查了：

- 会议列表：`/workbench`
- 会中页面：`accept_real_regression_pi_20260929_02`
- 会后页面：`rec_mulbirs8_2811851c87f8`
- 开始会议会前检查弹窗
- AI Provider 设置弹窗

真实页面已确认以下现象：

- 会议列表显示 4 个“会议进行中”，其中打开后实际显示录音已保存且当前无录音输入。
- 会中头部同时显示“录音已保存”“开始录音”“结束并整理”。
- “开始录音”同时出现在头部和黄色状态条。
- 右侧首屏先显示当前议题、技术运行明细，核心 Answer/Pi 卡片被推到下方。
- 右侧存在多层独立滚动区，Answer、过去建议、未闭环问题、会议事实之间难以定位。
- 会后页同一失败同时出现在任务条、红色总告警和内容空态中。
- 会前弹窗很长，禁用的“开始会议”位于固定底部，但未在按钮附近解释当前缺少哪一项；录音告知确认项需要滚动后才能看到。
- AI 设置顶部和底部各有一个“测试连接”；赞助商和项目链接占据配置主流程空间。

### 3.2 代码证据

- `ProductNavigation.tsx` 仅在 `active === "live"` 时渲染不可点击的“当前会议”。
- `App.tsx` 进入笔记或离线能力时清除 `meeting_id` 并卸载 `LiveMeetingWorkbench`。
- `useBrowserMicrophone.ts`、`useNativeDualTrack.ts` 在组件卸载时清理采集资源。
- `LiveMeetingWorkbench.tsx` 只根据后端 `meeting.phase` 判断是否已结束，并把“未结束但本地未采集”同时解释成可开始录音和可结束会议。
- `MeetingHistory.tsx` 只要 `meeting.phase === "live"` 就显示“会议进行中”，不检查活动采集、心跳或可恢复状态。
- `NowRail.tsx` 从平铺 suggestions 中选一条 current Answer，再单独寻找 Pi follow-up。
- `reducer.ts` 在新 Answer 到来时清空当前 linked `followUp/coachDecision`，历史虽然可能仍在，但主视图会产生内容被替换的感知。
- `runtime.mjs` 的 Deep Answer 工具合同只有 `boundary / risk / follow_up / why_now`，并被拼接为一个字符串。
- `AiWorkspace.tsx` 保存会议目标时重复请求了一次 preparation versions，增加无意义等待。
- 会前弹窗和 Provider 设置弹窗未实现完整焦点圈定、Escape 关闭和关闭后的焦点恢复合同。

## 4. 产品目标

用户在任何时刻都应能立即回答五个问题：

1. 当前是否真的在录音，录的是什么声音？
2. 会议是否仍在进行，离开这个页面会不会中断？
3. 对方刚才问了什么，我现在可以怎么回答？
4. Pi 发现了什么 Fast Answer 没覆盖的重点，依据是什么？
5. 新内容到来后，之前的回答和建议到哪里去了？

目标体验不是“展示 AI 运行过程”，而是“在不中断会议的前提下，持续提供可说、可追溯、可纠偏的帮助”。

## 5. 设计原则

1. 会话优先：任何普通页面导航都不能终止活动采集。
2. 内容优先：可直接说的回答优先于模型、token、Agent 轮数和内部检查项。
3. 成功内容不可被空状态覆盖：失败、静默、冷却和新任务等待只能更新状态，不能清空有效结果。
4. 一个问题一个线程：Question、Fast Answer、Pi、反馈和 revision 必须一起存放和展示。
5. 状态只表达用户可采取的行动：详细技术指标进入诊断抽屉。
6. 渐进披露：首屏展示结论和下一步，证据、历史、诊断按需展开。
7. 交互可预测：导航、返回、Tab、滚动和版本切换不能自动抢焦点。
8. 保持现有克制的专业工作台风格，不采用营销页式紫色渐变、装饰性卡片堆叠或大面积插画。

## 6. 问题分级矩阵

### 6.1 P0 阻断问题

| ID | 问题 | 用户影响 | 根因 | 完成条件 |
| --- | --- | --- | --- | --- |
| NAV-01 | 离开会议页可能停止录音 | 用户查看笔记后会议输入中断 | 采集 hook 属于会被路由卸载的页面 | 会中切到笔记再返回，PCM、WebSocket、计时连续 |
| NAV-02 | 没有稳定的“正在会议”入口 | 点击后回不去，只能依赖历史记录或浏览器返回 | 活动会议没有应用级状态 | 任意模块一键恢复活动会议 |
| STATE-01 | “会议进行中”与真实采集不一致 | 列表出现多个伪进行中会议 | 只使用后端 phase，缺少活动心跳和恢复态 | 列表只允许一个真实活动会话，其余标为待恢复/未结束 |
| STATE-02 | 同时显示开始与结束操作 | 用户不知道当前到底是否录音 | meeting phase 与 capture phase 未归一 | 任一状态只出现一个主操作 |
| AI-01 | 新 Answer/Pi 产生覆盖感 | 旧回答突然消失，用户无法比较 | 当前值投影和平铺历史分离 | 连续 10 个问题均可按线程回看 |
| AI-02 | Pi 内容过浅且模板化 | 大量信息只得到三句泛化提示 | Deep schema 和 prompt 主动限制输出 | Pi 输出包含意图、遗漏、可说补充、原因、行动、追问和证据 |
| LAYOUT-01 | 核心回答不在右侧首屏 | 用户最需要的信息被状态和滚动区压住 | NowRail 四块固定 grid + 多层滚动 | 当前问题和可直接说内容始终位于右栏顶部 |
| PREFLIGHT-01 | 启动按钮禁用原因不可见 | 用户误以为权限流程卡死 | 必选项在长表单底部，footer 无缺项提示 | 禁用按钮旁明确列出尚缺步骤并可定位 |

### 6.2 P1 核心体验问题

| ID | 问题 | 整改方向 |
| --- | --- | --- |
| NAV-03 | 会议列表返回使用 `replaceState`，返回层级不稳定 | 用户导航使用 `pushState`，仅 URL 规范化使用 replace |
| NAV-04 | 侧栏折叠后完全依赖图标记忆 | 保留 tooltip、活动状态和可展开入口；活动会议显示实时点 |
| NAV-05 | 刷新后的待恢复会议离开详情后丢失返回入口 | 将 recoverable 会话提升到应用导航层，结束或恢复后同步清理 |
| NAV-06 | 小屏切换文字/教练后浏览器返回直接离开会议 | 会中视图写入 history state，`popstate` 只恢复会中面板 |
| HEADER-01 | 头部塞入 7 个技术状态、设置、诊断和三个会议命令 | 合并为一条“采集健康”，详细状态移入诊断 |
| HEADER-02 | “开始录音”重复出现 | 状态带只保留说明，主 CTA 只保留一个 |
| HEADER-03 | 采集健康摘要和独立诊断图标执行同一操作 | 只保留可读状态摘要作为诊断入口 |
| STATUS-01 | “录音已保存 / 无输入 / 会中”语义互相冲突 | 建立统一 MeetingSession 状态机 |
| STATUS-02 | Provider 内部状态文案面向开发者 | 用户层只显示可用、较慢、不可用；错误码进诊断 |
| STATUS-03 | Pi 展示 Agent 轮数、tokens、工具调用等内部数据 | 主界面显示“正在补充/补充完成/暂不可用”，技术数据进诊断 |
| AI-03 | 当前议题为空，但当前问题和回答存在 | 当前问题优先；议题为空不能占据首屏大块区域 |
| AI-04 | Answer 历史和 Pi 历史分成两条无法对应 | 合并为 Answer Thread 时间线 |
| AI-05 | 无“没说中重点”等实时纠偏 | 增加 refinement actions 和原因选择 |
| AI-06 | 用户查看历史时新内容会改变主选择 | 历史查看锁定，显示“有新回答”而不自动跳转 |
| AI-07 | Pi 证据、理由和置信度大多没有可见呈现 | 结构化显示，证据可跳转原文 |
| AI-08 | `Ask AI` 与中文界面混用 | 改为“询问 AI”，统一术语 |
| TRANSCRIPT-01 | 选中文字后只出现图标工具栏，发现性差 | 首次显示图标+短标签，并支持键盘和焦点返回 |
| TRANSCRIPT-02 | 实时文字、已校对、无需校对等状态层级较重 | 正文为主，状态缩为次级元信息 |
| TRANSCRIPT-03 | 活动轨道、说话人和声音来源不够明确 | 双轨时明确“我的麦克风 / 会议声音”，未知身份不强行归属 |
| PREFLIGHT-02 | 必填安全检查与可选会议上下文混在一个长表单 | 上半区只放开始所需项，可选 AI 上下文折叠 |
| PREFLIGHT-03 | 浏览器权限等待时缺少明确的浏览器级操作指引 | 显示“等待浏览器权限”，并给出拒绝后的恢复入口 |
| SETTINGS-01 | 顶部和底部重复“测试连接” | 只保留 footer 测试与保存；顶部仅展示状态 |
| SETTINGS-02 | 保存、运行时同步、连接测试三个状态含义不清 | 固定状态机：未保存、已保存待测试、测试中、已连接、连接较慢、失败 |
| SETTINGS-03 | 赞助商、GitHub、博客打断配置主流程 | 移至“关于”或独立帮助区，不放在 Provider 表单中 |
| REVIEW-01 | 同一失败在任务条、红色告警和内容空态重复 | 页面级摘要一次，任务级操作一次，内容区仅给简短空态 |
| REVIEW-02 | 失败卡仍占据大面积空白 | 失败时显示紧凑状态和重试，不保留空白内容框 |
| HISTORY-01 | “进行中”只看数据库 phase | 使用 active session/heartbeat/recoverable 三态 |
| HISTORY-02 | 删除入口与打开入口距离近，图标语义弱 | 移入行菜单，并保留二次确认和删除范围说明 |
| RESPONSIVE-01 | 小屏把左右两栏各设为约 78dvh 后顺序堆叠 | 小屏使用“文字 / 教练”切换，不串联两个长页面 |
| A11Y-01 | Tab 缺少 roving focus、`aria-controls` 和统一 panel 语义 | 实现完整键盘 Tab 合同 |
| A11Y-02 | 弹窗缺少完整焦点圈定和 Escape/焦点恢复 | 提取统一 Dialog 基础组件 |
| A11Y-03 | Pi 反馈、会后导出等非模态菜单无法用 Escape/点击外部可靠关闭 | 统一 dismissable popover 合同并在 Escape 后恢复触发按钮焦点 |

### 6.3 P2 完善问题

| ID | 问题 | 整改方向 |
| --- | --- | --- |
| DENSITY-01 | 会议列表首屏统计卡占用较高 | 有活动会议时优先展示活动条，统计降为紧凑摘要 |
| LAYOUT-02 | 桌面端左右宽度固定，长回答和长转写不可调 | 支持有限范围拖动分栏并记忆偏好 |
| AI-09 | 没有“只看可直接说 / 展开分析”的密度偏好 | 增加紧凑/详细模式，默认详细但不堆满首屏 |
| AI-10 | 历史只有有限条目且没有搜索 | 提供按问题时间线和关键词过滤 |
| FEEDBACK-01 | Toast 3 秒自动消失，部分错误缺少可恢复动作 | 重要失败保持到用户处理，成功提示可自动消失 |
| PERF-01 | 保存会议上下文重复请求 versions | 删除重复请求，减少无意义等待 |
| ACCESS-01 | 缺少跳过导航入口 | 增加“跳到会议文字 / 跳到 AI 教练”快捷入口 |
| ACCESS-02 | 部分小图标点击区不足 44px | 扩大 hit area，不改变图标视觉尺寸 |

## 7. 目标信息架构

### 7.1 全局导航

```text
会议记录
正在会议 · 06:42       仅真实活动或可恢复会话存在时显示
  支付服务上线评审
笔记
离线能力
```

活动会议项规则：

- `capturing / reconnecting / paused`：绿色或黄色实时点，始终可点击。
- `recoverable`：黄色“待恢复”，点击进入恢复页。
- `ending`：显示“正在保存”，禁止再创建第二个会议。
- `ended`：从活动入口移除，进入会议历史。
- 不允许仅因为数据库 `phase=live` 就显示为正在录音。

### 7.2 会中桌面布局

```text
┌─────────────────────────────────────────────────────────────────────┐
│ 返回 会议标题    录音中 · 输入正常 · AI 可用       暂停  结束并整理 │
├───────────────────────────────┬─────────────────────────────────────┤
│ 会议文字                       │ [实时教练] [询问 AI] [会议目标]      │
│ 搜索  已确认数                  │ 当前问题                            │
│                               │ 可直接说                            │
│ 实时转写流                     │ Pi 深度补充                         │
│                               │ 反馈操作                            │
│                               │ 最近问题线程                        │
└───────────────────────────────┴─────────────────────────────────────┘
```

桌面端默认比例约 56:44，可在 48:52 到 68:32 范围拖动。右栏只有一个主滚动容器；Tab 和当前问题摘要可粘性定位，但不能形成嵌套滚动。

### 7.3 小屏布局

```text
会议标题             录音中   结束
[会议文字] [实时教练]

当前选中视图占满屏幕
底部保留录音状态与主操作
```

小屏不纵向堆叠完整 Transcript 和完整 AI Workspace，避免用户滚动两屏才能看到教练内容。

## 8. 统一会议状态机

前端新增应用级 `MeetingSessionState`：

```text
idle
preparing
capturing
paused
reconnecting
recoverable
ending
ended
error
```

### 8.1 状态来源优先级

1. 本机 capture controller 和桌面 bridge 活动状态。
2. 最近有效 heartbeat / PCM / WebSocket 活动时间。
3. 后端 meeting phase。
4. 历史快照仅用于恢复提示，不能单独证明正在录音。

### 8.2 主操作矩阵

| 状态 | 主操作 | 次操作 | 禁止出现 |
| --- | --- | --- | --- |
| idle | 开始会议 | 导入录音 | 结束并整理 |
| preparing | 正在准备 | 取消 | 再次开始 |
| capturing | 结束并整理 | 暂停、诊断 | 开始录音 |
| paused | 继续录音 | 结束并整理 | 再次创建会议 |
| reconnecting | 正在恢复 | 结束并保存 | 普通开始录音 |
| recoverable | 恢复录音 | 结束并整理 | “正在录音” |
| ending | 正在保存 | 无 | 所有重复命令 |
| ended | 查看复盘 | 导出 | 录音控制 |
| error | 恢复录音 | 保存现有内容、诊断 | 假装仍在采集 |

状态条只在需要用户行动时出现；正常状态由头部紧凑健康提示表达。

## 9. 会前检查重设计

### 9.1 首屏必选项

首屏只展示能否开始会议的条件：

1. 声音来源和设备。
2. 麦克风/系统音频检查状态。
3. 本地写入和 ASR 可用状态。
4. 录音告知确认。
5. AI 状态只作为可选能力，不阻塞本地录音和转写。

### 9.2 可选项

会议名称、教练技能、我的角色、会议目标、重点关注、整理格式、主动建议和技术词放入“会议与教练设置”折叠区。保留上次选择，但不得把旧会议的具体目标自动带入新会议。

### 9.3 权限交互

- 点击“检查麦克风”后立即变为“等待浏览器麦克风权限”。
- 同时显示：“请在浏览器地址栏附近选择允许本次使用麦克风”。
- 权限允许后进入 2-3 秒采样状态，并持续显示音量反馈。
- 权限拒绝后显示“重新请求”和“打开站点权限说明”，不能无限等待。
- 启动按钮禁用时，在 footer 直接显示缺项，例如“还需：允许麦克风、确认已告知参会者”。
- 点击缺项可滚动并聚焦到对应控件。

## 10. 会中文字区整改

1. 保持 canonical transcript 为唯一正文，partial 只作为临时尾部。
2. 双轨明确显示“我的麦克风 / 会议声音”；身份未知时不推断参会者姓名。
3. “已校对 / 无需校对 / 校对失败保留原文”改为次级状态，不抢正文视觉。
4. 展开校对稿时同时显示原文、最终文本、修正来源和时间，避免用户不知道哪一份会进入纪要。
5. 用户向上滚动时停止自动跟随，并显示固定的“回到最新 · N 条新内容”。
6. 搜索模式不自动跳回最新；退出搜索后恢复进入搜索前的位置。
7. 选中文字工具栏首轮显示“保存 / 询问 / 解释 / 提炼行动项”，再次使用后可收为图标。
8. 选中文字触发询问 AI 后，右侧切到询问页并明确显示引用范围，返回后保留原选择位置。
9. 会中提供“复制已确认文字”，但导出完整稿仍放在会后页。

## 11. AI 工作区重设计

### 11.1 Tab

```text
[实时教练] [询问 AI] [会议目标]
```

- 默认打开实时教练。
- 每个 Tab 保留自己的滚动位置。
- 新 Answer 到来时，若用户位于其他 Tab，只显示计数点，不强制切换。
- Tab 支持左右方向键、Home/End、`aria-controls` 和对应 `tabpanel`。

### 11.2 实时教练首屏

```text
AI 实时教练                         监听中

当前问题 · 14:32
为什么这次方案选择本地模型？        查看原话

可直接说
先给结论……                          复制

Pi 深度补充
核心判断
遗漏重点
可补充说
为什么重要
可能追问与回答方向
证据依据

没说中重点  更具体  补风险  换个角度  一句话版

最近问题  3
```

“当前议题”不再占据独立大块首屏。存在明确问题时优先展示问题；没有问题时再显示议题、正在关注的风险和等待状态。

### 11.3 内容更新规则

- 新问题创建新线程，不清空旧线程。
- 当前线程正在 streaming 时，旧成功线程仍保留在下方。
- 用户查看历史线程时，新线程只增加“1 条新回答”提示。
- Provider 失败、超时、静默和冷却不替换成功 Answer/Pi。
- 当前 Answer 已成功但新 revision 失败时，继续展示成功 revision，并在旁边显示“更新失败”。
- Pi 还在生成时先显示 Answer，Pi 区显示稳定的“正在补充重点”，不改变卡片标题。

## 12. Pi Agent 产品定位和输出合同

### 12.1 定位

Fast Answer 负责“现在先怎么回答”。

Pi Agent 负责：

- 判断问题真实意图和评判标准。
- 检查当前回答是否正面回答了问题。
- 从更早会议上下文检索支持事实、承诺、冲突和约束。
- 找出遗漏的结论、数字、边界、风险、责任或下一步。
- 给出一段可追加说出口的话。
- 预测最多两个高概率追问，并给出回答方向。
- 接受用户“没说中重点”等反馈，基于同一 Answer 生成新 revision。

Pi 不负责：

- 阻塞 Fast Answer 首字。
- 改写 ASR。
- 每段话都生成摘要。
- 用 keep_silent、失败或空状态覆盖成功内容。
- 展示 Agent 内部 token、工具轮数来代替业务结果。

### 12.2 新的结构化输出

```text
headline
question_intent
core_judgement
why_it_matters
say_this_addition
missing_points[]
constraints[]
risks[]
next_actions[]
likely_follow_ups[]
  question
  answer_angle
evidence_refs[]
  segment_id
  quote
confidence
```

稳定持久化和 UI 合同使用上面的完整结构。实时 Provider 为避免重复生成，当前只提交以下最小 terminal tool；host 从 Answer、绑定证据和两个固定语义要点展开完整结构，不能自行补写事实：

```text
headline
say_this_addition
key_points[0] = missing judgement
key_points[1] = next action
confidence
```

建议限制：

- `question_intent`：1 句，不超过 80 个中文字。
- `core_judgement`：1 句，不超过 100 个中文字。
- `say_this_addition`：60-180 个中文字，可直接说出口。
- `missing_points / constraints / risks / next_actions`：每组最多 3 项，每项不超过 80 字。
- `likely_follow_ups`：最多 2 项，每项包含问题和回答方向。
- 自动补充默认保持在当前会议仍可使用的长度；更详细内容通过新 revision 展开，不阻塞 Fast Answer。

### 12.3 质量门禁

每次 Pi 提交前必须通过：

1. 正面回应当前问题，而不是总结会议。
2. 不重复 Fast Answer 已有的核心句。
3. 至少提供一个增量点；没有增量价值才允许静默。
4. 具体数字、人员、产品、截止时间和完成状态必须来自绑定证据。
5. 至少给出一个可执行动作或可说补充。
6. 证据不足时明确缺失信息，不输出通用套话。
7. “需要确认适用边界”这类无对象模板不能单独作为有效卡片。

### 12.4 Harness/Loop

```text
answer_ready / user_request
        |
        v
读取当前问题 + Fast Answer + 本轮证据
        |
        v
按场景 checklist 检查回答覆盖度
        |
        +--> 信息足够：直接 submit_coaching_package
        |
        +--> 缺早期事实：search_prior_evidence -> read_transcript_span
        |
        v
事实校验 + 增量价值校验 + Answer ID 绑定
        |
        v
追加 Pi revision / keep_silent / explicit failure
```

必须继续保持有限工具集、绝对 deadline、证据引用和终止工具约束；扩展的是业务输出合同，不是无限放开 Agent。

## 13. Answer/Pi 线程和版本模型

```text
AnswerThread
  answer_id
  meeting_id
  question_text
  question_segment_ids[]
  created_at_ms
  status
  answer_revisions[]
  pi_revisions[]
  selected_answer_revision
  selected_pi_revision
```

```text
PiRevision
  decision_id
  answer_id
  revision
  supersedes_decision_id
  trigger_type
  user_request
  status
  content
  evidence_refs[]
  provider/model/runtime
  created_at_ms
  completed_at_ms
```

版本规则：

- `answer_id` 决定建议属于哪个问题。
- `decision_id` 唯一标识一次 Pi 输出。
- 只有同一 `answer_id` 的新成功 revision 可以 supersede 旧 revision 的默认展示。
- 不同 Answer 之间不建立 supersede 关系。
- `failed / timeout / not_triggered / protected_silent` 不得 supersede 成功版本。
- 历史默认展开最新版，但可切换旧版并查看“为什么更新”。
- 前端至少保留最近 20 个线程，服务端保留完整事件历史。

## 14. “没说中重点”反馈闭环

点击后提供原因：

- 偏离了问题。
- 内容太空泛。
- 没有结合当前会议。
- 缺少明确结论。
- 缺少案例或数据。
- 事实不准确。

行为合同：

1. 不删除原回答。
2. 将原因作为 `user_request`，绑定当前 `answer_id`、当前 revision 和原证据。
3. 创建新的 refinement job，状态在原卡下方可见。
4. 成功后生成新 revision，并显示“根据反馈重新分析”。
5. 失败时保留旧版本，并提供重试。
6. 用户可以在 `v1 / v2 / v3` 之间切换。

快捷动作映射：

| 操作 | Pi 指令重点 |
| --- | --- |
| 更具体 | 引用会议中的事实、条件或原话，减少抽象判断 |
| 补风险 | 补充最可能导致决策失败的风险和验证方式 |
| 换个角度 | 改用业务、技术、执行或对方立场分析 |
| 一句话版 | 生成 20-40 个中文字的直接回应 |
| 没说中重点 | 先重新判断问题意图，再生成修订版 |

## 15. Provider 设置整改

1. 顶部只显示连接状态，不再提供第二个“测试连接”。
2. Footer 固定为“测试连接”“保存配置”；两者分离。
3. 配置有修改时测试按钮说明“先保存修改”，保存成功后状态变为“已保存，待测试”。
4. 测试结果区分“已连接”“连接较慢”“鉴权失败”“模型不可用”“协议不匹配”。
5. 保存成功不等于测试成功，也不等于实时 SLO 通过。
6. Base URL 显示规范化预览，避免 `/v1` 重复或缺失造成隐性错误。
7. 高级设置明确区分通用模型、实时模型和校正模型。
8. API Key 永不回显，只显示是否已保存和最后更新时间。
9. 赞助商、GitHub 和博客移至“关于”，避免影响关键配置任务。
10. 弹窗实现 Escape 关闭、焦点圈定、关闭后焦点回到触发按钮。

## 16. 会后复盘整改

### 16.1 失败信息层级

```text
页面摘要：2 项 AI 内容未完成                       [全部重试]
任务列表：会议纪要 失败 [重试]  分析建议 失败 [重试]
内容区：会议复盘暂未生成                          [重试]
```

同一个错误不再出现两块红色通栏。校对失败是独立的数据质量问题，放到会议文字 Tab 的状态摘要，不与纪要任务失败混成一个告警层。

### 16.2 进度语义

- “纪要完成度 60%”必须说明计算项，或改为“3/5 项已完成”。
- 失败项不使用绿色完成感样式。
- 重试按钮必须有文字或稳定 tooltip，并在执行后显示任务状态变化。
- 失败内容卡使用紧凑空态，不保留大面积无效空白。
- 用户编辑版与 AI 新草稿保持版本隔离，重新生成不得覆盖用户最终稿。

## 17. 视觉规范

- 延续现有白色、浅灰、蓝色主操作和绿色成功状态，不引入紫色 AI 营销主题。
- 卡片圆角不超过 8px；页面区块不全部卡片化。
- 正常状态减少边框和底色，警告/错误颜色只用于需要行动的内容。
- 当前问题 13-14px，回答正文 14-16px，右栏内部标题不使用页面级大字号。
- 图标统一使用现有 Lucide，常规笔画宽度保持一致。
- 所有布局变化只使用颜色、透明度和边框过渡，不用会导致位移的 hover transform。
- 动效 150-250ms，并尊重 `prefers-reduced-motion`。
- 触控目标至少 44x44px；桌面小图标可视觉较小，但命中区域仍满足要求。
- 明暗模式分别检查文本、边框、禁用和焦点状态对比度。

## 18. 返回、关闭与恢复入口契约

“有返回箭头”不等于流程可逆。每个会改变视图、上下文、焦点或会话状态的入口，都必须提供明确且不丢数据的返回路径。

### 18.1 页面级返回矩阵

| 当前页面/动作 | 必须提供的返回方式 | 返回后必须保留 | 禁止行为 |
| --- | --- | --- | --- |
| 会议列表 -> 会议详情 | 浏览器返回、左侧会议记录、头部返回 | 搜索、筛选、排序和列表滚动位置 | 使用 replace 导致历史记录消失 |
| 会中 -> 笔记 | 左侧正在会议、浏览器返回 | 录音、计时、PCM、WebSocket、当前 Answer/Pi | 卸载活动采集或创建新会议 |
| 会中 -> 离线能力 | 左侧正在会议、浏览器返回 | 同上 | 把离开页面解释成结束会议 |
| 历史会议详情 -> 会议列表 | 头部返回、左侧会议记录、浏览器返回 | 列表状态和滚动位置 | 返回后丢失查询条件 |
| 当前活动会议 -> 另一个历史会议 | 左侧正在会议 | 活动会议继续后台采集 | 把查看历史会议切换为活动会话 |
| 会议文字证据 -> AI 卡片 | “返回建议”或保持原 AI Tab/线程选择 | 选中的 Answer/Pi revision 和右栏滚动位置 | 返回后跳到最新卡片 |
| AI 卡片 -> 会议原话 | 原文高亮、可返回原卡片 | 证据前的阅读上下文 | 只滚动原文但不给返回定位 |
| 移动端文字 -> 教练 | 顶部分段控件、系统返回仅退回上一视图 | 两个视图各自滚动位置 | 系统返回直接离开会议或停止录音 |
| 页面刷新/进程重载 | 恢复提示和明确“恢复录音” | 已持久化文字、Answer/Pi 和 meeting ID | 假装浏览器 MediaStream 已自动恢复 |

### 18.2 弹层返回矩阵

| 弹层 | 关闭入口 | 键盘/焦点合同 | 有未保存修改时 |
| --- | --- | --- | --- |
| 会前检查 | 取消、右上关闭、Escape、scrim | 打开后聚焦标题或首个必选项；关闭后回到“开始会议” | 未开始会议，可直接取消；已触发权限检查时先停止临时 stream |
| AI 设置 | 右上关闭、Escape、scrim | 焦点圈定；关闭后回到 AI 设置按钮 | 提示“放弃未保存修改”，不得静默丢失 |
| 运行诊断 | 关闭、Escape、scrim | 焦点圈定；关闭后回到诊断按钮 | 导出进行中需给出状态，不阻止普通关闭 |
| 删除本地数据 | 取消、Escape | 默认聚焦取消，危险操作不是默认动作 | 执行后明确删除范围和不可恢复性 |
| 导入录音 | 取消、Escape | 关闭后回到导入按钮 | 上传/处理进行中需二次确认取消 |
| 选中文字工具栏 | Escape、点击正文空白 | 焦点可在工具栏和原文间返回 | 不改变或清除原文字内容 |
| Pi 反馈原因菜单 | 取消、Escape、点击外部 | 关闭后回到触发反馈按钮 | 已提交 refinement 后显示任务状态，不再静默关闭 |
| 会后导出菜单 | Escape、点击外部、选择格式 | Escape 后回到导出按钮；导出中禁用重复提交 | 导出失败保留页面内容并显示可读错误 |

### 18.3 返回行为实现规则

1. 用户主动页面导航使用 `history.pushState`；只有 URL 别名清理、消费一次性 evidence 参数等规范化动作可以使用 `replaceState`。
2. `popstate` 只恢复视图，不结束会议、不删除状态、不重新创建 meeting ID。
3. 页面滚动、列表筛选、AI Tab、历史线程选择和 revision 选择进入可恢复的 view state。
4. 活动采集属于应用会话层，不属于某个 URL 对应的页面组件。
5. “返回会议列表”和“返回正在会议”是两个不同命令，文案和目标不能混用。
6. 所有 icon-only 返回、关闭和恢复按钮必须有 `aria-label`、tooltip 和至少 44x44px 命中区域。
7. 移动端不得依赖 hover tooltip 才能理解返回或关闭入口。
8. 关闭弹层后必须把焦点恢复到触发元素，不能落到页面顶部或浏览器地址栏。

### 18.4 返回/关闭专项 Checklist

- [x] 逐页审计会议列表、会中、会后、笔记、离线能力及主要弹层的入口与出口。
- [x] 会中进入笔记、离线能力、历史会议后均有“正在会议”恢复入口。
- [x] 会议列表进入详情再返回，搜索/筛选/排序/滚动位置保留。
- [x] 查看证据后可回到原 Answer/Pi 线程和 revision。
- [x] 小屏视图切换保留左右区域组件、状态和各自滚动位置。
- [x] 所有现有 Dialog/Drawer 支持 Escape、scrim、显式关闭、焦点圈定和焦点恢复。
- [x] AI 设置和数据保留策略有未保存修改时不会静默丢失。
- [x] 活动录音状态下任何非结束型返回操作都不会触发 capture cleanup。
- [x] 浏览器刷新后进入 recoverable，不错误显示录音中。
- [x] 浏览器前进/后退自动化测试覆盖会议列表、会中、笔记和离线能力的视图恢复。
- [x] recoverable 会议离开详情后仍保留“会议待恢复”入口，恢复或结束后清理。
- [x] 小屏文字/教练切换进入浏览器历史，返回只恢复前一会中视图。
- [x] AI Tab、Ask AI 线程、Answer 和 Pi revision 离开页面再返回后恢复。
- [x] Pi 反馈、会后导出和文字选择工具支持 Escape；菜单点击外部关闭，Escape 后恢复焦点。

### 18.5 补充交互审计（2026-09-29）

此前自动化只覆盖单个详情页识别跨窗口 lease，真实从首页进入仍有缺口。以下问题已经补充实现，并以最终自动化和 round2 真实浏览器证据关闭：

| ID | 现状 | 用户影响 | 整改与验收 |
| --- | --- | --- | --- |
| NAV-07 | `App` 只有打开会议详情后才知道服务端 `capture=active` | 第二窗口直接进入首页、笔记或离线能力时，左侧没有“另一窗口录音”入口 | 应用层独立轮询 live meeting 摘要；任意页面都能返回该会议，lease 过期后自动变为“待恢复” |
| ACTION-01 | 首页“开始会议”只检查本窗口 microphone phase | 另一窗口正在录音时仍可能创建第二场会议并争用麦克风 | 存在本机、跨窗口或待恢复会议时，主按钮改为“返回/查看/恢复会议”，不打开新会议会前检查 |
| NAV-08 | `<=760px` 时整个 `ProductNavigation` 被隐藏 | 手机端首页无法进入笔记/离线能力，会中离开后也缺少稳定的活动会议入口 | 增加固定底部主导航，保留会议记录、活动会议、笔记、离线能力；安全区、最长文案和 44px 命中区通过验收 |
| AI-11 | “当前议题”仍排在 Answer/Pi 前面 | 右栏首屏先看到泛化议题，当前问题和可直接说内容被下推 | Answer/Pi 成为实时教练首个内容区；当前议题降为后续上下文，DOM 和视觉顺序均验证 |
| AI-12 | 没有 Answer 时把不断变化的 runtime decision 当成空态主标题 | 用户感知为标题闪烁、正文没有结果 | 空态使用稳定标题；runtime decision 仅作“本轮状态”辅助信息，成功内容仍不能被失败或静默覆盖 |

补充交互验收 Checklist：

- [x] 首页首次加载即可发现另一窗口的活动 lease，并显示可返回入口。
- [x] 跨窗口活动 lease 期间无法从首页打开新会议会前检查。
- [x] lease 过期后入口从“另一窗口录音”自动切换为“会议待恢复”。
- [x] 在笔记、离线能力停留时仍能发现并返回活动/待恢复会议。
- [x] 390x844、375x667 和 <=340px 均可访问全部主导航，且不遮挡页面最后一项内容。
- [x] 有 Answer 时右栏首个内容区是当前问题/建议回答/Pi 补充，不是当前议题。
- [x] 没有 Answer 时空态标题稳定，Provider/loop 本轮状态作为辅助信息展示。
- [x] 上述行为均有自动化测试，并通过真实浏览器前进/后退和截图检查。

## 19. 实现分期

### Phase 0：会话安全与状态统一

- [x] 将 microphone/dual-track controller 提升到 `App` 生命周期；现阶段无需额外增加空壳 Provider。
- [x] 页面切换不卸载活动采集控制器。
- [x] 定义统一 `MeetingSessionState` 和状态优先级。
- [x] 增加活动 heartbeat / recoverable 判定。
- [x] 清理伪“会议进行中”状态，区分本机活动与后端未结束/待恢复会议。
- [x] 保证任一状态只有一个主操作。
- [x] 增加导航中断录音的回归测试。

### Phase 1：导航、会前和头部

- [x] 左侧新增稳定“正在会议”入口和计时。
- [x] 统一用户导航 `pushState`、URL 清理 `replaceState` 和 `popstate` 恢复行为。
- [x] 会前必选项与可选上下文分层。
- [x] 在启动按钮旁展示缺项和定位操作。
- [x] 优化浏览器权限等待、拒绝和重试状态。
- [x] 合并头部技术状态为采集健康摘要。
- [x] 删除与采集健康摘要重复的独立诊断入口。
- [x] 删除重复“开始录音”入口。

### Phase 2：Answer Thread 和右侧布局

- [x] 建立 AnswerThread 投影和版本模型。
- [x] 将 Answer 与 Pi 按 `answer_id` 聚合。
- [x] 实现当前/历史稳定选择，不自动抢焦点。
- [x] 按 meeting ID 恢复 AI Tab、Ask AI 线程、Answer 和 Pi revision 阅读状态。
- [x] 右栏改为单主滚动容器。
- [x] 当前问题和可直接说内容置于右栏首屏优先位置。
- [x] 合并最近回答和过去建议为按问题组织的线程。
- [x] 移除主界面的 token、Agent 轮数和工具次数。
- [x] 小屏改为“会议文字 / 实时教练”视图切换。
- [x] 小屏视图切换接入浏览器历史，返回不离开会议。

### Phase 3：Pi 内容与反馈闭环

- [x] 扩展 Pi terminal tool schema。
- [x] 扩展持久化和前端类型，禁止把结构重新压平成字符串。
- [x] 加入问题意图和回答覆盖度 checklist。
- [x] 加入重复检测、证据和增量价值门禁。
- [x] 实现“没说中重点 / 更具体 / 补风险 / 换角度 / 一句话版”。
- [x] 支持 Pi revision、supersedes 和失败保留旧版。
- [x] 场景 skill 分别定义输出重点。
- [x] 修复绑定旧 Answer 时后续段落挤掉原证据的问题，并覆盖真实 revision 数据合同。
- [x] Deep Answer 使用隔离的非流式 Responses transport；realtime lane 保持 SDK streaming。
- [ ] 当前真实 Provider 在 8 秒内完成 terminal tool，并产生可见 Pi revision。

### Phase 4：设置、会后和全局一致性

- [x] Provider 设置只保留一个测试入口。
- [x] 移出赞助商和项目链接。
- [x] 会后错误去重和任务级重试。
- [x] 会议列表区分活动、待恢复、未结束和已完成。
- [x] 删除操作移入行菜单。
- [x] 统一现有 Dialog/Drawer 的关闭、焦点、忙碌和未保存反馈合同。
- [x] 修复会议上下文保存的重复请求。

### Phase 5：无障碍、响应式和视觉验收

- [x] Tab 键盘行为和 panel 语义完整。
- [x] Dialog 焦点圈定、Escape 和焦点恢复。
- [x] 会中增加“跳到会议文字 / 跳到实时教练” skip links 和稳定主区锚点。
- [x] 图标按钮 44px 命中区域和可见 focus ring。
- [x] reduced-motion 动画与滚动降级。
- [x] 深浅侧栏视觉检查；全局暗色模式不在当前产品能力范围内。
- [x] 1440x1000、1280x800、980x900、390x844 和 375x667 验收。

### Phase 6：补充交互闭环

- [x] 将 capture freshness 从详情页提升为应用级活动会议发现。
- [x] 首页主操作根据本机/跨窗口/待恢复状态切换，禁止重复创建。
- [x] 小屏增加固定底部主导航并验证安全区和内容尾部可达。
- [x] Answer/Pi 提升为右栏首个内容区，当前议题后移。
- [x] 教练空态使用稳定标题，本轮 loop/provider decision 降为辅助状态。
- [x] 补齐跨窗口发现、过期切换、首页主操作、移动导航和内容顺序测试。
- [x] 重新构建并完成桌面/移动真实浏览器截图、重叠和 console 验收。

## 20. 主要代码落点

| 模块 | 预计修改 |
| --- | --- |
| `src/app/App.tsx` | 路由、应用级采集生命周期、活动会议恢复和 history 语义 |
| `src/app/meetingNavigationState.ts` / `src/features/live-meeting/meetingSessionState.ts` | 按会议保存导航阅读状态；统一采集、恢复和结束状态投影 |
| `src/components/ProductNavigation.tsx` | 正在会议入口、状态和计时 |
| `src/features/live-meeting/LiveMeetingWorkbench.tsx` | 统一状态和主操作、简化头部 |
| `src/features/live-meeting/MeetingPreflightDialog.tsx` | 权限、必选项、缺项提示、渐进披露 |
| `src/features/live-meeting/TranscriptPane.tsx` | 选择工具、轨道标识、跟随与搜索 |
| `src/features/live-meeting/AiWorkspace.tsx` | Tab、滚动位置、术语和上下文请求 |
| `src/features/live-meeting/NowRail.tsx` | AnswerThread、结构化 Pi、历史和反馈 |
| `src/domain/events.ts` / `reducer.ts` | 线程、revision、错误不可覆盖成功状态 |
| `src/features/settings/ProviderSettingsControl.tsx` | 单一测试入口和连接状态 |
| `src/features/history/MeetingHistory.tsx` | 活动/待恢复状态与安全操作 |
| `src/features/review/ReviewWorkspace.tsx` | 错误去重和重试层级 |
| `src/styles.css` | 新信息架构、单滚动区和响应式 |
| `pi_coach_bridge/src/runtime.mjs` | 新 Pi coaching package schema 和 prompt |
| `realtime_intelligence.py` | 结构校验、证据和增量价值门禁 |
| `v2_persistence.py` | Pi revision、Answer 绑定和投影 |

## 21. 自动化测试 Checklist

### 导航和会话

- [x] 会中打开笔记，controller 不卸载，采集仍 active。
- [x] 从笔记点击正在会议恢复同一 meeting ID。
- [x] 浏览器后退/前进恢复正确页面和活动入口。
- [x] 小屏浏览器返回恢复上一个会中面板，不离开会议。
- [x] 刷新后不能自动恢复浏览器 MediaStream 时进入 recoverable。
- [x] recoverable 状态离开详情后仍有稳定返回入口。
- [x] 不允许同时存在两个本机活动采集会话。

### 状态

- [x] capturing 只显示暂停和结束，不显示开始。
- [x] recoverable 只显示恢复和结束，不显示录音中。
- [x] ended 不显示录音控制。
- [x] 数据库 phase=live 但不是本机 active session 时不显示为正在录音。
- [x] 正常活动采集状态不出现黄色/红色恢复通栏。

### Answer/Pi

- [x] 连续 10 个问题形成 10 个独立线程。
- [x] 每条 Pi 严格绑定对应 Answer ID。
- [x] 新问题不清除旧 Answer/Pi。
- [x] 静默、失败、超时、冷却不清除成功内容。
- [x] 同一 Answer 新成功 revision 默认展示，旧版可回看。
- [x] 新 revision 失败时继续显示上一个成功版本。
- [x] 查看历史时新 Answer 只显示通知，不抢焦点。
- [x] 用户 feedback 触发带 `user_request` 的新 job。

### 会前和设置

- [x] 麦克风权限等待、允许、拒绝、无设备、静音和超时均有明确状态。
- [x] 启动按钮禁用时可见且可定位全部缺项。
- [x] AI 不可用不阻塞本地录音和 ASR。
- [x] Provider 保存和测试完全分离。
- [x] 修改配置后必须重新测试。
- [x] Base URL 规范化有测试。
- [x] Dialog 支持 Escape、焦点圈定和焦点恢复。
- [x] 非模态菜单支持 Escape、点击外部关闭和触发按钮焦点恢复。

### 会后

- [x] 同一失败不重复显示两块页面级告警。
- [x] 单任务重试只更新对应任务。
- [x] 重新生成不覆盖用户最终稿。
- [x] transcript correction 失败时原文和最终导出行为明确。

### 响应式和无障碍

- [x] 桌面右侧无嵌套滚动和内容截断。
- [x] 小屏下文字和教练通过视图切换访问，组件切换时不卸载。
- [x] 最长中文和英文单词使用 `overflow-wrap: anywhere` 和 `word-break: break-word`，不撑破会议主区和历史行。
- [x] Tab、会议行菜单、Dialog 和反馈操作可全键盘完成。
- [x] focus ring 可见，颜色不是唯一状态提示。
- [x] reduced-motion 下禁用非必要动画并将平滑滚动降级为即时滚动。

## 22. 真实 Provider 验收

当前结论：未通过。`job_0e348dd7ed517344c68eca36` 是最新受控复验，20.008 秒命中 deadline；以下门槛和 DoD 保持未勾选，不能用 mock、Fast Answer 或本地 fallback 替代。

### 22.1 五分钟会议流程

1. 开始双轨或麦克风会议。
2. 会中切到笔记，再通过“正在会议”返回。
3. 连续提出至少 5 个不同问题。
4. 在一个回答上点击“没说中重点”，再点击“更具体”。
5. 查看旧问题并等待新问题到来，确认不抢焦点。
6. 暂停/恢复或模拟输入中断。
7. 结束并整理，检查文字、录音、纪要和导出。

### 22.2 内容质量门槛

- Fast Answer 有效回答率不低于 85%。
- Pi 每条成功输出至少包含一个可验证会议事实或明确的信息缺口。
- Pi 每条成功输出至少包含一个可执行动作或可说补充。
- Pi 不重复 Fast Answer 的主要内容。
- Pi 相对只看 Fast Answer 的增量价值盲评通过率不低于 70%。
- 具体事实无证据幻觉为 0。

### 22.3 性能门槛

- Fast Answer final-to-first-token P50 <= 1.5s，P95 <= 2.5s。
- Fast Answer 完成 P95 <= 5s。
- Pi 完整补充 P95 <= 8s。
- UI 接收事件到可见渲染 P95 <= 150ms。
- 页面切换不丢失 PCM、未提交 final 或当前 Answer 状态。

### 22.4 ASR 不回归门槛

- 本轮不修改采集、VAD、FunASR worker、canonical final/revision 和 correction 算法。
- 同一固定音频改造前后 canonical transcript 必须一致。
- Provider 和 Pi 全部关闭时，左侧实时文字仍独立可用。
- UI 重构不得改变导出所使用的最终文字版本。

## 23. Definition of Done

只有同时满足以下条件才能宣称本轮完成：

- [x] P0 全部关闭，无导航导致的采集中断。
- [ ] P1 全部关闭或有用户明确接受的延期项。
- [x] Answer/Pi 按线程和版本可追溯。
- [x] Pi 输出合同和 UI 都不再只有三行模板。
- [ ] “没说中重点”真实触发 revision 并保留旧版。
- [ ] 真实 Provider、多问题、切页、结束会议完整走通。
- [x] 自动化测试、构建和关键回归通过。
- [x] 桌面和移动截图验收通过，无重叠、跳动和内容遮挡。
- [x] ASR 固定音频回归一致。
- [ ] 文档中的验收证据填入真实 meeting ID、指标和截图路径。

## 24. 明确不接受的伪完成

- 只修改标题、颜色或空状态文案。
- 把技术运行日志显示得更多，却没有更有用的会议建议。
- 通过隐藏历史来消除“覆盖感”。
- 用 mock Provider 代替真实 Provider 验收。
- 只验证 Pi SDK 被调用，不验证输出增量价值。
- 为修右侧体验改动左侧 ASR 链路。
- 以“测试通过数量多”代替真实用户工作流验收。
- 在 P0 尚未关闭时继续增加外围功能。
