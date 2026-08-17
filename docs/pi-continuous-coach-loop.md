# Pi 持续实时教练 Agent Loop 实施说明

> 分支：`feat/pi-continuous-coach-loop`
>
> 实施提交：`8c36b09`、`9ef5364`、`0cb383d`、`3d271c2`、`6f106eb`、`e2ec16a`、`09e050c`、`fe64d78`、`cb2cd5f`、`ba3cb54`
>
> 日期：2026-08-17

## 1. 本轮交付结果

本分支把上一版“Pi 作为可选的单次教练执行器”升级为持续会议教练：

1. Pi 是本分支默认实时教练 runtime，异常时自动回退原有 direct LLM。
2. 同一会议复用同一个 Pi Session，连续保留最近的教练判断历史。
3. 每个新稳定转写片段都会驱动一轮 Agent Loop；系统声音和麦克风片段都会更新状态。
4. 每轮必须先执行 6 项教练 checklist，再决定是否读取上下文、检索历史证据、给出建议或保持静默。
5. 新增持续表达清晰度检查：重复、失焦或缺少结论时，给出一条可直接说出口的收束句。
6. 右侧“AI 实时教练”直接展示 Pi 是否监听、检查、回退，以及本轮 checklist、检索次数和静默原因。
7. 转写精修导致在途证据过期时，旧任务标记为已取消，并自动对最新证据补排一次分析，不再把正常修订显示成 AI 失败。

这里的“持续”是事件驱动常驻 Session，不是静音时无限轮询。没有新稳定转写时不调用模型，避免无意义延迟和费用。

## 2. 用户看到什么

实时教练标题右侧会显示当前状态：

- `Pi 教练监听中`：上一轮确实由 Pi 完成。
- `Pi 教练正在检查`：新片段正在进入 Agent Loop。
- `Pi 已回退普通模式`：Pi sidecar 或 Provider 失败，本轮由 direct LLM 兜底。
- `Pi 教练等待模型`：尚未配置 LLM Provider。
- `实时教练已关闭`：会议准备中的主动建议策略关闭，或全局开关关闭。

没有生成建议卡时，界面仍展示本轮状态，例如：

```text
本轮结论：暂不打断，没有发现需要立刻介入的问题
本轮完成 6 项检查 · 检索历史 1 次 · 已延续会议上下文
```

这让“Pi 正在工作但选择静默”和“Pi 根本没有运行”能够被区分。

## 3. 每轮 Agent Loop

```mermaid
flowchart LR
    Transcript["新稳定转写"] --> Session["复用会议 Pi Session"]
    Session --> Checklist["review_coaching_checklist"]
    Checklist --> Decide{"当前信息足够吗"}
    Decide -->|否| Context["read_realtime_context"]
    Decide -->|需要较早证据| Search["search_prior_evidence"]
    Context --> Terminal{"是否应立即介入"}
    Search --> Terminal
    Decide -->|是| Terminal
    Terminal -->|是| Intervention["submit_intervention"]
    Terminal -->|否| Silent["keep_silent"]
```

一次评估最多 4 个 Agent turn、8 次工具调用。模型必须调用一个且只能调用一个终止工具，普通文本不会进入产品。

## 4. 教练 Checklist

| 检查项 | 对应事件 | 判断目标 |
| --- | --- | --- |
| 问题回应 | `question_to_user` | 对方的问题是否仍等待用户回答 |
| 承诺条件 | `commitment_risk` | 时间、范围、结果或责任是否缺少必要前提 |
| 目标覆盖 | `goal_at_risk` | 议题离开前，会议目标或关注点是否仍未覆盖 |
| 前后口径 | `contradiction` | 当前说法是否与较早事实、条件或立场冲突 |
| 表达清晰 | `communication_clarity` | 持续表达是否重复、失焦或迟迟没有结论，能否用一句话立即收束 |
| 介入价值 | 静默门槛 | 提示现在是否仍有用，能否避免具体损失 |

checklist 是工具边界，不只是 Prompt 文案。没有先调用 `review_coaching_checklist`，Pi 不能提交建议，也不能结束为静默。

## 5. 历史检索与证据边界

Pi 每轮只直接看到新片段，不自动把整场会议塞进 Prompt。需要旧信息时，它可以：

- 读取会议目标、rolling state 或当前语义窗口。
- 在最近最多 48 条较早转写中执行 `search_prior_evidence`。
- 使用检索命中的原始 segment ID 和逐字引用提交建议。

Python 和 Node 两侧都会再次校验证据 ID 与引用。不存在于模型可见转写中的人名、数字、期限或立场不能进入教练卡。

麦克风片段可用于判断问题是否已经回应、承诺是否仍开放，但不能单独证明说话者就是软件使用者。

## 6. 运行配置

本分支默认请求 Pi：

```powershell
$env:MEETING_COPILOT_REALTIME_COACH_RUNTIME = "pi"
```

该变量可以省略。需要临时回到旧实现时设置：

```powershell
$env:MEETING_COPILOT_REALTIME_COACH_RUNTIME = "direct"
```

完全关闭实时教练：

```powershell
$env:MEETING_COPILOT_REALTIME_COACH_ENABLED = "0"
```

仍需配置一个可用的 OpenAI-compatible LLM Provider。Pi 不提供模型，它负责 Session、工具调用和 Agent Loop。

## 7. 无外放验证

Pi sidecar 使用官方 faux Provider 跑固定转写，不访问麦克风或扬声器：

```powershell
cd code\agent_runtime\pi_coach_bridge
npm.cmd test
npm.cmd run smoke
```

同一批固定 transcript 的 direct/Pi 对照入口仍为：

