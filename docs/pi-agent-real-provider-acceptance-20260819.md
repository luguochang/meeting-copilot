# PI Agent 真实 Provider 验收记录（2026-08-19）

## 验收目标

在不改动左侧 ASR/会议文字链路的前提下，验证 PI 是否能把“普通 LLM 生成”变成有边界的实时私人教练：只在高价值时刻介入，给出下一句可执行话术，并且能回指原文证据、保留未闭环状态、显示调用成本。

本次使用 Chrome 打开的真实工作台页面，并使用本地 WAV 的无声 PCM 回放脚本进行尝试。回放脚本不会调用扬声器或麦克风设备：

`tools/silent_agent_acceptance.py`

## 页面验收结果

页面会议：`pi-coach-proof-grounded-20260817`

截图：`artifacts/tmp/pi-agent-agent-eval-20260819/final-coach.png`

页面左侧保留了两段原文：

1. “The release date must remain uncommitted until the load test has passed...”
2. “I promise we will launch this Friday even if the load test has not passed.”

页面右侧真实显示：

- 场景技能包：通用对话
- 5 项教练检查
- 1 轮 Agent
- 1 次工具调用
- 6250 tokens
- 响应约 9.4 秒
- 教练建议：`Clarify now that Friday is only a target and remains conditional on the load test passing.`
- 证据引用：同时引用发布日期条件和无条件承诺两段原文
- 未闭环问题：本周五承诺是否取代负载测试前不承诺发布日期的既有条件

这证明当前 PI 集成的可见价值是“发现矛盾后给出下一句”，而不是重复会议摘要。建议卡片有标题、原因、话术、证据入口和紧急程度，且不会覆盖左侧正文。

## 真实调用数据

同一会议的 realtime SLO 数据：

| Lane | Calls | Prompt | Completion | Total |
| --- | ---: | ---: | ---: | ---: |
| `realtime_coach` | 2 | 11,477 | 290 | 11,767 |
| `realtime_intelligence` | 2 | 10,781 | 940 | 11,721 |
| 合计 | 4 | 22,258 | 1,230 | 23,488 |

provider 没有返回价格，所以接口的 `estimated_cost_cny` 为 unavailable。成本应按实际 provider 价格计算：

`prompt_tokens / 1e6 * input_price + completion_tokens / 1e6 * output_price`

当前 UI 已把本轮 `total_tokens` 展示出来，后续可以在设置中增加预算上限和按场景统计。不能把 6250 tokens 当成每句固定成本；它是该轮会话的完整消耗，门控和上下文压缩会直接影响后续成本。

## 本轮代码改进

### 1. PI 只在高信号时刻运行

`should_run_realtime_coach()` 现在只对 PI lane 增加轻量门控，ASR 和普通 intelligence lane 不受影响。问句、承诺、上线/交付、负责人、截止、风险、决定、下一步等词会进入 coach；普通的短语音段落保持静默；较长的多段表达进入表达清晰度检查。

### 2. 缩小每轮上下文

- 后端只给 PI 最近 2 个上下文段落、12 个检索段落和 4 个语义窗口。
- Pi Session 最多保留 3 个 user turns。
- rolling state 只保留必要字段，open items 最多 6 条。
- 更老的证据通过 bounded `search_prior_evidence` 工具按需检索。

### 3. 继续保留 Agent 的控制能力

Pi loop 的边界仍然是：最多 2 轮 Agent、最多 4 次工具调用，只允许 `submit_intervention` 或 `keep_silent`，提交前必须通过证据校验。Pi 的优势是可控、可追踪、可解释，不是自动提高底层 LLM 的智力。

## 长音频无声回放结果与阻塞

本地音频：

`artifacts/tmp/pi-agent-eval-audio-5min.wav`

已尝试多个全新会议进行 120 秒回放。左侧 ASR 的基础链路没有被代码改动破坏，但本机实时环境先后出现以下基础设施问题：

- 默认 Uvicorn WebSocket ping timeout 为 20 秒，FunASR 本地计算期间客户端被 `1011 keepalive ping timeout` 断开。
- 离线精修 worker 在多次测试后反复启动失败，页面只能停留在“监听中”，无法提交最终段落，因此没有机会触发 PI。
- 发现并停止了遗留的 8765 测试服务及其 FunASR worker；8766 已重启为单实例，并将本地验收服务的 WebSocket ping 窗口临时调到 60/180 秒。
- 即使窗口调大，离线精修 worker 仍有启动失败日志（`asr_refiner.py` 的 worker stop 异常），所以这些回放不能作为 PI 质量的 A/B 结论。

因此，本轮长音频结果应记为“ASR 精修环境阻塞”，而不是“PI 没有识别到内容”。在该阻塞解除前，不应继续通过加大 Agent loop 来掩盖问题，也不应改动左侧 ASR 投影。

## 结论与下一步

当前建议保留 PI，但只作为受门控的 coach lane：

1. 先解决单实例 FunASR offline refiner 的稳定性，并以“最终段落持续产生、正文不丢失”为前置验收。
2. 用同一批最终段落做 direct LLM 与 PI A/B：比较高价值介入率、误报率、证据覆盖率、首字延迟、总 token。
3. 只有当 PI 在“矛盾承诺、未回答问题、决策缺口、执行闭环”四类场景中明显提高证据覆盖或话术可执行性时，才扩大技能包和 loop。
4. 对普通段落继续静默；把静默原因保留在诊断中，不在页面堆积无价值卡片。

当前已验证的业务亮点是：**在会议继续进行时，发现一个带证据的决策/承诺风险，并即时给出用户下一句可以说的话。** 这与会后总结和普通主题卡片是不同的产品能力。
