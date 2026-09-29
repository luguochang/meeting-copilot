# Pi Agent 分支交付说明

本文面向代码评审，说明 Pi SDK 在 Meeting Copilot 中承担的职责、代码边界、关键约束、验证方式和当前已知缺口。阶段性实验日志和本机录音证据不属于源码交付物；可复现的验收逻辑以测试和 `tools/` 下的脚本为准。

## 1. 交付状态

当前分支已经打通真实 Provider 下的 Pi Agent harness/loop，并把它从“首答生成器”收敛为 Fast Answer 之后的增量教练：宿主先识别完整问题并流式生成可直接说的回答，Pi 再通过受限工具读取当前 Answer 和多段会议证据，以 `submit_intervention` 或 `keep_silent` 结束一轮判断。Provider 调用、工具调用、证据、时延、降级原因和建议生命周期均进入持久化事件，可由快照和诊断接口复核。

此前“标题闪动但正文无价值”的两个 P0 已处理：Fast Answer 与 Pi Deep 使用独立 durable lane，失败/静默不再清除有效回答或上一张卡；Provider 费率未知不再错误禁用 correction，Answer/Pi/correction 的 reservation 和公平调度也已分开。打开 MacBook 上盖后的五分钟真实硬件复验已经取得 microphone 与 system audio 两条物理轨的非零 PCM、独立 final、Fast Answer、Pi Deep 和会后派生结果，合盖阻塞项已关闭。

当前仍不宣称桌面端达到完整 Go 标准。最新六个成功 Answer 的 final-to-first-token P95 为约 `2.95s`，高于目标 `2.5s`；Pi SLO 采集还缺完整的 Provider/投影阶段时钟；Pi 相对只使用 Fast Answer 的增量价值仍缺至少两名独立标注者盲评。也就是说，物理双轨与功能主链已经通过，剩余问题是性能、可观测性和产品增量价值证明，不再是麦克风权限或 Pi SDK 未调用。

## 2. 运行链路

```text
麦克风 / 系统音频
        |
        v
实时 ASR partial -> 端点切分 -> 本地离线精修 -> 权威 final/revision
                                             |
             +-------------------------------+------------------------------+
             |                               |                              |
             v                               v                              v
      后台 transcript correction     Fast Answer durable lane      普通 Pi candidate 门控
                                             |
                                  流式可说回答 + Answer ID
                                             |
                                             v
                                   Pi bridge session/harness
                                             |
                          当前 Answer + 多段宿主证据 + 场景 checklist
                                             |
                                  submit_intervention / keep_silent
                                             |
                         事实落地、证据/结构/时限校验、生命周期屏障
                                             |
                                      事件落库和前端投影
```

Fast Answer 负责“现在怎么回答”，不等待 Pi；`answer_ready` 只创建一个绑定当前 Answer 的 Deep 任务，Pi 负责“还缺什么边界、风险和下一问”。普通 candidate 在同一证据已有 Answer 时可审计静默，不与首答争抢 Provider。`not_triggered` 表示宿主没有调用 Pi；`protected_silent` 且 `origin=pi` 才表示 Pi 已运行并主动保持静默。二者不能在指标或 UI 上混为一类。

## 3. 代码边界

| 模块 | 职责 | 不负责 |
| --- | --- | --- |
| `code/agent_runtime/pi_coach_bridge/` | Pi SDK 会话、场景 checklist、受限工具循环、终止工具和响应规范化 | 音频采集、业务持久化、前端投影 |
| `realtime_answer_copilot.py` / `v2_streaming_suggestions.py` | 问题检测、Answer Prompt、流式草稿、事实落地和 commit 屏障 | Pi session、音频采集 |
| `realtime_intelligence.py` | 候选事件、场景规则、本地安全提示、证据和输出校验 | 进程管理、数据库事务 |
| `pi_coach_runtime.py` | Python 与 Node bridge 的生命周期、请求关联、超时和宿主工具分发 | 决定建议是否有业务价值 |
| `pi_evidence_registry.py` | 将一次 Pi 请求绑定到对应会议、任务和证据读取通道 | 长期保存会议数据 |
| `realtime_provider_circuit.py` | Provider 熔断、半开恢复探测、跨进程持久化状态 | 替代 Provider 自身限流策略 |
| `llm_lane_locks.py` | realtime/deep/correction 调用的优先级和 reservation 协调 | 业务候选判断 |
| `v2_persistence.py` | durable job、事件、生命周期、证据屏障和并发事务 | 调用模型 |
| `pipeline_trace.py` / `realtime_slo.py` | 端到端阶段时钟、失败归因和 SLO 样本 | 改写业务输出 |
| `asr_stream.py` / `funasr_resident.py` | 实时切分、resident worker、final/revision 和资源收敛 | 教练决策 |
| `frontend_v2/src/domain/` | 事件解析、来源和生命周期状态投影 | 推断后端没有提供的建议 |
| `NowRail.tsx` | 当前建议、静默/失败状态、来源、证据和历史建议展示 | 修改 durable 决策 |
| `tools/realtime_coach_eval/` | 同输入回放、评分、盲评资格和人工价值门禁 | 代替真实设备和真人体验验收 |

