# 主线恢复报告（2026-07-13）

## 结论

本轮修复前，页面表现为“旧会议内容未清空、提醒重复、L2 等到停止后才出现、建议请求锁竞争后无补偿、历史会话持续显示语义降级”。这些问题叠加后看起来像所有接口都不可用。

本轮没有掩盖错误，也没有放宽事实保护。经过修复和一次新鲜浏览器主链路运行，受控中文技术会议音频已经完成：

```text
浏览器模拟麦克风
  -> FunASR realtime
  -> partial/final canonical transcript
  -> L2 realtime correction
  -> recording-time AI suggestion
  -> saved audio
  -> transcript/approach/minutes/history/export
```

## 已修复

1. 旧语义降级状态会在会话读取和列表读取时重新计算并迁移。
2. 真实 ASR ready 会恢复只由 ASR 失败造成的全局降级状态。
3. L2 final 批次从 240 字/30 秒调整为 80 字/15 秒，停止时仍强制清空。
4. 修正和建议改为前端单队列，防止同一 session lock 的 `in_flight` 竞态吞掉建议。
5. 新会议第一帧清空旧会议可见内容；旧会话只用于失败恢复。
6. 同一片段的 provisional/final reminder 在页面合并。
7. 建议请求等待期间显示明确的“正在分析这段已确认文字”。

## Fresh Evidence

证据目录：`artifacts/tmp/browser_live_mic/mainline-fix-final2-20260713/`

| 指标 | 结果 |
|---|---:|
| ASR provider | `funasr_realtime` |
| provider mode | `real` |
| first text after audio active | 5652 ms |
| first final after audio active | 12315 ms |
| first suggestion visible | 20143 ms |
| first correction visible | 20143 ms |
| final transcript segments | 5 |
| visible suggestion cards | 1 |
| visible UI cards after review | 3 |
| audio export | HTTP 200, SHA-256 matched |
| minutes | visible |
| browser console errors | 0 |
| network errors | 0 |
| mainline status | `passed_production_mainline` |

## 仍未宣称完成

- 这次输入是受控中文技术会议 WAV 的浏览器 fake microphone，不是自然多人麦克风。
- ASR 文本仍可能出现英文术语、数字和断句错误；L2 只在模型返回满足安全校验时替换。
- 远端 gateway 的约 20 秒首卡/首修正延迟仍需要产品层接受或继续做成本、模型和提示词优化。
- 长会议 soak、自然中文麦克风、Mac/Windows 安装包和发布合规仍未完成。

## 可追溯文件

- `docs/decision-log.md`：DEC-353 至 DEC-355。
- `docs/current-mainline-index.md`：2026-07-13 fresh evidence。
- `code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs`：主链路自动化验证器。
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js`：页面串行 AI、canonical transcript 和新会议隔离。