```powershell
cd code\web_mvp\backend
uv run --frozen python ../../../tools/realtime_coach_eval/replay.py `
  ../../../tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl `
  --runtime both `
  --output ../../../artifacts/realtime-coach-eval.json
```

A/B 需要真实 Provider 配置，但不需要播放或外放声音。

### 真实 Provider 端到端结果

2026-08-17 使用 `gpt-5.6-sol`、`chat_completions` 和两条直接注入的稳定转写进行验收，未播放音频，也未打开麦克风：

1. 远端先声明“压测通过前不得承诺发布日期”，Pi 完成 checklist 后选择静默；`runtime_used=pi`，无回退，1 个 Agent turn、1 次 `keep_silent`，约 3.46 秒。
2. 麦克风轨随后出现“即使压测未通过也承诺周五上线”，同一 Pi Session 识别为 `commitment_risk` 并立即建议撤回无条件承诺；`runtime_used=pi`，无回退，1 个 Agent turn、1 次 `submit_intervention`，约 9.37 秒。
3. 第二轮 `session_reused=true`，建议同时引用两条逐字证据。两条证据已在当前有界语义窗口内，因此本例无需额外调用历史搜索工具；更早证据才走 `search_prior_evidence`。
4. Python 与 Node 均支持“每行一条”的多证据引用校验；任何一行不是所选证据的逐字子串都会拒绝，避免用改写内容冒充原话。

风险教练工作台验收会议为 `pi-coach-proof-grounded-20260817`。

### 真实持续表达故障与修复验收

用户真实录音 `rec_msxe9i9z_7f4797b5d69c` 最初约 155 秒、13 段稳定转写，左侧有文字但右侧没有建议。诊断确认有两个独立原因：

1. 转写精修会在 Pi 分析期间提升 evidence hash 或段落 revision，旧结果因此过期；原实现把它记为失败，却没有立即基于最新文字补分析。
2. 旧 checklist 只覆盖问题、承诺、目标、矛盾和介入价值。即使模型识别出长段独白，麦克风说话者身份的保守规则也会让 Pi 静默，无法形成私人表达教练价值。

修复后使用该录音第 2 至 8 段直接注入文本回放，没有播放音频，也没有打开麦克风：

- `runtime_used=pi`，无 fallback，完成 6 项 checklist。
- 事件类型为 `communication_clarity`，使用多处逐字证据，不推断麦克风说话者身份。
- 工作台会议 `pi-coach-real-speech-20260818` 的右侧实际展示“收束到核心结论”。
- 可直接说出的建议为：“我的核心观点是：直播时提供情绪价值，拍戏时把戏拍好，两件事都要专业。”
- 浏览器验收确认教练卡、原因、依据入口和 `Pi 教练监听中` 状态均可见。

### 教练与最近讨论历史验收

原实现虽然持久化了每轮 `meeting.intelligence.applied` 和 `meeting.topic.updated`，但快照和前端只投影最后一条，造成右侧内容不断覆盖。修复不增加数据库表，直接从正式事件生成两个有界信息流：

- 教练建议最多保留 12 条，连续重复建议合并；当前区只突出本轮有效介入，历史默认显示 3 条并可展开。
- 最近讨论最多保留 10 条，混合主题、结论和待确认问题；Ask AI 默认显示 5 条并可展开。
- 最新 Pi 轮次选择静默时，当前区展示静默原因，过去建议全部留在历史，不继续突出已经解决的旧建议。
- 每条历史保留时间、事件类型、正式模型来源和可定位的原文证据。

2026-08-18 使用 `pi-coach-history-silent-20260818` 直接注入 5 段稳定转写，未播放音频，也未打开麦克风：

1. 冗长且无结论的麦克风表达触发 `communication_clarity`。
2. 系统声音中的直接提问触发 `question_to_user`，证明电脑会议对方声音进入转写后可以驱动教练。
3. 无视安全测试和预算审批的上线承诺触发 `commitment_risk`。
4. 只修正上线条件但仍缺审批责任人，再产生一条有依据的追问建议。
5. 明确负责人、截止时间、同步方式和上线前提后，Pi 调用 `keep_silent`；当前主卡消失并展示“不需要打断”，此前 4 条建议仍可展开回看。

最终快照包含 4 条教练历史和 10 条最近讨论。浏览器实际检查了默认数量、展开交互、静默状态、证据入口和无重叠布局。自动化结果为后端 135 项通过、1 项跳过，前端 242 项通过，Pi bridge 10 项通过且 faux-provider smoke 成功，TypeScript、ESLint、Ruff 和生产构建均通过。

## 8. 当前边界

- 桌面安装包仍需把 Node `>=22.19.0` 和 Pi sidecar 一起打包；源码开发环境已经跑通，生产打包尚未完成。
- 历史检索目前是本地、只读、最多 48 条转写的轻量检索，不是整场向量索引。
- Agent Loop 只消费稳定转写，不处理 ASR partial，避免在半句话上误报。
- Pi 能改善跨轮状态、按需取证和静默决策，但不会自动提升底层模型本身的语义能力；真实增量仍应通过相同模型的 direct/Pi A/B 数据判断。

## 9. 主要代码位置

- Pi Session、checklist、工具循环：`code/agent_runtime/pi_coach_bridge/src/runtime.mjs`
- Python sidecar 与回退：`code/web_mvp/backend/meeting_copilot_web_mvp/pi_coach_runtime.py`
- 双音轨触发和教练路由：`code/web_mvp/backend/meeting_copilot_web_mvp/realtime_intelligence.py`
- 历史转写投影和运行状态：`code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- 用户可见教练状态与历史：`code/web_mvp/frontend_v2/src/features/live-meeting/NowRail.tsx`
- 最近讨论时间线：`code/web_mvp/frontend_v2/src/features/live-meeting/AiWorkspace.tsx`
