# 真实麦克风权限重试记录

日期：2026-07-13
工作台：`http://localhost:8767/workbench`
系统：macOS，MacBook Air 内置麦克风

## 结论

本次真实麦克风权限与录音主链路已成功触发并跑通：

- `http://127.0.0.1:8767/workbench` 仍返回浏览器缓存的 `NotAllowedError`。
- macOS 麦克风权限列表中 `ChatGPT`、`Google Chrome` 均为开启状态。
- 将同一服务切换到 `http://localhost:8767/workbench` 后，浏览器重新请求并获得麦克风权限。
- 页面进入“录音中”，设备显示为 `MacBook Air 麦克风 (Built-in)`，输入状态为“输入正常”。
- 录音结束后，服务端使用真实的 `funasr_realtime` 完成了 1 个 partial 和 1 个 final。
- 录音保存成功，页面历史记录中出现本次会话。

## 会话证据

```text
session_id: rec_mrjbrzku
provider: funasr_realtime
provider_mode: real
is_mock: false
recording_duration: 03:21
partial_count: 1
final_count: 1
recognized_text: 六六六
audio_saved: true
ai_suggestions: 0
```

服务端事件中确认：

```text
transcript_partial -> normalized_text=六六六, confidence=0.8
transcript_final   -> normalized_text=六六六, confidence=0.9
evaluation_summary -> is_mock=false, provider=funasr_realtime,
                      passes_minimum_gate=true, error_event_count=0
```

## 结果解释

这次录音接收到的是当前环境中的单一短语“六六六”，不是清晰的中文技术会议内容。因此本次可以确认：

- 权限链路可用。
- 浏览器录音和音频上传可用。
- 本地中文 ASR 真实流可用。
- partial/final 事件可以进入页面并在会后保存。

本次不能确认：

- 多人中文会议的识别准确率。
- 技术术语、数字和中英文混杂场景的质量。
- 有足够上下文时的实时 AI 建议和实时大模型修正。
- 长会议下的持续稳定性。

## 下一次真实验收入口

使用下面的地址，避免当前 `127.0.0.1` 来源的拒绝状态：

```text
http://localhost:8767/workbench
```

下一次应使用一段清晰的中文技术会议音频或自然中文发言，验证完整顺序：

```text
开始会议 -> 麦克风输入正常 -> partial/final 实时文字
-> 实时提醒/AI 建议 -> 结束会议 -> 录音保存
-> 完整文字稿 -> 会后整理/会议纪要
```
