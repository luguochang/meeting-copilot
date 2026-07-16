# 真实麦克风 + 远程中转站主链路报告

日期：2026-07-14
测试地址：`http://127.0.0.1:8767/workbench`
会话：`rec_mrk6n8kk`
证据目录：`artifacts/tmp/browser_live_mic/real-remote-mainline-20260714-130017`

## 测试方式

- 使用可见 Chrome，真实 `getUserMedia` 麦克风输入，设备为 MacBook Air 内置麦克风。
- 通过 Mac 扬声器播放仓库内的中文技术会议音频 `code/asr_runtime/outputs/simulated-release-review.16k.wav`，模拟会议声源进入真实麦克风。
- ASR 使用本机 FunASR/Paraformer streaming，不调用远程 ASR。
- AI 修正、实时建议、方案分析和会议纪要使用已配置的 OpenAI-compatible 远程中转站；本次调用模型为 `gpt-5.5`。
- 测试脚本自动执行：开始会议 -> 麦克风采集 -> 实时文字/提醒/修正/建议 -> 结束会议 -> 录音导出 -> 整理会议 -> 文字稿/方案/纪要展示。

## 结果

| 能力 | 结果 | 证据 |
| --- | --- | --- |
| 真实麦克风采集 | 通过 | `health_status=audio_capture_health_passed`，`chunk_count=94`，RMS `0.0586` |
| 本地中文实时 ASR | 通过 | `provider=funasr_realtime`，`provider_mode=real`，`is_mock=false`，3 个 final |
| 实时文字展示 | 通过 | 录音中可见 10 个 partial、3 个确认段；首个文字延迟 `3367ms` |
| 实时 AI 建议 | 通过 | 录音中出现 1 张有 evidence 的建议卡，首卡延迟 `25136ms` |
| 实时 AI 修正 | 通过 | 录音中 `AI 已校正`，可见 1 段修正及“查看原始识别” |
| 录音保存 | 通过 | WAV HTTP `200`，`892972` bytes，导出 SHA 与会话 SHA 一致 |
| 会后方案分析 | 通过 | 3 张方案分析卡 |
| 会后会议纪要 | 通过 | 纪要 `421` 字符，页面 `已生成` |
| 浏览器错误 | 通过 | console errors `0`，network errors `0` |

本次会话最终状态为 `acceptance_eligible=true`、`acceptance_blockers=[]`、`degradation_reasons=[]`。中转站共记录 6 次 LLM 调用、3565 tokens；密钥未写入证据文件。

## 本轮修复

1. Workbench 顶部会话信息改为统计 canonical 已确认段落，不再把 partial/event 数量误报成“数千条实时文字”。
2. 收到 `asr_ready` 后立即把底部语音识别状态更新为“已就绪”。
3. 实时校正状态改为累计保存修正段 ID；后续“本批无需修改”不会覆盖已经成功的“AI 已校正”。
4. 快照恢复直接消费 `realtime_transcript_correction` 对象，避免把整个状态对象再包装成错误的 `status` 字段。
5. auto-suggestion provider failure 增加异常类型、调用耗时、完成时间和结构化 warning，便于区分超时、连接失败和响应解析错误。

## 仍然不能宣称的事项

- 这次声源是单台电脑扬声器播放的受控中文音频，不等于自然多人远场会议、串音、口音和多人抢话质量已通过。
- 首张实时建议约 25 秒，当前仍需优化为更适合会议现场的响应体验；不能把它描述成毫秒级。
- 本次验证了用户提供的远程 OpenAI-compatible 中转站可用，但没有验证长期额度、计费单价、服务 SLA 或网络波动下的稳定性。
- macOS 安装包签名、公证、Windows 实机和移动端发布仍是独立发布门禁。