`app.py` 仍是既有 FastAPI composition root，负责组装上述模块和 HTTP/WebSocket 路由。新的状态机和策略优先落在独立模块中；后续若继续拆分，应按 Provider admission、job handler 和 projection 三个应用服务边界迁移，避免只为缩短文件而移动代码。

## 4. 关键不变量

1. **证据先于建议**：建议引用的 segment、quote、数字、负责人、期限、产品名和完成状态必须能在当前会议证据或已提交 Answer 中验证；无依据硬事实不能跨过 commit 屏障。
2. **只有终止工具能形成结果**：普通 assistant 文本不能直接成为产品建议；一轮必须以 `submit_intervention` 或 `keep_silent` 结束。
3. **失败不算正确静默**：超时、Provider 错误、结构校验失败和 Agent 主动静默分别计数。
4. **迟到结果不可覆盖新状态**：结果超过实时有效窗口或证据 revision 已变化时，不进入当前卡片。
5. **来源必须可审计**：`origin`、`runtime_requested`、`runtime_used`、`pi_provider_attempted`、工具名和 Provider 尝试次数必须一致。
6. **调用要有绝对时限**：队列等待、Pi loop、Provider 和投影共享同一轮 deadline，不能每一层重新获得完整超时。
7. **建议生命周期可追溯**：替换、撤回和解决通过 decision id 建链，历史记录保留，结束态 UI 与验收审计快照分离。
8. **密钥只存在于运行配置**：API key 通过系统凭据存储或环境配置注入，日志、事件、快照和测试产物不保存明文。
9. **ASR 是独立事实层**：Answer 和 Pi 只读 canonical transcript，不得改写 raw ASR、切段、离线精修或 revision 生命周期；某一静音轨的 `asr_no_final` 不能否定另一轨已持久化的有效 final。

## 5. Provider 与降级策略

实时教练优先使用显式 `realtime_model`。Pi 请求获得 realtime/deep reservation 后，普通语义和 correction lane 不得抢占同一受限 Provider 容量。连续超时、限流或服务错误会打开持久化 circuit；恢复探测成功后才重新允许实时请求。

本地提示只覆盖少量可由原文确定的高置信模式，例如明确缺少负责人、下一步或回应义务。它必须标记 `origin=local_reflex`，不能伪装成 Pi 输出。无法形成可靠建议时保持静默，并在诊断面板保留失败原因。

## 6. 验证入口

```bash
# Pi bridge：工具循环、会话复用、总时限和响应校验
cd code/agent_runtime/pi_coach_bridge
npm test

# 后端：业务合同、持久化、并发、故障和 Provider 边界
cd ../../web_mvp/backend
uv run --frozen ruff check meeting_copilot_web_mvp tests
uv run --frozen pytest -q

# 前端：事件投影、来源、生命周期、权限状态和构建
cd ../frontend_v2
npm run lint
npm run typecheck
npm test
npm run build

# 桌面端：配置、采集协调和打包入口
cd ../../desktop_tauri/src-tauri
cargo fmt --check
cargo check --locked
```

生产链路的 controlled replay、三次 canary、故障注入、资源门和 paired blind 入口位于 `tools/pi_stage0_*.py`、`tools/pi_provider_lifecycle_probe.py` 与 `tools/realtime_coach_eval/`。这些工具默认 fail closed：缺证据、超时、fallback、运行时替换或样本不完整都不能被计为通过。

### 当前提交验证基线（2026-09-29）

| 范围 | 结果 |
| --- | --- |
| Python 静态检查（本次改动文件） | Ruff 和 `py_compile` 通过 |
| 后端全量测试 | `1799 passed, 1 skipped` |
| Pi bridge | `50 passed` |
| 前端 | ESLint 通过，`329 passed`，TypeScript/Vite build 通过 |
| 真实 Provider | Fast Answer `1/1`；Pi `deep_answer`、单轮工具调用和多段证据绑定成功 |
| 真实物理双轨 | `accept_real_dual_open_lid_pi_20260929_05`：microphone `9` 条 final、system audio `12` 条 final；`138` 个录音块、`685232ms` 双轨总时长、录音状态 `saved` |
| Fast Answer | 触发 `7` 次，成功 `6`、新证据覆盖取消 `1`、失败 `0`；TTFT `P50 1.74s / P95 2.93s / Max 3.32s`，完成 `P50 3.08s / P95 4.44s / Max 4.80s` |
| Pi Deep | `6/6` 为 `runtime_used=pi` + `deep_answer` + 单轮 `submit_intervention`，工具错误/validation/fallback 均为 `0`；TTFT `P50 2.54s / P95 2.89s`，投影 `P50 4.77s / P95 5.29s` |
| Correction / 会后任务 | `21` 条 final 中 `5 changed + 16 no_change + 0 error`；minutes、approach、index 均成功，生成 `3` 张 approach cards |
| Git 差异 | `git diff --check` 与凭据扫描通过，不包含本地录音或 `artifacts/` |

后端唯一跳过项是显式环境集成测试，不在普通源码运行环境伪造通过。Vite 仍提示主包超过 500 kB，这是已记录的性能优化项，不影响本次构建正确性。

## 7. 当前验收 Checklist

- [x] Pi SDK 使用固定版本并由独立 Node bridge 承载。
- [x] harness 支持受限宿主工具、场景 checklist 和多轮 loop。
- [x] Pi intervention 与 deliberate silence 有不同终止状态。
- [x] Provider 尝试、工具调用、证据、deadline 和失败类型可追踪。
- [x] durable job、跨进程 reservation、circuit 和结束态 settlement 有自动化覆盖。
- [x] 本地 FunASR resident refinement 与修正任务有独立状态。
- [x] Web 与 Tauri Provider 配置支持 realtime/correction 模型来源，并避免回传密钥。
- [x] 评测支持同输入 replay、故障注入、资源检查和双人盲评合同。
- [x] 有效 Pi 卡片不会被后续 `not_triggered`/`protected_silent` 空状态清除。
- [x] 显式测试 Provider 下 realtime/deep/correction 使用独立 reservation；未知费率不再错误禁用 correction。
- [x] 真实 system audio 会议达到 Fast Answer 首字、完成时延和 ASR 可读性门槛。
- [x] 打开 MacBook 上盖后确认 `AppleClamshellState = No`；3 秒 AVAudioEngine probe 为 `audible`，并完成 microphone/system audio 双物理轨约五分钟稳定性验收。
- [ ] 将 Fast Answer final-to-first-token P95 从本轮约 `2.95s` 降到 `<= 2.5s`，并用不少于 30 个有效问题复验，避免以六个样本下结论。
- [ ] 补齐 Pi Deep 的 Provider connected、projection 和 UI render 阶段时钟，使 `/realtime-ai-slo` 不再因 trace incomplete 返回 `insufficient_data`。
- [ ] 至少两名独立标注者证明 Pi 相对 direct/local 具有增量价值。

## 8. 建议评审顺序

1. `code/agent_runtime/pi_coach_bridge/src/runtime.mjs`：Agent 能力、工具和终止合同。
2. `code/web_mvp/backend/meeting_copilot_web_mvp/realtime_intelligence.py`：候选、证据和安全校验。
3. `code/web_mvp/backend/meeting_copilot_web_mvp/pi_coach_runtime.py`：跨进程运行边界。
4. `code/web_mvp/backend/meeting_copilot_web_mvp/v2_persistence.py`：durable 状态和并发屏障。
5. `code/web_mvp/frontend_v2/src/domain/reducer.ts` 与 `NowRail.tsx`：用户可见投影。
6. 对应测试和 `tools/realtime_coach_eval/README.md`：失败口径与产品价值门禁。

评审时应分别判断三件事：SDK 是否真实运行、工程状态是否可追踪、建议是否对用户有增量价值。SDK 与主链已有真实 Provider 和物理双轨证据；性能与阶段时钟仍需按 checklist 收口；产品增量价值仍需盲评，不能用测试数量替代。
